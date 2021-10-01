#!/usr/bin/env python
import datetime
import json
import os
#import subprocess
import sys
import uuid
from typing import Optional

import paramiko

from sqlalchemy import Column, Integer, String, create_engine, event, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import text

import click

Base = declarative_base()  # pylint: disable=invalid-name,useless-suppression

# class LocalExecuteEngine:
#     def __init__(self):
#         self._connection = None
    
#     def open(self):
#         # When running locally, you have all read/write rights so you can pretend to be
#         # any user. But when running via SSH, the user will be written
#         user = 'testuser'
#         # Open an unbuffered connection
#         self._connection = subprocess.Popen([sys.argv[0], 'pushpullserver', user], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
#         helo = self._connection.stdout.readline()
#         if helo != b"OK\n":
#             click.echo(f"ERROR starting connection, got '{helo.decode('ascii')}'")
#             sys.exit(1)


#     def close(self):
#         if self._connection is None:
#             return
#         self._connection.stdin.write(b"END\n")
#         # Here I should actually wait and kill it only if it didn't end (but in a non blocking way maybe?)
#         self._connection.kill()
#         self._connection = None
    
#     @property
#     def is_open(self):
#         return self._connection is not None

#     def send(self, cmd, body):
#         if not self.is_open:
#             raise ValueError("Connection closed")
        
#         assert b'\n' not in cmd
#         assert b'\n' not in body

#         self._connection.stdin.write(cmd + b'\n')
#         self._connection.stdin.write(str(len(body)).encode('ascii') + b'\n')
#         self._connection.stdin.write(body + b'\n')

#     def rcv(self):
#         if not self.is_open:
#             raise ValueError("Connection closed")

#         return self._connection.stdout.readline()
    

class ExecuteEngine:
    def __init__(self, remote):
        # In the future the remote should be in the git format user@machine:path
        # I ignore the path for simplicity in this PoC
        self._remoteuser, _, self._machine = remote.partition('@')
        self._connection = None
        self._channel = None
    
    def open(self):
        # Open an unbuffered connection
        self._connection = paramiko.SSHClient()
        # Not secure, but just to ease testing, RejectPolicy + load_system_host_keys() should be used
        # in production
        self._connection.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print("Trying to connect...")
        self._connection.connect(self._machine, username=self._remoteuser)
        print("Getting a session...")
        self._channel = self._connection.get_transport().open_session()
        #self._channel.exec_command(b'START\n')
        self._channel.invoke_shell()
        print("Creating channels...")
        self._stdin = self._channel.makefile('wb')
        self._stdout = self._channel.makefile('rb')

        print("Checking ack OK message...")
        helo = self._stdout.readline()
        if helo != b"OK\n":
            click.echo(f"ERROR starting connection, got '{helo.decode('ascii')}'")
            sys.exit(1)

    def close(self):
        if self._channel is None:
            return

        self._stdin.write(b"END\n")
        self._stdin.flush()
        self._stdin.channel.shutdown_write()

        self._connection.close()
        self._connection = None
        self._channel = None
    
    @property
    def is_open(self):
        return self._connection is not None

    def send(self, cmd, body):
        if not self.is_open:
            raise ValueError("Connection closed")
        
        assert b'\n' not in cmd
        assert b'\n' not in body

        self._stdin.write(cmd + b'\n')
        self._stdin.flush()
        self._stdin.write(str(len(body)).encode('ascii') + b'\n')
        self._stdin.flush()
        self._stdin.write(body + b'\n')
        self._stdin.flush()

    def rcv(self):
        if not self.is_open:
            raise ValueError("Connection closed")

        return self._stdout.readline()
    



class Msg(Base):
    """The main (and only) table to store messages."""

    __tablename__ = "db_msg"

    id = Column(Integer, primary_key=True)
    uuid = Column(String, nullable=False, unique=True, index=True)
    message = Column(String)
    time = Column(DateTime)


