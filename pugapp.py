#!/usr/bin/env python2

from __future__ import print_function

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from twisted.internet import reactor
from colorama import Fore, Back, Style
import colorama

from pugbot import *
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
        self.logger_console = logging.getLogger("{0}.Console".format(self.__class__.__name__))
        self.print_info("* Started logging: {0}".format(log_file))
        
        self.cfg = SafeConfigParser()
        self.cfg.read(cfg_file)
        self.print_info("* Read config file: {0}".format(cfg_file))

        self._read_cfg(cfg_file)

        self.public_ip = public_ip()
        self.ip = network_ip()
        self.print_info("* Discovered public ip: {0}, network ip: {1}".format(self.public_ip, self.ip))

        connectTCP(self.irc_server, self.irc_port, self)

        self.rcon = PugServer(self.rcon_server, self.rcon_port, self.rcon_password, self.rcon_log_port)
        self.print_rcon("* authenticated for rcon: {0}:{1}, listening on {2}:{3}".format(
            self.rcon_server, self.rcon_port, self.rcon.network_ip, self.rcon_log_port))
        
        db_exists = isfile(self.db_file)
        self.db_engine = create_engine("sqlite:///{0}".format(self.db_file), echo=True)
        self.db_session = sessionmaker(connection=self.db_engine, autoflush=True)
        if not db_exists:
            create_all(self.db_engine)
            self.print_db("* created new sqlite3 db for PugBot: {0}".format(self.db_file))
        self.print_db("* connected to sqlite3 db: {0}".format(self.db_file))

        self.game = None
        self.map = "cp_badlands"
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

    def add(self, player):
        """ Add a PugUser `player` to the game. """
        if self.game:
            self.players.append(player)
            self.logger.info('Player "{0}" ADDED to the game'.format(player.irc_nick))
            return True
        else:
            return False

    def remove(self, player):
        """ Remove a PugUser `player` from the game. """
        if player in self.players:
            self.players.remove(player)
            self.logger.info('Player "{0}" REMOVED from the game'.format(player.irc_nick))
            return True
        else:
            return False

    def teams(self):
        # pick teams
        shuffle(self.players)
        size = len(self.players)
        team1 = self.players[:size/2]
        team2 = self.players[size/2:]
        self._rungame(self.map, team1, team2)
        return (team1, team2)

    def _rungame(self, server, port, map, team1, team2):
        del self.players[:]
        self.game = None
        self.map = None
        game = PugGame(server, port, map)
        self.db_session.add(game)
        for player in team1:
            p = PugParticipation(player, game, u"Blu", u"", False)
            self.db_session.add(p)
        for player in team2:
            p = PugParticipation(player, game, u"Red", u"", False)
            self.db_session.add(p)
        self.db_session.commit()
        self.rcon.changemap(map)

    def endgame(self):
        if self.game is not None:
            self.game = None
            del self.players[:]
            self.logger.info('Game ENDED')
            return True
        else:
            return False

    def serverinfo(self):
        i = self.rcon.info()
        self.logger.info('Retrieved {0} info elements via rcon_query'.format(len(i)))
        self.logger.debug(i)
        return i

    def print_info(self, str):
        print(Style.BRIGHT + Fore.WHITE + str + Style.RESET_ALL)
        self.logger_console.info(str)

    def print_irc(self, str):
        print(Style.BRIGHT + Fore.GREEN + str + Style.RESET_ALL)
        self.logger_console.info(str)

    def print_rcon(self, str):
        print(Style.BRIGHT + Fore.CYAN + str + Style.RESET_ALL)
        self.logger_console.info(str)

    def print_db(self, str):
        print(Style.BRIGHT + Fore.MAGENTA + str + Style.RESET_ALL)
        self.logger_console.info(str)

    def print_error(self, str):
        print(Style.BRIGHT + Fore.RED + Back.WHITE + str + Style.RESET_ALL, file=stderr)
        self.logger_console.error(str)

if __name__ == '__main__':
    app = PugApp()
    app.run()

