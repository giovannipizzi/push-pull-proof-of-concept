# A mini proof-of-concept of the push/pull mechanism (e.g. for AiiDA)

This small repository provides a proof of concept of how a push and pull mechanism could work.

Author: Giovanni Pizzi, EPFL

## The app
The app is a very simple system to log immutable messages. It will store them in a SQLite DB in the current folder.

A message will have (beside a integer ID, never really used), a UUID, a text body, and a timestamp.

You can add a new message with `./app.py add MESSAGE_BODY` and list all messages with `./app.py list`. If you want to delete all messages, use `./app.py dropdb` (or just delete the database file).

In addition, it provides two push and pull commands to send all messages and synchronize them with a remote instance (via SSH, using paramiko).

## Design
The design is very similar to git. You create a remote account (here called `server`) where you have the app that serves just the role of a shared repository. When you connect to the `server` UNIX account via SSH, access is granted *only* via SSH keys, and the SSH key should be configured as discussed below, so that it is bound to run a command only, and it should be bound to a given user name (the username is something the push-pull application is aware of, for authentication).

This is achieved by prepending each key in the `.ssh/authorized_keys` of the `server` user with something like this:

```
command="/usr/bin/python3.8 /home/server/app.py pushpullserver user1",no-port-forwarding,no-x11-forwarding,no-agent-forwarding ssh-rsa ...
```
where the `pushpullcommand` is implemented in the app and takes care of 'replying' to requests, and `user1` is the hardcoded username for that specific key.
You want to have one row per key (possible multiple keys for the same user, e.g. if the user wants to connect both from the laptop and from the workstation).

**NOTE**: the use of SSH to achieve this push/pull is intended; it means we don't need to worry about authentication (done by SSH with all its security aspects in place), and we will need to think only to authorization in the app (could be something as simple as none/read/write (and possibly admin), like on GitHub).

It is now the role of the `pushpullserver` command to authorize the user, and communicate (on the other end there will be either the `push` or `pull` command) to transfer the data.

At this point, the `push` (or `pull`) command will communicate with the `pushpullserver` sending lines of ASCII text (separated by `\n`) over stdin/stdout to communicate.

In particular, in the current version, `push` will just send *all* messages to the server (suboptimal but OK for PoC purposes); the server will then check if there are new messages (based on the UUIDs) and add those to its DB. The `pull` command will instead, when requesting to pull, first send the full list of its own message UUIDs, and the server will just send back the missing ones, if any (so the pull already has some basic delta transfer mechanism, even if without any check for consistency etc.). 

Over SSH, commands are identified by a string, possibly (not all commands) by a size, and finally (when needed) by a string body content, typically seralized as a single-line JSON.

## How to test

### Start the docker container
A simple Dockerfile (needs improvement, see also the last section) is provided.
You can build the image e.g. with

```
docker build . -t test-push-pull
```
and then run it with
```
docker run --name=test-push-pull -d test-push-pull
```

**Note**: to stop the image, you need to run in a terminal `docker kill test-push-pull` followed by `docker rm test-push-pull` (this is required even if you don't start in daemon mode [`-d`], since I'm just starting sshd as the docker command without a proper init script, and this does not stop when CTRL+C is pressed).

### Test the commands
There are 3 users in the docker container: `user1`, `user2`, `user3` (plus a `server` user, that is already configured with the authorized keys of the three users, and the code; you should never try to login into this user, but only use it as the remote for the push and pull commands, see commands below).

The `app.py` code hardcodes authorization for `user1` and `user2` and *not* `user3` (in a real app: this would be configurable of course).

So the first test you can do is to connect as `user3` and check that you cannot push/pull:

```
docker exec -u user3 -it test-push-pull bash
```
And then in the container:
```
$ cd # to go to the home
$ ./app.py push server@localhost
[...]
ERROR starting connection, got 'NOT_AUTHORIZED
'

$ ./app.py pull server@localhost
[...]
ERROR starting connection, got 'NOT_AUTHORIZED
'
```

Let us now connect in two terminals as `user1` and `user2` and test the pushing and pulling.

I show here the commands for `user1`, just do the same for `user2` in a different terminal.

```
docker exec -u user1 -it test-push-pull bash
```
And then in the container:
```
$ cd # to go to the home
$ ./app.py add 'msg from user 1'
$ ./app.py list
* [821b22c9aafc438cb29de27c73fdf433 - 2021-10-01 12:16:07.588528] msg from user 1


$ ./app.py push server@localhost
[...]
Reply correct! 1 new pushed.
Connection closed.

# If you want to pull, run instead:
$ ./app.py pull server@localhost
[...]
0 new messages pulled and stored locally.
Connection closed.
```

You want now to push from one user and pull into the other and vice versa, and then check with `./app.py list` that you have all messages in both users.



## A word of caution - this is only a proof of concept! (PoC)
NOTE: the code is written very quickly, just to show the concept.
*Many* things need to be improved in a production code.

A non-exhaustive list:

- use a proper library to communicate messages over SSH (is going via stdin/out the right approach? Can we use existing libraries to send message? In any case, features that are missing include proper checks for no-buffering, no hanging when connecting via ssh to a standard bash shell rather than an account where the SSH authorized_keys enforces the executable, checking for valid messages - both at the protocol level [do I understand this command, it has the right syntax], and then at the application level [does the data that I received makes sense], and validating that there is no error in the communication, ...)
- adding efficient delta-checks to transfer only valid data in push and pull (nothing currently in push, some basic delta in pull)
- improve *a lot* the code style
- for testing, use a proper docker image with an init system that starts SSH rather than starting it as the CMD
- tools to customize automatically the .ssh/authorized keys (e.g. to add or remove keys and users). This would also validate that each row is properly written, e.g. to avoid can directly SSH via bash into the `server` user, that would be a security issue because then you could access and delete data for all users.
- a proper authorization system for various users (not only hardcoded)
- and much more...

