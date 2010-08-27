#!/usr/bin/env python2
# -*- coding: utf-8 -*-
# vim: enc=utf-8

from __future__ import print_function

from sys import stderr
import logging
import re

from twisted.words.protocols import irc
from twisted.internet import reactor, protocol, task

from pugdata import *
from pugserver import public_ip

def connectSSL(irc_server, irc_port, app):
    f = PugBotFactory(app)
    reactor.connectSSL(irc_server, irc_port, f, ssl.ClientContextFactory())

def connectTCP(irc_server, irc_port, app):
    f = PugBotFactory(app)
    reactor.connectTCP(irc_server, irc_port, f)


# needed for @command decorator
_commands = {}
    
class PugBot(irc.IRCClient):
        
    _re_stripper = re.compile("""[\x0f\x02\x1f\x16\x1d\x11]     | # formatting
                                  \x03(?:\d{1,2}(?:,\d{1,2})?)? | # mIRC colors
                                  \x04[0-9a-fA-F]{0,6}            # rgb colors
                              """, re.UNICODE | re.VERBOSE)
    
    @staticmethod
    def _strip_all(str):
        return PugBot._re_stripper.sub('', str)

    @staticmethod
    def _has_color(str):
        str_strip = PugBot._strip_all(str)
        return str != str_strip

    MSG_INFO    = 0x1
    MSG_CONFIRM = 0x2
    MSG_ERROR   = 0x3

    def __init__(self, app):
        self.app = app
        self.nickname = app.irc_nick
        self.password = app.irc_pass
        self.color = app.irc_color
        self.lineRate = .75
        self.versionName = 'PugBot'
        self.keep_alive = task.LoopingCall(self._ping)
        self.nickmodes = {}
        self.users = {} # (nick, PugUser)
        self.logger = logging.getLogger("PugApp.PugBot")

    def _colorize(self, str, type):
        color_dict = {
            self.MSG_ERROR   : '\x02\x035,01',
            self.MSG_INFO    : '\x02\x030,01',
            self.MSG_CONFIRM : '\x02\x033,01'
        }
        color_reset     = '\x0f'
        
        if self.color:
            # only automatically color if no (custom) color formatting is already present
            str = color_dict.get(type, '') + str + color_reset if not self._has_color(str) else str + color_reset
        else:
            str = self._strip_all(str)
        return str


    # overrides
    def msg(self, user, message, type=None):
        message_stripped = self._strip_all(message)
        log_message = u"{0} (msg) ← {1}".format(user, message_stripped)
        self.logger.info(log_message) if user != self.app.irc_server else self.logger.debug(log_message)
        if type is not None:
            message = self._colorize(message, type)
        nick = PugBot._get_nick(user)
        irc.IRCClient.msg(self, nick, message)

    def notice(self, user, message, type=None):
        message_stripped = self._strip_all(message)
        self.logger.info(u"{0} (notice) ← {1}".format(user, message_stripped))
        if type is not None:
            message = self._colorize(message, type)
        nick = PugBot._get_nick(user)
        irc.IRCClient.notice(self, nick, message)

    def describe(self, channel, action):
        self.logger.info("{0} (action) ← {1}".format(channel, action))
        irc.IRCClient.describe(self, channel, action)

    def whois(self, nickname, server=None):
        self.logger.debug(u"Requested WHOIS {0}".format(nickname))
        irc.IRCClient.whois(self, nickname, server)
    
    # callbacks
    def signedOn(self):
        self.logger.info(u"Signed onto IRC network {0}:{1}".format(self.app.irc_server, self.app.irc_port))
        self._nickserv_login()
        self.join(self.app.irc_channel)
        self.keep_alive.start(100)

    def joined(self, channel):
        self.app.print_irc("* joined channel {0}".format(channel))
        self.logger.info(u"Joined channel {0}".format(channel))
        self._who(channel)
        self.whois(self.app.irc_nick)

    def left(self, channel):
        self.app.print_irc("* left channel {0}".format(channel))
        self.logger.info(u"Left channel {0}".format(channel))
        self.nickmodes.clear()
        self.users.clear()

    def kickedFrom(self, channel, kicker, message):
        self.logger.warning(u"Kicked from {0} by {1} ({2})".format(channel, kicker, message))
        self.nickmodes.clear()
        self.users.clear()
        task.deferLater(reactor, 5.0, self.join, self.app.irc_channel)

    def nickChanged(self, nick):
        self.logger.warning(u"Nick changed to: {0}".format(nick))

    def privmsg(self, user, channel, msg):
        msg = self._strip_all(msg)
        self.logger.info(u":{0} (msg) → {1}: {2}".format(user, channel, msg))
        cmd = msg.split(' ', 1)[0].lower()
        nick = PugBot._get_nick(user)
        if cmd in _commands:
            cmd_f, cmd_access = _commands[cmd]
            if cmd_access is None:
                cmd_f(self, user, channel, msg)
            elif nick not in self.users:
                self.whois(nick)
                self.notice(user, "Refreshing access list, please try again shortly.", self.MSG_ERROR)
            elif self.users[nick].irc_access >= cmd_access:
                cmd_f(self, user, channel, msg)
            else:
                self.notice(user, "You don't have access to this command!", self.MSG_ERROR)

    
    def noticed(self, user, channel, msg):
        self.logger.info(u"{0} (notice) → {1}: {2}".format(user, channel, msg))

    def action(self, user, channel, data):
        self.logger.info(u"{0} (action) → {1}: {2}".format(user, channel, msg))

    def _purge_user(self, user, reason):
        self.logger.info(u"{0}: {1}".format(user, reason))
        nick = PugBot._get_nick(user)
        if nick in self.users:
            p_user = self.users[nick]
            if p_user in self.app.players:
                self.app.remove(p_user)
                self.logger.debug(u"Removed user {0} from game ({1})".format(nick, reason))
                self._list_players(channel)
            del self.users[nick]


    def userLeft(self, user, channel):
        reason = u"left {0}".format(channel)
        if channel.lower() == self.app.irc_channel:
            self._purge_user(user, reason)

    def userQuit(self, user, quitMessage):
        reason = u"quit ({0})".format(quitMessage)
        self._purge_user(user, reason)

    def userKicked(self, kickee, channel, kicker, message):
        reason = u"kicked by {0} in {1} ({2})".format(kicker, channel, message)
        if channel.lower() == self.app.irc_channel:
            self._purge_user(kickee, reason)

    def userRenamed(self, oldname, newname):
        if oldname in self.users:
            p_user = self.users[oldname]
            p_user.irc_name = newname
            self.db_session.add(p_user)
            self.db_session.commit()
            self.users[newname] = p_user
            del self.users[oldname]
        self.logger.info(u"User renamed: {0} → {1}".format(oldname, newname))

    def modeChanged(self, user, channel, set, modes, args):
        if channel.lower() == self.app.irc_channel:
            self._who(channel)
        mode_prefix = '+' if set else '-'
        for mode, arg in zip(modes, args):
            self.logger.debug(u"{0} → {1} mode change: {2}{3} {4}".format(
                user, channel, mode_prefix, mode, arg))

    def pong(self, user, secs):
        self.logger.debug(u"{0} (pong) ← {1}".format(user, secs))

    def irc_RPL_WHOREPLY(self, prefix, args):
        me, chan, uname, host, server, nick, modes, name = args
        log_msg = u"Recieved WHOREPLY: chan: {0}, uname: {1}, host: {2}, server: {3}, nick: {4}, modes: {5}, name: {6}".format(
            chan, uname, host, server, nick, modes, name)
        self.logger.debug(log_msg)
        if chan.lower() == self.app.irc_channel:
            access = PugBot._get_access(modes)
            self.nickmodes[nick] = access
            self.logger.debug(u"Set {0} to access level {1}".format(nick, access))

    def irc_RPL_ENDOFWHO(self, prefix, args):
        self.logger.debug(u"Recieved WHO list: {0}".format(args))

    def irc_RPL_WHOISUSER(self, prefix, args):
        self.logger.debug(u"WHOIS list: {0}".format(args))

    def irc_RPL_WHOISACCOUNT(self, prefix, args):
        me, nick, account, msg = args
        self.logger.debug(u"WHOIS account: nick: {0}, account {1}".format(nick, account))
        if nick in self.users:
            self.users[nick].irc_account = account
        else:
            p_user = PugUser(nick, account)
            self.users[nick] = p_user

    def irc_RPL_ENDOFWHOIS(self, prefix, args):
        self.logger.debug(u"Recieved WHOIS: {0}".format(args))
    
    @staticmethod
    def _get_nick(user):
        return user.split('!', 1)[0]

    @staticmethod
    def _get_access(modes):
        mode_dict = {
            '@': PugUser.IRC_OP,
            '+': PugUser.IRC_VOICED
        }
        for key, val in mode_dict.iteritems():
            if key in modes:
                return val
        return PugUser.IRC_USER
    
    def _who(self, channel):
        msg = 'WHO {0}'.format(channel.lower())
        self.logger.debug(u"Requested {0}".format(msg))
        self.sendLine(msg)

    def _ping(self):
        self.ping(self.app.irc_server)

    def _nickserv_login(self):
        self.msg('NickServ@services.', 'IDENTIFY {0} {1}'.format(self.nickname, self.password))

    def _authserv_login(self):
        self.msg('AuthServ@services.', 'AUTH {0} {1}'.format(self.nickname, self.password))
    
    def _list_players(self, channel):
        players = self.app.players
        if len(players) == 0:
            self.msg(channel, "No players are currently signed up.", self.MSG_INFO)
        else:
            player_list = ', '.join((p.irc_nick for p in self.app.players))
            suffix = 's' if len(self.app.players) != 1 else ''
            self.msg(channel, "{0} player{1}: {2}".format(len(players), suffix, player_list), self.MSG_INFO)

    def _teams(self, channel):
        team1, team2 = self.app.teams()
        team1 = ', '.join((p.irc_nick for p in team1))
        team2 = ', '.join((p.irc_nick for p in team2))
        self.msg(channel, "10,01BLU Team: {0}".format(team1))
        self.msg(channel, "05,01RED Team: {0}".format(team2))
        msg_red = "You have been assigned to RED team. Connect as soon as possible to {0}:{1}".format(
            self.app.rcon_server, self.app.rcon_port)
        msg_blu = "You have been assigned to BLU team. Connect as soon as possible to {0}:{1}".format(
            self.app.rcon_server, self.app.rcon_port)
        [self.msg(p.irc_nick, msg_red, MSG_INFO) for p in team1]
        [self.msg(p.irc_nick, msg_blu, MSG_INFO) for p in team2]

    
    class command(object):
        def __init__(self, name, access=None):
            self.name = name
            self.access = access

        def __call__(self, f):
            global _commands
            if not isinstance(self.name, str):
                for name in self.name:
                    name = name.lower()
                    _commands[name] = (f, self.access)
            else:
                name = self.name.lower()
                _commands[name] = (f, self.access)
            
            def exec_cmd(*args):
                try:
                    f(args)
                except Exception as e:
                    print(Fore.RED + e, file=stderr)
                    self.logger.exception(e)

            return exec_cmd

    # commands
    @command('!startgame', PugUser.IRC_OP)
    def cmd_startgame(self, user, channel, msg):
        self.app.startgame()
        self.msg(channel, "Game started. Type !add to join the game.", self.MSG_INFO)

    @command([ '!add', '!a' ], PugUser.IRC_USER)
    def cmd_join(self, user, channel, msg):
        nick = PugBot._get_nick(user)
        p_user = self.users[nick]
        if self.app.game is not None:
            if p_user not in self.app.players:
                self.app.add(p_user)
                self.notice(user, "You successfully added to the game.", self.MSG_CONFIRM)
                if len(self.app.players) >= 12:
                    self._teams(channel)
                else:
                    self._list_players(channel)
            else:
                self.notice(user, "You have already signed up for the game!", self.MSG_ERROR)
        else:
            self.notice(user, "There is no active game to sign up for!", self.MSG_ERROR)
    
    @command('!join')
    def cmd_add(self, user, channel, msg):
        self.notice(user, "Please use !add instead.", self.MSG_ERROR)

    @command([ '!remove', '!r' ], PugUser.IRC_USER)
    def cmd_remove(self, user, channel, msg):
        nick = PugBot._get_nick(user)
        p_user = self.users[nick]
        if p_user in self.app.players:
            self.app.remove(p_user)
            self.notice(user, "You successfully removed from the game.", self.MSG_CONFIRM)
            self._list_players(channel)
        else:
            self.notice(user, "You are not in the game!", self.MSG_ERROR)

    @command(('!players', '!p'))
    def cmd_list(self, user, channel, msg):
        if self.app.game is None:
            self.msg(channel, "There is no game running currently.", self.MSG_INFO)
        else:
            self._list_players(channel)
    
    @command('!endgame', PugUser.IRC_OP)
    def cmd_endgame(self, user, channel, msg):
        if self.app.game is not None:
            self.app.endgame()
            self.msg(channel, "Game ended.", self.MSG_INFO)
        else:
            self.notice(user, "There is no game to be ended!", self.MSG_ERROR)

    @command('!server')
    def cmd_server(self, user, channel, msg):
        info = self.app.serverinfo()
        self.msg(channel, "connect {0}:{1};".format(self.app.rcon_server, info['port']), self.MSG_INFO)
        #TODO: Why does it give key errors when using format()?
        self.msg(channel, "%(map)s | %(numplayers)s / %(maxplayers)s | stv: %(specport)s" % (info), self.MSG_INFO)

    @command('!mumble')
    def cmd_mumble(self, user, channel, msg):
        self.msg(channel, ("Mumble is the shiniest new voice server/client used by players to communicate with each other.\n"
                          "It's not laggy as hell like Ventrilo and has a sweet ingame overlay. Unfortunately, Europeans use it.\n"
                          "Mumble IP: {0}  port: {1}").format(self.app.mumble_server, self.app.mumble_port), self.MSG_INFO)

    @command('!version')
    def cmd_version(self, user, channel, msg):
        self.msg(channel, "PugBot: 3alpha", self.MSG_INFO)

    @command('!bear')
    def cmd_bear(self, user, channel, msg):
        self.describe(channel, "goes 4rawr!", self.MSG_INFO)
    
    @command('!magnets')
    def cmd_magnets(self, user, channl, msg):
        self.msg(channel, "What am I, a scientist?", self.MSG_INFO)

    @command('!rtd')
    def cmd_rtd(self, user, channel, msg):
        nick = PugBot._get_nick(user)
        self.msg(channel, "Don't be a noob, {0}.".format(nick), self.MSG_INFO)

    @command('!whattimeisit')
    def cmd_whattimeisit(self, user, channel, msg):
        nick = PugBot._get_nick(user)
        self.msg(channel, "Go back to #tf2.pug.na, {0}.".format(nick))


class PugBotFactory(protocol.ReconnectingClientFactory):
    protocol = PugBot

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("PugApp.PugBot")

    def buildProtocol(self, addr):
        self.resetDelay()
        p = PugBot(self.app)
        p.factory = self
        return p

    def clientConnectionLost(self, connector, reason):
        msg = "connection lost, reconnecting: {0}".format(reason)
        self.app.print_irc(msg)
        self.logger.error(msg)
        protocol.ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

    def clientConnectionFailed(self, connector, reason):
        msg = "connection failed: {0}".format(reason)
        self.app.print_irc(msg)
        self.logger.error(msg)
        protocol.ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

