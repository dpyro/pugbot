from base64 import b64encode
from random import getrandbits
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import func
from sys import stderr
from struct import pack
from urllib2 import URLError, urlopen
from xml.etree import ElementTree

class PugException(Exception):
    pass

Base = declarative_base()

def create_all(db_engine, *args):
    Base.metadata.create_all(db_engine, *args)

class PugData(object):
    id      = Column(Integer, primary_key=True)
    time    = Column(DateTime, default=func.now())

class PugUser(Base, PugData):
    IRC_USER    = 0
    IRC_VOICED  = 100
    IRC_OP      = 200
    IRC_MASTER  = 300
    IRC_COOWNER = 400
    IRC_OWNER   = 500

    __tablename__ = 'pug_user'
    irc_account     = Column(String)
    irc_nick        = Column(String)
    irc_uname       = Column(String)
    irc_host        = Column(String)
    irc_access      = Column(Integer)
    steam_id        = Column(Integer)
    steam_vcode     = Column(String)
    email           = Column(String)
    email_verified  = Column(Boolean)
    facebook_oauth  = Column(String)

    def __init__(self, irc_nick, irc_account, irc_access=IRC_USER):
        self.irc_nick = irc_nick
        self.irc_account = irc_account
        self.irc_access = irc_access

    def gen_steam_vcode(self):
        """ Generates a tag + random 6 character verification code for the user. """
        r = getrandbits(8 * 8)
        b = pack('<Q', r)
        code = b64encode(b[:6])
        self.steam_vcode = "[pugbot:%s]" % (code)
        return self.steam_vcode

    def verify_steam_vcode(self, profile_id):
        if self.steam_vcode is None:
            raise PugException("You must first request a verification code for your Steam profile.")
        # from the Valve wiki
        type = "profiles" if profile_id.isdigit() and int(profile_id) >= 76561197960265728 else "id"
        xml_link = "https://www.steamcommunity.com/%s/%s?xml=1" % (type, profile_id)
        try:
            f = urlopen(xml_link, timeout=2)
        except URLError:
            print >> stderr, e
            raise PugException("Could not connect to the Steam Community site.")
        xtree = ElementTree.parse(f)
        if xtree.find("error") is not None:
            raise PugException(xtree.find("error").text)
        if xtree.find("privacyState").text.lower() != "public":
            raise PugException("You must set your Steam profile as public.")
        searchtags = ["steamID", "headline", "location", "realname", "summary"]
        for tag in searchtags:
            if self.steam_vcode in xtree.find(tag).text:
                steam_id = xtree.find("steamID64")
                assert steam_id is not None and steam_id.isdigits()
                self.steam_id = int(steam_id)
                return True
        return False

class PugGame(Base, PugData):
    __tablename__ = 'pug_game'
    server      = Column(String)
    port        = Column(Integer)
    map         = Column(String)

    def __init__(self, server, port, map):
        self.server = server
        self.port = port
        self.map = map

class PugParticipation(Base, PugData):
    __tablename__ = 'pug_participation'
    user_id     = Column(Integer, ForeignKey('pug_user.id'))
    user        = relationship(PugUser, backref=__tablename__)
    game_id     = Column(Integer, ForeignKey('pug_game.id'))
    game        = relationship(PugGame, backref=__tablename__)
    team        = Column(String)
    team_class  = Column(String)
    captain     = Column(Boolean)

    def __init__(self, user, game, team, team_class, captain):
        self.user_id = user.id
        self.game_id = game.id
        self.team = team
        self.team_class = team_class
        self.captain = captain

if __name__ == '__main__':
    u = PugUser('me')
    print u.verify_steam_vcode("550")