def get_session(
    path: str, create: bool = True, raise_if_missing: bool = False
) -> Optional[Session]:
    if not create and not os.path.exists(path):
        if raise_if_missing:
            raise FileNotFoundError("Pack index does not exist")
        return None

    engine = create_engine(f"sqlite:///{path}", future=True)

    # For the next two bindings, see background on
    # https://docs.sqlalchemy.org/en/13/dialects/sqlite.html#serializable-isolation-savepoints-transactional-ddl
    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, _):
        """Hook function that is called upon connection.
        It modifies the default behavior of SQLite to use WAL and to
        go back to the 'default' isolation level mode.
        """
        # disable pysqlite's emitting of the BEGIN statement entirely.
        # also stops it from emitting COMMIT before any DDL.
        dbapi_connection.isolation_level = None
        # Open the file in WAL mode (see e.g. https://stackoverflow.com/questions/9671490)
        # This allows to have as many readers as one wants, and a concurrent writer (up to one)
        # Note that this writes on a journal, on a different packs.idx-wal,
        # and also creates a packs.idx-shm file.
        # Note also that when the session is created, you will keep reading from the same version,
        # so you need to close and reload the session to see the newly written data.
        # Docs on WAL: https://www.sqlite.org/wal.html
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=wal;")
        cursor.close()

    # For this binding, see background on
    # https://docs.sqlalchemy.org/en/13/dialects/sqlite.html#serializable-isolation-savepoints-transactional-ddl
    @event.listens_for(engine, "begin")
    def do_begin(conn):  # pylint: disable=unused-variable
        # emit our own BEGIN
        conn.execute(text("BEGIN"))

    if create:
        # Create all tables in the engine. This is equivalent to "Create Table"
        # statements in raw SQL.
        Base.metadata.create_all(engine)

    # Bind the engine to the metadata of the Base class so that the
    # declaratives can be accessed through a DBSession instance
    Base.metadata.bind = engine

    # We set autoflush = False to avoid to lock the DB if just doing queries/reads
    session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )()

    return session


PATH = os.path.expanduser('app.db')

@click.group()
def cli():
    pass

@cli.command()
def dropdb():
    if os.path.exists(PATH):
        os.remove(PATH)

@cli.command()
@click.argument('content')
def add(content):
    session = get_session(PATH)
    msg = Msg(uuid=uuid.uuid4().hex, time=datetime.datetime.now(), message=content)
    session.add(msg)
    session.commit()


@cli.command()
def list():
    session = get_session(PATH)
    for msg in session.query(Msg).order_by(Msg.time):
        click.echo(f"* [{msg.uuid} - {msg.time}] {msg.message}")


@cli.command()
@click.argument('dest')
def push(dest):
    session = get_session(PATH)
    messages = {_[0]: {'time': _[1].isoformat(), 'message': _[2]} for _ in session.query(Msg.uuid, Msg.time, Msg.message).order_by(Msg.uuid).all()}

    # engine = LocalExecuteEngine() if dest == 'localhost' else ExecuteEngine(dest)
    engine = ExecuteEngine(dest)
    engine.open()
    click.echo("Push session open...")
    engine.send(cmd=b'my_messages', body=json.dumps(messages).encode('ascii'))
    click.echo("My data sent...")
    reply = engine.rcv()
    click.echo("Reply received...")
    assert reply.startswith(b'ACK-'), f'error: {reply}'
    num_new = int(reply.partition(b'-')[2])
    click.echo(f"Reply correct! {num_new} new pushed.")
    engine.close()
    click.echo("Connection closed.")

@cli.command()
@click.argument('src')
def pull(src):
    click.echo("Getting local DB session...")
    session = get_session(PATH)
    # engine = LocalExecuteEngine() if dest == 'localhost' else ExecuteEngine(dest)
    click.echo("Creating engine...")
    engine = ExecuteEngine(src)
    click.echo("Connecting...")
    engine.open()
    click.echo("Pull session open...")
    uuids = [_[0] for _ in session.query(Msg.uuid).order_by(Msg.uuid).all()]
    engine.send(cmd=b'pull_missing_messages', body=json.dumps(uuids).encode('ascii'))
    click.echo("My UUIDs sent...")
    reply = engine.rcv()
    click.echo("Reply received...")
    assert reply == b'ACK\n', f'error: {reply}'
    # TODO: all error checking
    size = int(engine.rcv())
    print(f"Expecting to receive {size} bytes...")
    # Will need to be received in chunks, e.g. ideally one per line
    reply = engine.rcv()
    if reply.endswith(b'\n'):
        reply = reply[:-1]
    assert len(reply) == size, f"Wrong size: {len(reply)} vs {size}"
    reply = json.loads(reply)
    click.echo("Data received...")

    for message_uuid, payload in reply.items():
        session.add(Msg(uuid=message_uuid, time=datetime.datetime.fromisoformat(payload['time']), message=payload['message']))
    session.commit()

    click.echo(f"{len(reply)} new messages pulled and stored locally.")
    engine.close()
    click.echo("Connection closed.")


