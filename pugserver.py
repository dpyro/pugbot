from datetime import datetime
from time import sleep
from urllib2 import urlopen, URLError
import socket

from SourceLib.SourceLog import SourceLogParser, SourceLogListener
from SourceLib.SourceQuery import SourceQuery
from SourceLib.SourceRcon import SourceRcon, SourceRconError

def network_ip():
    return socket.gethostbyname(socket.gethostname())

def public_ip():
    global _public_ip
    if _public_ip is None:
        result = None
        while _public_ip is None:
            try:
                _public_ip = urlopen("http://www.whatismyip.org").read()
            except URLError:
                sleep(1)
    return _public_ip

_public_ip = None

class PugServer(object):
    def __init__(self, address, port, rcon_pass, local_port=17105):
        self.network_ip = network_ip()
        self.public_ip = public_ip()
        self.query = SourceQuery(address, port)
        self.srcon = SourceRcon(address, port, rcon_pass)
        self.logger = SourceLogListener((self.network_ip, local_port), (address, port), PugServerLogParser(self))
        self.rcon('logaddress_add "%s:%d"' % (self.public_ip, local_port))

    def connect(self):
        self.query.connect()
        self.srcon.connect()

    def disconnect(self):
        self.query.disconnect()
        self.srcon.disconnect()

    def info(self):
        return self.query.info()

    def player(self):
        return self.query.player()

    def rules(self):
        return self.query.rules()

    def rcon(self, command):
        return self.srcon.rcon(command)[:-1]

    def cevo(self):
        stopwatch_maps = ['cp_dustbowl', 'cp_egypt', 'cp_gorge', 'cp_gravelpit', 'cp_junction', 'cp_steel']
        map = self.info()['map']
        if map.startswith('ctf_'):
            file = 'cevo_ctf.cfg'
        elif map.startswith('koth_'):
            file = 'cevo_koth.cfg'
        elif map in stopwatch_maps:
            file = 'cevo_stopwatch.cfg'
        else:
            file = 'cevo_push.cfg'
        return self.rcon('exec "%s"' % (file))

    def changelevel(self, map):
        result = self.rcon('changelevel "%s"' % (map))
        return "No such map" not in result

    def status(self):
        return self.rcon('status')

# FIXME: port to twisted?
class PugServerLogParser(SourceLogParser):
    def __init__(self, pug_server):
        self.server = pug_server
        self.game = False

    def action(self, remote, timestamp, key, value, properties):
        if key == 'trigger_world' and value['trigger'] == 'Round_Start':
            info = self.server.info()
            status = self.server.status()
            self.game = True

        print remote, timestamp, key, value, properties


if __name__ == '__main__':
    address = "208.94.240.46"
    port = 27015
    password = "fr33r4d1c4l5"
    
    s = PugServer(address, port, password)
    print s.status()

