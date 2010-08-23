#!/usr/bin/env python2
# -*- coding: utf-8 -*-
# vim: enc=utf-8

from sys import stderr
import logging
import re

from twisted.words.protocols import irc
from twisted.internet import reactor, protocol, task

from pugapp import PugApp
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
        log_message = u"%s (msg) ← %s" % (user, message_stripped)
        self.logger.info(log_message) if user != self.app.irc_server else self.logger.debug(log_message)
        if type is not None:
            message = self._colorize(message, type)
        irc.IRCClient.msg(self, user, message)

    def notice(self, user, message, type=None):
        message_stripped = self._strip_all(message)
        self.logger.info(u"%s (notice) ← %s" % (user, message_stripped))
        if type is not None:
            message = self._colorize(message, type)
        user = user.split('!', 1)[0]
        irc.IRCClient.notice(self, user, message)

    def describe(self, channel, action):
        self.logger.info("%s (action) ← %s" % (channel, action))
        irc.IRCClient.describe(self, channel, action)

    def whois(self, nickname, server=None):
        self.logger.debug(u"Requested WHOIS %s" % (nickname))
        irc.IRCClient.whois(self, nickname, server)
    
    # callbacks
    def signedOn(self):
        self.logger.info(u"Signed onto IRC network %s:%d" % (self.app.irc_server, self.app.irc_port))
        self._nickserv_login()
        self.join(self.app.irc_channel)
        self.keep_alive.start(60)

    def joined(self, channel):
        self.app.print_irc("* joined channel %s" % (channel))
        self.logger.info(u"Joined channel %s" % (channel))
        self._who(channel)
        self.whois(self.app.irc_nick)

    def left(self, channel):
        self.app.print_irc("* left channel %s" % (channel))
        self.logger.info(u"Left channel %s" % (channel))
        self.nickmodes.clear()

    def kickedFrom(self, channel, kicker, message):
        self.logger.warning(u"Kicked from %s by %s (%s)" % (channel, kicker, message))
        self.nickmodes.clear()
        task.deferLater(reactor, 5.0, self.join, self.app.irc_channel)

    def nickChanged(self, nick):
        self.logger.warning(u"Nick changed to: %s" % (nick))

    def privmsg(self, user, channel, msg):
        msg = self._strip_all(msg)
        self.logger.info(u":%s (msg) → %s: %s" % (user, channel, msg))
        cmd = msg.split(' ', 1)[0].lower()
        nick = user.split('!', 1)[0]
        if cmd in _commands:
            cmd_f, cmd_access = _commands[cmd]
            if cmd_access is None:
                cmd_f(self, user, channel, msg)
            elif nick not in self.nickmodes:
                self._who(channel)
                self.notice(user, "Refreshing access list, please try again shortly.", self.MSG_ERROR)
            elif self.nickmodes[nick] >= cmd_access:
                cmd_f(self, user, channel, msg)
            else:
                self.notice(user, "You don't have access to this command!", self.MSG_ERROR)

    
    def noticed(self, user, channel, msg):
        self.logger.info(u"%s (notice) → %s: %s" % (user, channel, msg))

    def action(self, user, channel, data):
        self.logger.info(u"%s (action) → %s: %s" % (user, channel, msg))

    def userLeft(self, user, channel):
        nick = PugBot._get_nick(user)
        if nick in self.nickmodes:
            del self.nickmodes[nick]
        self.logger.info("%s left %s" % (user, channel))
        if nick in self.app.players:
            self.app.remove(nick)
            self.logger.debug(u"Removed user %s from game (left %s)" % (user, channel))
            self._list_players(channel)

    def userQuit(self, user, quitMessage):
        nick = PugBot._get_nick(user)
        if nick in self.nickmodes:
            del self.nickmodes[nick]
        self.logger.info("%s quit" % (user))
        if nick in self.app.players:
            self.app.remove(nick)
            self.logger.debug(u"Removed user %s from game (quit)" % (user))
            self._list_players(channel)

    def userKicked(self, kickee, channel, kicker, message):
        nick = PugBot._get_nick(kickee)
        if nick in self.nickmodes:
            del self.nickmodes[nick]
        self.logger.info("%s kicked by %s in %s" % (kickee, kicker, channel))
        if nick in self.app.players:
            self.app.remove(nick)
            self.logger.debug(u"Removed user %s from game (kicked by %s in %s (%s))" % (kickee, kicker, channel, message))
            self._list_players(channel)

    def userRenamed(self, oldname, newname):
        if oldname in self.nickmodes:
            modes = self.nickmodes[oldname]
            del self.nickmodes[oldname]
            self.nickmodes[newname] = modes
        self.logger.info("%s renamed to %s" % (oldname, newname))
        if oldname in self.app.players:
            self.app.changename(oldname, newname)
            self.logger.debug(u"User renamed: %s → %s" % (oldname, newname))

    def modeChanged(self, user, channel, set, modes, args):
        if channel.lower() == self.app.irc_channel:
            self._who(channel)
        mode_prefix = '+' if set else '-'
        for i in range(len(modes)):
            mode = modes[i]
            arg = args[i]
            self.logger.debug(u"%s → %s mode change: %s%s %s" % (user, channel, mode_prefix, mode, arg))

    def pong(self, user, secs):
        self.logger.debug(u"%s (pong) ← %f" % (user, secs))

    def irc_RPL_WHOREPLY(self, prefix, args):
        me, chan, uname, host, server, nick, modes, name = args
        log_msg = u"Recieved WHOREPLY: chan: %s, uname: %s, host: %s, server: %s, nick: %s, modes: %s, name: %s"
        self.logger.debug(log_msg % (chan, uname, host, server, nick, modes, name))
        if chan.lower() == self.app.irc_channel:
            access = PugBot._get_access(modes)
            self.nickmodes[nick] = access
            self.logger.debug(u"Set %s to access level %d" % (nick, access))

    def irc_RPL_ENDOFWHO(self, prefix, args):
        self.logger.debug(u"Recieved WHO list: %s" % (args))

    def irc_RPL_WHOISUSER(self, prefix, args):
        self.logger.debug(u"WHOIS list: %s" % (args))

    def irc_RPL_WHOISACCOUNT(self, prefix, args):
        me, nick, account, msg = args
        self.logger.debug(u"WHOIS account: nick: %s, account %s" % (nick, account))
        if nick in self.users:
            self.users[nick].irc_account = account
        else:
            p_user = PugUser(nick, account)
            self.users[nick] = p_user

    def irc_RPL_ENDOFWHOIS(self, prefix, args):
        self.logger.debug(u"Recieved WHOIS: %s" % (args))
    
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
        msg = 'WHO %s' % channel.lower()
        self.logger.debug(u"Requested %s" % (msg))
        self.sendLine(msg)

    def _ping(self):
        self.ping(self.app.irc_server)

    def _nickserv_login(self):
        self.msg('NickServ@services.', 'IDENTIFY %s %s' % (self.nickname, self.password))

    def _authserv_login(self):
        self.msg('AuthServ@services.', 'AUTH %s %s' % (self.nickname, self.password))
    
    def _list_players(self, channel):
        players = self.app.players
        if len(players) == 0:
            self.msg(channel, "No players are currently signed up.", self.MSG_INFO)
        else:
            player_list = ', '.join(self.app.players)
            suffix = ''
            if len(self.app.players) != 1:
                suffix = 's'
            self.msg(channel, "%d player%s: %s" % (len(players), suffix, player_list), self.MSG_INFO)

    def _teams(self, channel):
        team1, team2 = self.app.teams()
        team1 = ', '.join([p.split('!', 1)[0] for p in team1])
        team2 = ', '.join([p.split('!', 1)[0] for p in team2])
        self.msg(channel, "10,01BLU Team: %s" % (team1))
        self.msg(channel, "05,01RED Team: %s" % (team2))
        msg_red = "You have been assigned to RED team. Connect as soon as possible to %s:%d" % (self.app.rcon_server, self.app.rcon_port)
        msg_blu = "You have been assigned to BLU team. Connect as soon as possible to %s:%d" % (self.app.rcon_server, self.app.rcon_port)
        [self.msg(p.split('!', 1)[0], msg_red, MSG_INFO) for p in team1]
        [self.msg(p.split('!', 1)[0], msg_blu, MSG_INFO) for p in team2]


    # FIXME better
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
                    print >> stderr, Fore.RED + e
                    self.logger.exception(e)

            return exec_cmd

    # commands
    @command('!startgame', PugUser.IRC_OP)
    def cmd_startgame(self, user, channel, msg):
        self.app.startgame()
        self.msg(channel, "Game started. Type !add to join the game.", self.MSG_INFO)

    @command([ '!add', '!a' ])
    def cmd_join(self, user, channel, msg):
        if self.app.game is not None:
            nick = PugBot._get_nick(user)
            if nick not in self.app.players:
                self.app.add(nick)
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

    @command([ '!remove', '!r' ])
    def cmd_remove(self, user, channel, msg):
        nick = PugBot._get_nick(user)
        if nick in self.app.players:
            self.app.remove(nick)
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
        self.msg(channel, "connect %s:%d;" % (self.app.rcon_server, info['port']), self.MSG_INFO)
        self.msg(channel, "%(map)s | %(numplayers)d / %(maxplayers)d | stv: %(specport)s" % (info), self.MSG_INFO)

    @command('!mumble')
    def cmd_mumble(self, user, channel, msg):
        self.msg(channel, "Mumble is the voice server used by players to communicate with each other.", self.MSG_INFO)
        self.msg(channel, "Mumble IP: %s  port: %d" % (self.app.mumble_server, self.app.mumble_port), self.MSG_INFO)

    @command('!version')
    def cmd_version(self, user, channel, msg):
        self.msg(channel, "PugBot: 3alpha", self.MSG_INFO)

    @command('!rtd')
    def cmd_rtd(self, user, channel, msg):
        self.msg(channel, "Don't be a noob, %s." % (user), self.MSG_INFO)

    @command('!bear')
    def cmd_bear(self, user, channel, msg):
        self.describe(channel, "goes 4rawr!", self.MSG_INFO)

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
        msg = "connection lost, reconnecting: %s" % (reason)
        self.app.print_irc(msg)
        self.logger.error(msg)
        protocol.ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

    def clientConnectionFailed(self, connector, reason):
        msg = "connection failed: %s" % (reason)
        self.app.print_irc(msg)
        self.logger.error(msg)
        protocol.ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

if __name__ == '__main__':
    print public_ip()
    app = PugApp()
    connectTCP('irc.freenode.net', 6667, app)
    app.run()
