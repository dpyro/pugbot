#!/usr/bin/env python2

from twisted.internet import reactor
import sqlalchemy
from colorama import Fore, Back, Style
import colorama

from pugdata import *
from pugserver import network_ip, public_ip, PugServer

from ConfigParser import SafeConfigParser
from os.path import isfile
from random import shuffle
from socket import setdefaulttimeout
from sys import stderr
import logging

class PugApp(object):
    def __init__(self, cfg_file="pugbot.cfg", log_file="pugbot.log"):
        colorama.init()
        setdefaulttimeout(5)
        
        log_fmt = "%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s"
        self.logger = self._init_logger(log_file, log_fmt)
        self.logger_console = logging.getLogger("%s.Console" % (self.__class__.__name__))
        self.print_info("* Started logging: %s" % (log_file))
        
        self.cfg = SafeConfigParser()
        self.cfg.read(cfg_file)
        self.print_info("* Read config file: %s" % (cfg_file))

        self._read_cfg(cfg_file)

        self.host = PugAppPluginHost(self)

        self.public_ip = public_ip()
        self.ip = network_ip()
        self.print_info("* Discovered public ip: %s, network ip: %s" % (self.public_ip, self.ip))

        self.rcon = PugServer(self.rcon_server, self.rcon_port, self.rcon_password, self.rcon_log_port)
        self.print_rcon("* authenticated for rcon: %s:%d, listening on %s:%d" %
            (self.rcon_server, self.rcon_port, self.rcon.network_ip, self.rcon_log_port))
        
        db_exists = isfile(self.db_file)
        self.db_engine = sqlalchemy.create_engine("sqlite:///%s" % (self.db_file), echo=True)
        #if not db_exists:
        #    db_Base.metadata.create_all(self.db_engine)
        #    self.print_db("* created new sqlite3 db for PugBot: %s" % (self.db_file))
        self.print_db("* connected to sqlite3 db: %s" % (self.db_file))

        self.game = None
        self.players = []
        self.games = []

    def _init_logger(self, log_file, log_fmt):
        formatter = logging.Formatter(log_fmt)
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setFormatter(formatter)
        logger = logging.getLogger(self.__class__.__name__)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger

    def _read_cfg(self, cfg_file):
        self.irc_nick = self.cfg.get("irc", "nick")
        self.irc_pass = self.cfg.get("irc", "password")
        self.irc_server = self.cfg.get("irc", "server")
        self.irc_port = self.cfg.getint("irc", "port")
        self.irc_ssl = self.cfg.getboolean("irc", "ssl")
        self.irc_channel = self.cfg.get("irc", "channel")
        self.irc_color = self.cfg.getboolean("irc", "color")

        self.rcon_server = self.cfg.get("rcon", "server")
        self.rcon_port = self.cfg.getint("rcon", "port")
        self.rcon_log_port = self.cfg.getint("rcon", "log_port")
        self.rcon_password = self.cfg.get("rcon", "password")

        self.mumble_server = self.cfg.get("mumble", "server")
        self.mumble_port = self.cfg.getint("mumble", "port")

        self.db_file = self.cfg.get("db", "file")
        
    def run(self):
        reactor.run()

    def startgame(self):
        self.game = True
        del self.players[:]
        self.logger.info('Game STARTED')
        self.host.exec_startgame()

    def add(self, player):
        if self.game:
            self.players.append(player)
            self.logger.info('Player "%s" ADDED to the game' % (player))
            self.host.exec_add(player)
            return True
        else:
            return False

    def remove(self, player):
        if player in self.players:
            self.players.remove(player)
            self.logger.info('Player "%s" REMOVED from the game' % (player))
            self.host.exec_remove(player)
            return True
        else:
            return False

    def teams(self):
        # pick teams
        shuffle(self.players)
        size = len(self.players)
        team1 = self.players[:size/2]
        team2 = self.players[size/2:]
        self.endgame()
        self.host.exec_teams(team1, team2)
        return (team1, team2)

    def changename(self, oldname, newname):
        if oldname in self.players:
            self.players.remove(oldname)
            self.players.append(newname)
            self.host.exec_changename(oldname, newname)
            return True
        else:
            return False

    def endgame(self):
        if self.game is not None:
            self.game = None
            del self.players[:]
            self.logger.info('Game ENDED')
            self.host.exec_endgame()
            return True
        else:
            return False

    def serverinfo(self):
        i = self.rcon.info()
        self.logger.info('Retrieved %d info elements via rcon_query' % (len(i)))
        self.host.exec_serverinfo(i)
        return i

    def print_info(self, str):
        print Style.BRIGHT + Fore.WHITE + str + Style.RESET_ALL
        self.logger_console.info(str)

    def print_irc(self, str):
        print Style.BRIGHT + Fore.GREEN + str + Style.RESET_ALL
        self.logger_console.info(str)

    def print_rcon(self, str):
        print Style.BRIGHT + Fore.CYAN + str + Style.RESET_ALL
        self.logger_console.info(str)

    def print_db(self, str):
        print Style.BRIGHT + Fore.MAGENTA + str + Style.RESET_ALL
        self.logger_console.info(str)

    def print_error(self, str):
        print Style.BRIGHT + Fore.RED + Back.WHITE + str + Style.RESET_ALL
        self.logger_console.error(str)

class PugAppPluginHost(object):
    def __init__(self, app):
        self.app = app
        self.plugins = []

    def add_plugin(self, plugin):
        self.plugins.append(plugin)

    def _exec_handler(self, handler, *args):
        for p in self.plugins:
            try:
                handler(p, *args)
            except Exception as e:
                print >> stderr, e
                self.app.logger.exception(e)

    def exec_startgame(self):
        self._exec_handler(PugAppPlugin.on_startgame)

    def exec_add(self, user):
        self._exec_handler(PugAppPlugin.on_add, user)

    def exec_remove(self, user):
        self._exec_handler(PugAppPlugin.on_remove, user)

    def exec_teams(self, team1, team2):
        self._exec_handler(PugAppPlugin.on_teams, team1, team2)

    def exec_endgame(self):
        self._exec_handler(PugAppPlugin.on_endgame)

    def exec_changename(self, oldname, user):
        self._exec_handler(PugAppPlugin.on_changename, oldname, user)

    def exec_serverinfo(self, info):
        self._exec_handler(PugAppPlugin.on_serverinfo, info)

class PugAppPlugin(object):
    def on_startgame(self):
        pass

    def on_add(self, user):
        pass

    def on_remove(self, user):
        pass

    def on_teams(self, team1, team2):
        pass

    def on_endgame(self):
        pass

    def on_changename(self, oldname, user):
        pass

    def on_serverinfo(self, info):
        pass

