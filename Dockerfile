FROM ubuntu:20.04

RUN apt-get update && apt-get install -y openssh-server python3.8
RUN apt-get install -y python3-pip
RUN ln -s /usr/bin/python3.8 /usr/bin/python
RUN pip install paramiko sqlalchemy click
RUN mkdir /var/run/sshd

RUN adduser user1
COPY app.py /home/user1/app.py
RUN mkdir /home/user1/.ssh
COPY key1 /home/user1/.ssh/id_rsa
COPY key1.pub /home/user1/.ssh/id_rsa.pub
RUN chown -R user1:user1 /home/user1 && chmod go= /home/user1/.ssh/ && chmod +x /home/user1/app.py

RUN adduser user2
COPY app.py /home/user2/app.py
RUN mkdir /home/user2/.ssh
COPY key2 /home/user2/.ssh/id_rsa
COPY key2.pub /home/user2/.ssh/id_rsa.pub
RUN chown -R user2:user2 /home/user2 && chmod go= /home/user2/.ssh/ && chmod +x /home/user2/app.py

RUN adduser user3
COPY app.py /home/user3/app.py
RUN mkdir /home/user3/.ssh
COPY key3 /home/user3/.ssh/id_rsa
COPY key3.pub /home/user3/.ssh/id_rsa.pub
RUN chown -R user3:user3 /home/user3 && chmod go= /home/user3/.ssh/ && chmod +x /home/user3/app.py

RUN adduser server
COPY app.py /home/server/app.py
RUN mkdir /home/server/.ssh
COPY server_authorized_keys /home/server/.ssh/authorized_keys
RUN chown -R server:server /home/server && chmod go= /home/server/.ssh/ && chmod +x /home/server/app.py

# SSH login fix. Otherwise user is kicked off after login
#RUN sed 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' -i /etc/pam.d/sshd

RUN service ssh start
RUN service ssh stop

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D"]