@cli.command()
@click.argument('username')
def pushpullserver(username):
    # For this PoC the allowed users are hardcoded - in reality 
    # they will be read from some appropriate configuration
    # (and one could think to a basic permission system:
    # none - readonly - readwrite (and possibly 'admin'))
    allowed_users = ['user1', 'user2']
    if username not in allowed_users:
        sys.stdout.buffer.write(b"NOT_AUTHORIZED\n")
        sys.stdout.buffer.flush()
        return
    sys.stdout.buffer.write(b"OK\n")
    sys.stdout.buffer.flush()

    session = get_session(PATH)

    while True:
        command = sys.stdin.buffer.readline()
        if command == b"END\n":
            break
        if command == b"START\n":
            pass
        elif command == b'my_messages\n':
            size = sys.stdin.buffer.readline()
            body = sys.stdin.buffer.readline()
            if body.endswith(b'\n'):
                body = body[:-1]
            try:
                size = int(size)
            except ValueError:
                sys.stdout.buffer.write(b"INVALID_SIZE_VALUE\n")
                sys.stdout.buffer.flush()
                return
            if len(body) != size:
                sys.stdout.buffer.write(f"INVALID_BODY_SIZE-{len(body)}-{size}\n".encode('ascii'))
                sys.stdout.buffer.flush()
                return
            try:
                body = json.loads(body)
            except ValueError:
                sys.stdout.buffer.write(b"INVALID_BODY_CONTENT\n")
                sys.stdout.buffer.flush()
                return
            # TODO: one would implement first an efficient check of which nodes are missing and
            # send only the delta.
            # TODO: Some validation of the received content...
            current_uuids = set(_[0] for _ in session.query(Msg.uuid).all())
            new_body_uuids = set(body.keys()).difference(current_uuids)

            for message_uuid in new_body_uuids:
                payload = body[message_uuid]
                session.add(Msg(uuid=message_uuid, time=datetime.datetime.fromisoformat(payload['time']), message=payload['message']))
            session.commit()

            sys.stdout.buffer.write(f"ACK-{len(new_body_uuids)}\n".encode('ascii'))
            sys.stdout.buffer.flush()
        elif command == b'pull_missing_messages\n':
            size = sys.stdin.buffer.readline()
            body = sys.stdin.buffer.readline()
            if body.endswith(b'\n'):
                body = body[:-1]
            try:
                size = int(size)
            except ValueError:
                sys.stdout.buffer.write(b"INVALID_SIZE_VALUE\n")
                sys.stdout.buffer.flush()
                return
            if len(body) != size:
                sys.stdout.buffer.write(f"INVALID_BODY_SIZE-{len(body)}-{size}\n".encode('ascii'))
                sys.stdout.buffer.flush()
                return
            try:
                body = json.loads(body)
            except ValueError:
                sys.stdout.buffer.write(b"INVALID_BODY_CONTENT\n")
                sys.stdout.buffer.flush()
                return
            # TODO: Some validation of the received content...
            current_uuids = set(_[0] for _ in session.query(Msg.uuid).all())
            has_already_uuids = set(body)
            to_send_uuids = current_uuids.difference(has_already_uuids)

            messages = {_[0]: {'time': _[1].isoformat(), 'message': _[2]} for _ in session.query(Msg.uuid, Msg.time, Msg.message).filter(Msg.uuid.in_(to_send_uuids)).order_by(Msg.uuid).all()}
            messages = json.dumps(messages)

            sys.stdout.buffer.write(b"ACK\n")
            sys.stdout.buffer.flush()

            sys.stdout.buffer.write(f"{len(messages)}\n".encode('ascii'))
            sys.stdout.buffer.flush()

            sys.stdout.buffer.write(f"{messages}\n".encode('ascii'))
            sys.stdout.buffer.flush()
        else:
            sys.stdout.buffer.write(b"UNKNOWN_CMD\n")
            sys.stdout.buffer.flush()
            return

if __name__ == "__main__":
    cli()