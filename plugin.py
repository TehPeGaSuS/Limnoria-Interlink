###
# Copyright (c) 2015-2020 James Lu <james@overdrivenetworks.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

from copy import deepcopy
import functools
import json
import os
import pickle
import re
import string
import textwrap
import threading
import time

import supybot.world as world
import supybot.irclib as irclib
import supybot.ircmsgs as ircmsgs
import supybot.ircdb as ircdb
import supybot.conf as conf
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.schedule as schedule
from supybot.utils.structures import TimeoutQueue
try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Interlink')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x


filename = conf.supybot.directories.data.dirize("Interlink.db")
# Interlink is a fork of RelayNext; its relays are picked up if Interlink has no database yet.
relaynext_filename = conf.supybot.directories.data.dirize("RelayNext.db")
bans_filename = conf.supybot.directories.data.dirize("Interlink-bans.json")

class ModerationError(Exception):
    """An error in a moderation command, sent to the caller as a private notice so that it isn't
    relayed (bot messages in channels are relayed when events.relaySelfMessages is on)."""

def private_errors(f):
    @functools.wraps(f)
    def newf(self, irc, msg, *args):
        try:
            return f(self, irc, msg, *args)
        except ModerationError as e:
            irc.error(str(e), private=True, notice=True)
    return newf

# Commands that are never relayed (the moderation commands below)
MODERATION_COMMANDS = {'ban', 'tban', 'kick', 'unban', 'bans', 'baninfo'}

class Interlink(callbacks.Plugin):
    """Relays channels between networks, with cross-network moderation for channel ops."""
    threaded = True
    noIgnore = True

    ### Database handling

    def loadDB(self):
        for path in (filename, relaynext_filename):
            try:
                with open(path, 'rb') as f:
                    self.db = pickle.load(f)
                return
            except Exception as e:
                self.log.debug('Interlink: Unable to load pickled database %s: %s', path, e)

    def exportDB(self):
        try:
            with open(filename, 'wb') as f:
                pickle.dump(self.db, f, 2)
        except Exception as e:
             self.log.warning('Interlink: Unable to write pickled database: %s',
                              e)

    def __init__(self, irc):
        self.__parent = super(Interlink, self)
        self.__parent.__init__(irc)

        # This part facilitates flood protection
        self.msgcounters = {}
        self.floodTriggered = {}

        self.db = {}
        self.loadDB()

        # Add our database exporter to world.flushers, so it's automatically
        # ran on every flush cycle.
        world.flushers.append(self.exportDB)

        self.banLock = threading.RLock()
        self.bandb = {'next_id': 1, 'bans': {}}
        self.loadBans()
        # Channels (network, channel) to check against mask bans once their WHO reply ends
        self.pendingScans = set()
        self.scheduleBanEvents()

    def die(self):
        self.exportDB()
        world.flushers.remove(self.exportDB)
        self.saveBans()
        for name in list(schedule.schedule.events):
            if isinstance(name, str) and name.startswith('Interlink-'):
                schedule.removeEvent(name)
        self.__parent.die()

    ### Relayer core

    def simpleHash(self, s, hash_using=''):
        """
        Returns a colorized version of the given text based on a simple hash algorithm
        (sum of all characters).
        """
        colors = ('02', '03', '04', '05', '06', '07', '08', '09', '10', '11',
                  '12', '13')

        # Optionally specify a different string to hash vs. display
        hash_using = hash_using or s

        num = sum([ord(char) for char in hash_using])
        num = num % len(colors)
        return "\x03%s%s\x03" % (colors[num], s)

    @staticmethod
    def blockHighlights(nick):
        """Block highlights in relaying by sticking a zero-width-space in the middle."""
        return (nick[0] + "\u200b" + nick[1:] if len(nick) > 0 else "")

    def _format(self, irc, msg, channel, isAnnouncement=False):
        """
        Formats a relay given the IRC object, msg object, and channel.
        """
        s = ''
        nick = real_nick = msg.nick
        userhost = ''
        noHighlight = self.registryValue('noHighlight', channel=channel, network=irc.network)
        useHostmask = self.registryValue('hostmasks', channel=channel, network=irc.network)
        color = self.registryValue('color', channel=channel, network=irc.network)
        netname = irc.network

        # Adding a zero-width space to prevent being highlighted by clients
        if noHighlight:
            nick = self.blockHighlights(nick)

        if color:
            nick = self.simpleHash(nick, hash_using=real_nick)
            netname = self.simpleHash(netname)

        # Skip hostmask checking if the sender is a server
        # (i.e. a '.' is present in their name)
        if useHostmask and '.' not in nick:
            try:
                userhost = ' (%s)' % msg.prefix.split('!', 1)[1]
            except IndexError:
                pass

        if isAnnouncement:
            # Announcements use a special syntax
            s = '*** %s' % msg.args[1]
        else:
            ignoreRegexp = self.registryValue("ignoreRegexp", channel=channel, network=irc.network)
            if msg.command == 'NICK':
                newnick = msg.args[0]
                if noHighlight:
                    newnick = self.blockHighlights(newnick)

                if color:
                    newnick = self.simpleHash(newnick, hash_using=msg.args[0])

                s = '%s is now known as %s' % (nick, newnick)

            elif msg.command == 'PRIVMSG':
                text = msg.args[1]

                # Show status prefixes (@%+) in front of the nick if enabled,
                # but only the highest prefix.
                if self.registryValue("showPrefixes", channel=channel, network=irc.network):
                    chobj = irc.state.channels[channel]
                    if chobj.isOp(real_nick):
                        nick = '@' + nick
                    elif chobj.isHalfop(real_nick):
                        nick = '%' + nick
                    elif chobj.isVoice(real_nick):
                        nick = '+' + nick

                if ignoreRegexp and ignoreRegexp.search(text, re.IGNORECASE):
                    self.log.debug("Interlink: filtering message %r from ignoreRegexp %s", text, ignoreRegexp)
                    return

                # Check for CTCP ACTION and format those properly.
                if re.match('^\x01ACTION .*\x01$', text):
                    text = text[8:-1]
                    s = '* %s %s' % (nick, text)
                elif text.startswith('\x01'):
                    # Other CTCP messages should just be ignored
                    return
                else:
                    s = '<%s> %s' % (nick, msg.args[1])

            elif msg.command == 'JOIN':
                s = '%s%s has joined %s' % (nick, userhost, channel)

            elif msg.command == 'PART':
                # Part message isn't a required field and can be empty
                try:
                    partmsg = ' (%s)' % msg.args[1]
                except IndexError:
                    partmsg = ''

                if ignoreRegexp and ignoreRegexp.search(partmsg, re.IGNORECASE):
                    self.log.debug("Interlink: filtering part message %r from ignoreRegexp %s", partmsg, ignoreRegexp)
                    partmsg = ''

                s = '%s%s has left %s%s' % (nick, userhost, channel, partmsg)

            elif msg.command == 'QUIT':
                quitmsg = msg.args[0]
                if ignoreRegexp and ignoreRegexp.search(quitmsg, re.IGNORECASE):
                    self.log.debug("Interlink: filtering quit message %r from ignoreRegexp %s", quitmsg, ignoreRegexp)
                    quitmsg = ''

                s = '%s has quit (%s)' % (nick, quitmsg)

            elif msg.command == 'MODE':
                modes = ' '.join(msg.args[1:])
                s = '%s%s set mode %s on %s' % (nick, userhost, modes, channel)

            elif msg.command == 'TOPIC':
                s = '%s set topic on %s to: %s' % (nick, channel, msg.args[1])

            elif msg.command == 'KICK':
                kicked = msg.args[1]
                # Show the host of the kicked user, not the kicker
                userhost = irc.state.nickToHostmask(kicked).split('!', 1)[1]
                if noHighlight:
                    kicked = self.blockHighlights(kicked)
                if color:
                    kicked = self.simpleHash(kicked, hash_using=msg.args[1])
                s = '%s (%s) has been kicked from %s by %s (%s)' % (kicked,
                    userhost, channel, nick, msg.args[2])

        if s:  # Then, prepend the source network name in bold.
            s = "\x02[%s]\x02 %s" % (netname, s)
        return s

    def checkAntiFlood(self, irc, msg, source, channel):
        """
        Run antiflood on the given network / channel / message, returning True if the given message
        should be processed (i.e. it has not been blocked by antiflood).
        """
        if self.registryValue("antiflood.enable", channel=channel, network=irc.network):
            # Flood prevention timeout - how long commands of a certain type
            # should cease being relayed after flood prevention triggers
            timeout = self.registryValue("antiflood.timeout", channel=channel, network=irc.network)

            # If <maximum> messages of the same kind on one channel is
            # received in <seconds> seconds, flood prevention timeout is
            # triggered.
            maximum = self.registryValue("antiflood.maximum", channel=channel, network=irc.network)
            seconds = self.registryValue("antiflood.seconds", channel=channel, network=irc.network)

            # Store the message in a counter, with the keys taking the
            # form of (source channel@network, command name). If the counter
            # doesn't already exist, create one here.
            source = "%s@%s" % (channel, irc.network)
            if (source, msg.command) not in self.msgcounters:
                self.msgcounters[(source, msg.command)] = TimeoutQueue(seconds)
            self.msgcounters[(source, msg.command)].enqueue(msg.prefix)

            # Two different limits: one for messages and one for all others
            if msg.command == "PRIVMSG":
                maximum = self.registryValue("antiflood.maximum", channel=channel, network=irc.network)
            else:
                maximum = self.registryValue("antiflood.maximum.nonPrivmsgs",
                                             channel=channel, network=irc.network)

            lastFloodTrigger = self.floodTriggered.get((source, msg.command))
            if lastFloodTrigger is not None and (time.time() - lastFloodTrigger) > timeout:
                # Antiflood timeout passed
                self.floodTriggered[(source, msg.command)] = False
            elif lastFloodTrigger:
                # Antiflood was previously triggered
                return False
            elif len(self.msgcounters[(source, msg.command)]) > maximum:
                # Amount of messages in the counter surpassed our limit,
                # announce the flood and block relaying messages of the
                # same type for X seconds

                announce_text = format("Flood detected on %s (%s %ss/%s seconds), "
                                       "not relaying %ss for %s seconds!", channel,
                                       maximum, msg.command, seconds, msg.command, timeout)
                announce_msg = ircmsgs.privmsg(channel, announce_text)

                self.floodTriggered[(source, msg.command)] = time.time()
                self.log.info("Interlink (%s): %s", irc.network, announce_text)
                self.relay(irc, announce_msg, channel, isAnnouncement=True)
                return False
        return True

    def relay(self, irc, msg, channel, isAnnouncement=False):
        self.log.debug("Interlink (%s): got channel %s", irc.network, channel)
        if not channel in irc.state.channels:
            return
        channel = channel.lower()

        # Check for ignored events first. Checking for "'.' not in msg.nick" is for skipping
        # ignore checks from servers.
        if not isAnnouncement:
            ignoredevents = map(str.upper, self.registryValue('events.userIgnored', channel=channel, network=irc.network))
            if msg.command in ignoredevents and msg.nick != irc.nick and '.' not in msg.nick and\
                    ircdb.checkIgnored(msg.prefix, channel):
                self.log.debug("Interlink (%s): ignoring message from %s",
                               irc.network, msg.prefix)
                return

        # Get the source channel
        source = "%s@%s" % (channel, irc.network)
        source = source.lower()

        out_s = self._format(irc, msg, channel, isAnnouncement=isAnnouncement)
        if out_s:
            for relay in self.db.values():
                self.log.debug("Interlink (%s): check if %s in %s", irc.network, source, relay)
                if source in relay:  # If our channel is in a relay
                    self.log.debug("Interlink: found %s to be in relay %s", source, relay)

                    # Remove ourselves from the target channels so we don't get duplicated messages
                    targets = list(relay)
                    targets.remove(source)

                    self.log.debug("Interlink: found targets %s for relay %s", targets, relay)
                    if (not isAnnouncement) and not self.checkAntiFlood(irc, msg, source, channel):
                        self.log.debug("Interlink: antiflood blocked event %s from %s",
                                       msg.command, source)
                        return

                    for cn in targets:
                        # Iterate over all the relay targets for this message:
                        # each target is stored internally as a #channel@netname
                        # string.
                        target, net = cn.split("@")
                        otherIrc = world.getIrc(net)
                        if otherIrc is None:
                            self.log.debug("Interlink: message to network %r dropped, we are not "
                                           "connected there!", net)
                            continue

                        target_chanobj = otherIrc.state.channels.get(target)
                        if (not target_chanobj) or otherIrc.nick not in target_chanobj.users:
                            # We're not in the target relay channel!
                            self.log.debug("Interlink: message to %s@%s dropped, we are not in that "
                                           "channel!", target, net)
                        else:
                            for line in self._splitLine(otherIrc, target, out_s):
                                out_msg = ircmsgs.privmsg(target, line)

                                # Tag the message as relayed so we (and other relayers) don't
                                # try to relay it again.
                                out_msg.tag('relayedMsg')
                                otherIrc.queueMsg(out_msg)

    # The part of a relayed line repeated on each piece when it is split: "[net] <nick> ",
    # "[net] * nick " or just "[net] ".
    _linePrefix = re.compile(r'^(\x02\[[^\]]*\]\x02 (?:<[^>]*> |\* \S+ )?)')

    def _splitLine(self, irc, target, text):
        """Splits a relayed line into pieces that fit in one IRC message (512 bytes, including
        the ":nick!user@host PRIVMSG <target> :" that servers add), at spaces where possible.
        Each piece repeats the network and sender."""
        _nick, ident, host = ircutils.splitHostmask(irc.prefix)
        nick = irc.nick  # The prefix's nick can lag behind a nick change
        if host == 'unset.domain':  # We haven't seen our own host yet; assume a long one.
            host = 'x' * 64
        overhead = len((':%s!%s@%s PRIVMSG %s :\r\n' % (nick, ident, host, target)).encode('utf-8'))
        limit = 512 - overhead
        if len(text.encode('utf-8')) <= limit:
            return [text]

        match = self._linePrefix.match(text)
        head = match.group(1) if match else ''
        body = text[len(head):]
        room = max(limit - len(head.encode('utf-8')), 50)
        pieces = []
        current = ''
        for word in body.split(' '):
            candidate = word if not current else current + ' ' + word
            if len(candidate.encode('utf-8')) <= room:
                current = candidate
                continue
            if current:
                pieces.append(current)
            # A single word longer than a line is cut by characters.
            while len(word.encode('utf-8')) > room:
                cut = len(word)
                while len(word[:cut].encode('utf-8')) > room:
                    cut -= 1
                pieces.append(word[:cut])
                word = word[cut:]
            current = word
        if current:
            pieces.append(current)
        return [head + piece for piece in pieces]

    def doPrivmsg(self, irc, msg):
        if msg.channel and self._isModerationCommand(irc, msg):
            return  # Moderation commands stay on their network
        self.relay(irc, msg, channel=msg.channel)

    def _isModerationCommand(self, irc, msg):
        payload = callbacks.addressed(irc, msg)
        words = payload.lower().split() if payload else []
        if words[:1] == [self.name().lower()]:
            words = words[1:]
        return bool(words) and words[0] in MODERATION_COMMANDS

    def doNonPrivmsg(self, irc, msg):
        if self.registryValue("events.relay%ss" % msg.command, channel=msg.channel, network=irc.network):
            self.relay(irc, msg, channel=msg.channel)

    doTopic = doPart = doKick = doNonPrivmsg  # doJoin and doMode also enforce bans (below)

    # NICK and QUIT aren't channel specific, so they require a bit
    # of extra handling
    def _relayNick(self, irc, msg):
        for channel in msg.tagged('channels'):
            if self.registryValue("events.relaynicks", channel=channel, network=irc.network):
                self.relay(irc, msg, channel)

    def doQuit(self, irc, msg):
        for channel in msg.tagged('channels'):
            if self.registryValue("events.relayquits", channel=channel, network=irc.network):
                self.relay(irc, msg, channel)

    def outFilter(self, irc, msg):
        # Catch our own messages and send them into the relay (this is
        # useful because Supybot is often a multi-purpose bot!)
        try:
            if msg.command == 'PRIVMSG' and not msg.relayedMsg and \
                    self.registryValue("events.relaySelfMessages", channel=msg.channel, network=irc.network):
                new_msg = deepcopy(msg)
                new_msg.nick = irc.nick
                self.relay(irc, new_msg, channel=msg.channel)
        except Exception:
            # We want to log errors, but not block the bot's output
            log.exception("Interlink: Caught error in outFilter:")
        finally:
            return msg

    ### User commands

    def nicks(self, irc, msg, args, channel, optlist):
        """[<channel>] [--count]
        Returns the nicks of the people in the linked channels.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        If --count is specified, only the amount of users in the relay is given."""
        opts = dict(optlist)

        if irc.nested and 'count' not in opts:
            irc.error('This command cannot be nested.', Raise=True)

        try:
            c = irc.state.channels[channel]
        except KeyError:
            irc.error("Unknown channel '%s'." % channel, Raise=True)

        if msg.nick not in c.users:
            self.log.warning('Interlink: %s on %s attempted to view '
                             'nicks of %s without being in it.', msg.nick,
                             irc.network, channel)
            irc.error(("You are not in '%s'." % channel), Raise=True)

        source = "%s@%s" % (channel, irc.network)
        source = source.lower()
        channel_count = 0
        user_count = 0
        all_users = []

        # First, enumerate all the relays that the calling channel is it. Use a
        # set to prevent duplicates since one channel can be part of many relays.
        all_relays = set()
        for relay in self.db.values():
            if source in relay:
                for channelpair in relay:
                    # Each channel pair is a "#chan@net" string in the DB.
                    all_relays.add(channelpair)

        for channelpair in all_relays:
            channel_count += 1
            channel, net = channelpair.split("@", 1)
            try:
                c = world.getIrc(net).state.channels[channel]
            except (KeyError, AttributeError):
                # Unknown network or network disconnected.
                continue
            user_count += len(c.users)
            users = []

            # Sort users before listing them, but do so case-insensitively.
            for s in sorted(c.users, key=ircutils.toLower):
                s = s.strip()
                if s in c.ops:
                    users.append('@%s' % s)
                elif s in c.halfops:
                    users.append('%%%s' % s)
                elif s in c.voices:
                    users.append('+%s' % s)
                else:
                    users.append(s)

            all_users += c.users
            s = format('%s users in %s on %s: %L', len(c.users),
                       channel, net, users)

            # In outputting the user list, we need to make sure that the message fits,
            # and if not, wrap it into multiple messages.
            # XXX: This is ugly, but https://github.com/ProgVal/Limnoria/issues/1080
            # means we have to chop off the (XX more messages) part too.
            allowed_length = 466 - len(irc.prefix) - len(irc.nick) - len(msg.nick) - \
                len(_('(XX more messages)'))
            replies = textwrap.wrap(s, allowed_length)

            if 'count' not in opts:
                # Only bother doing this if we're not using --count.
                irc.reply(replies[0], private=True, notice=True)
                for s in replies[1:]:
                    irc.reply("... %s" % s, private=True, notice=True)

        if 'count' in opts:  # --count was specified; just reply with the amount of users.
            irc.reply(user_count)
        elif channel_count:
            irc.reply("Total users across %d channels: %d. Unique nicks: %d" %
                      (channel_count, user_count, len(set(all_users))),
                      private=True)
        else:
            irc.error("No relays for '%s' exist." % channel)
    nicks = wrap(nicks, ['Channel', getopts({'count': ''})])

    def checkRelays(self, irc, relays):
        for relay in relays:
            r = relay.split("@")
            if len(r) != 2 or not (ircutils.isChannel(r[0]) and r[1]):
                irc.error("Channels must be given in the form "
                          "#channel@networkname.", Raise=True)

    def set(self, irc, msg, args, rid, relays):
        """<relay name> <#channel1>@<network1> <#channel2>@<network2> [<#channel3>@<network3>] ...

        Sets <relay name> to relay between the specified channels,
        replacing it if it already exists. Each relay requires at least
        two channels to relay between.
        """
        relays = set(map(str.lower, relays))
        if len(relays) < 2:
            irc.error("Not enough channels to relay between (need at least "
                      "2).", Raise=True)
        self.checkRelays(irc, relays)
        self.db[rid] = relays
        irc.replySuccess()
    set = wrap(set, ['admin', 'somethingWithoutSpaces',
                     many('somethingWithoutSpaces')])

    def add(self, irc, msg, args, rid, relays):
        """<relay name> [<#channel1>@<network1>] [<#channel2>@<network2>] ...

        Adds the specified channels to an existing relay <relay name>,
        creating it if it does not already exist.
        """
        # Supybot's internals are terribly inconsistent here, only
        # returning a list IF there are more than one items. Otherwise,
        # the object is returned alone.
        if type(relays) == list:
            relays = set(map(str.lower, relays))
        else:
            relays = set([relays.lower()])
        self.checkRelays(irc, relays)
        if rid not in self.db.keys() and len(relays) < 2:
            irc.error("Not enough channels to relay between (need at least "
                      "2).", Raise=True)
        try:
            new_relays = set(self.db[rid])
        except KeyError:
            self.db[rid] = new_relays = set()
        new_relays.update(relays)
        self.db[rid] = new_relays
        irc.replySuccess()
    add = wrap(add, ['admin', 'somethingWithoutSpaces',
                     many('somethingWithoutSpaces')])

    def remove(self, irc, msg, args, rid, relays):
        """<relay name> [<#channel1>@<network1>] [<#channel2>@<network2>] ...

        Removes the specified channels from <relay name>. If no channels
        are given, removes the entire relay.
        """
        try:
            current_relays = self.db[rid]
        except KeyError:
            irc.error("No such relay '%s' exists." % rid, Raise=True)
        if not relays:
            del self.db[rid]
            irc.replySuccess()
            return
        relays = list(map(str.lower, relays))
        self.checkRelays(irc, relays)
        missing = []
        for relay in relays:
            if relay not in current_relays:
                missing.append(relay)
            current_relays.discard(relay)
        if len(current_relays) < 2:
            del self.db[rid]
        if missing:
            s = format("However, the following channels were not removed "
                       "because they were not found in the original relay: %L",
                       missing)
            irc.replySuccess(s)
            return
        irc.replySuccess()
    remove = wrap(remove, ['admin', 'somethingWithoutSpaces',
                           any('somethingWithoutSpaces')])

    def list(self, irc, msg, args):
        """takes no arguments.

        Lists all relays currently configured."""
        items = [format("%s: %s", ircutils.bold(k), ' \x02<=>\x02 '.join(v))
                 for (k, v) in self.db.items()]
        if not items:
            irc.error("No relays have been defined.", Raise=True)
        irc.reply(', '.join(items))

    def clear(self, irc, msg, args):
        """takes no arguments.

        Clears all relays defined.
        """
        self.db = {}
        irc.replySuccess()
    clear = wrap(clear, ['admin'])

    ### Moderation: ban, tban, kick and unban across networks

    def loadBans(self):
        try:
            with open(bans_filename) as f:
                self.bandb = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log.warning('Interlink: Unable to load the ban database: %s', e)

    def saveBans(self):
        with self.banLock:
            try:
                tmp = bans_filename + '.tmp'
                with open(tmp, 'w') as f:
                    json.dump(self.bandb, f, indent=4)
                os.replace(tmp, bans_filename)
            except Exception as e:
                self.log.warning('Interlink: Unable to write the ban database: %s', e)

    def _activeBans(self):
        return [ban for ban in self.bandb['bans'].values() if ban['active']]

    def _relaysOf(self, channelpair):
        """Returns the names of the relays the given #channel@network is in."""
        return [name for name, relay in self.db.items() if channelpair.lower() in relay]

    @staticmethod
    def _parseDuration(text):
        """Parses durations like 30m, 2h, 1d or 1h30m into seconds; returns None otherwise."""
        parts = re.findall(r'(\d+)([smhdw])', text.lower())
        if not parts or ''.join(n + u for n, u in parts) != text.lower():
            return None
        units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
        return sum(int(n) * units[u] for n, u in parts)

    @staticmethod
    def _formatDuration(seconds):
        out = []
        for unit, size in (('w', 604800), ('d', 86400), ('h', 3600), ('m', 60), ('s', 1)):
            if seconds >= size:
                out.append('%d%s' % (seconds // size, unit))
                seconds %= size
        return ''.join(out) or '0s'

    def _context(self, irc, msg):
        """Checks that a moderation command is used by a halfop or higher in a relayed channel,
        returning the caller's channel and the names of its relays."""
        channel = msg.channel
        if not channel:
            raise ModerationError(_('Use this command in a relayed channel.'))
        relays = self._relaysOf('%s@%s' % (channel, irc.network))
        if not relays:
            raise ModerationError(_('%s is not relayed.') % channel)
        chanstate = irc.state.channels.get(channel)
        if not chanstate or not chanstate.isHalfopPlus(msg.nick):
            raise ModerationError(_('You must be a channel operator in %s to use this.') % channel)
        return channel, relays

    def _targetChannels(self, relays, network):
        """Returns the (Irc, channel) pairs of the given relays on network ('*' for all)."""
        targets = []
        for name in relays:
            for channelpair in self.db.get(name, ()):
                chan, net = channelpair.split('@', 1)
                if network != '*' and net != network.lower():
                    continue
                otherIrc = world.getIrc(net)
                if otherIrc and (otherIrc, chan) not in targets:
                    targets.append((otherIrc, chan))
        return targets

    @staticmethod
    def _isProtected(otherIrc, channel, nick):
        """Users who can moderate (halfop or higher), and the bot itself, can't be targeted."""
        chanstate = otherIrc.state.channels[channel]
        return nick == otherIrc.nick or chanstate.isHalfopPlus(nick)

    def _matches(self, otherIrc, channel, mask):
        """Returns the nicks in channel whose hostmask matches mask."""
        nicks = []
        for nick in otherIrc.state.channels[channel].users:
            try:
                hostmask = otherIrc.state.nickToHostmask(nick)
            except KeyError:
                continue
            if ircutils.hostmaskPatternEqual(mask, hostmask):
                nicks.append(nick)
        return nicks

    def _makeMask(self, otherIrc, channel, nick):
        try:
            hostmask = otherIrc.state.nickToHostmask(nick)
        except KeyError:
            return None
        n, ident, host = ircutils.splitHostmask(hostmask)
        try:
            account = otherIrc.state.nickToAccount(nick) or ''
        except KeyError:
            account = ''
        template = self.registryValue('moderation.banmask', channel=channel, network=otherIrc.network)
        if '$account' in template and not account:
            return None
        return string.Template(template).safe_substitute(nick=n, ident=ident, host=host,
                                                         account=account)

    def _announce(self, relays, text):
        """Sends text to every channel of the given relays (without relaying it again)."""
        sent = set()
        for name in relays:
            for channelpair in self.db.get(name, ()):
                chan, net = channelpair.split('@', 1)
                otherIrc = world.getIrc(net)
                if not otherIrc or (net, chan) in sent:
                    continue
                if not self.registryValue('moderation.announce', channel=chan, network=net):
                    continue
                chanstate = otherIrc.state.channels.get(chan)
                if chanstate and otherIrc.nick in chanstate.users:
                    out = ircmsgs.privmsg(chan, '*** %s' % text)
                    out.tag('relayedMsg')
                    otherIrc.queueMsg(out)
                    sent.add((net, chan))

    def _caller(self, irc, msg):
        return {'nick': msg.nick, 'network': irc.network, 'hostmask': msg.prefix}

    def _setBan(self, otherIrc, channel, ban):
        """Sets +b for ban on channel, and schedules its lift for permanent bans."""
        key = '%s@%s' % (channel, otherIrc.network.lower())
        state = ban['channels'].setdefault(key, {'set': False, 'set_at': None})
        if not state['set']:
            otherIrc.queueMsg(ircmsgs.ban(channel, ban['mask']))
            state['set'] = True
            state['set_at'] = time.time()
        if ban['expires'] is None:
            liftAfter = self.registryValue('moderation.liftAfter', channel=channel, network=otherIrc.network)
            if liftAfter:
                self._schedule('Interlink-lift-%s-%s' % (ban['id'], key), state['set_at'] + liftAfter,
                               self._liftBan, ban['id'], key)

    def _schedule(self, name, when, f, *args):
        try:
            schedule.removeEvent(name)
        except KeyError:
            pass
        schedule.addEvent(f, max(when, time.time() + 1), name, args=list(args))

    def _unschedule(self, ban):
        for name in list(schedule.schedule.events):
            if isinstance(name, str) and (name.startswith('Interlink-lift-%s-' % ban['id']) or
                                          name == 'Interlink-expire-%s' % ban['id']):
                schedule.removeEvent(name)

    def _removeBanFromChannels(self, ban):
        for key, state in ban['channels'].items():
            if not state['set']:
                continue
            chan, net = key.split('@', 1)
            otherIrc = world.getIrc(net)
            if otherIrc:
                otherIrc.queueMsg(ircmsgs.unban(chan, ban['mask']))
            state['set'] = False

    def _liftBan(self, banid, key):
        """Removes the +b of a permanent ban after moderation.liftAfter; it stays enforced."""
        with self.banLock:
            ban = self.bandb['bans'].get(str(banid))
            if not ban or not ban['active'] or not ban['channels'].get(key, {}).get('set'):
                return
            chan, net = key.split('@', 1)
            otherIrc = world.getIrc(net)
            if otherIrc is None:  # Not connected now; try again later
                self._schedule('Interlink-lift-%s-%s' % (banid, key), time.time() + 60, self._liftBan, banid, key)
                return
            otherIrc.queueMsg(ircmsgs.unban(chan, ban['mask']))
            ban['channels'][key]['set'] = False
            self.saveBans()

    def _expireBan(self, banid):
        with self.banLock:
            ban = self.bandb['bans'].get(str(banid))
            if not ban or not ban['active']:
                return
            # (any() is shadowed by the supybot.commands converter of the same name)
            offline = [key for key, st in ban['channels'].items()
                       if st['set'] and not world.getIrc(key.split('@', 1)[1])]
            if offline:
                self._schedule('Interlink-expire-%s' % banid, time.time() + 60, self._expireBan, banid)
                return
            self._removeBanFromChannels(ban)
            ban['active'] = False
            ban['removed_by'] = 'expired'
            ban['removed_at'] = time.time()
            self._unschedule(ban)
            self.saveBans()
            self.log.info('Interlink: ban #%s (%s) expired', banid, ban['mask'])

    def scheduleBanEvents(self):
        """Reschedules lifts and expiries after a restart."""
        with self.banLock:
            for ban in self._activeBans():
                if ban['expires'] is not None:
                    self._schedule('Interlink-expire-%s' % ban['id'], ban['expires'], self._expireBan, ban['id'])
                else:
                    for key, state in ban['channels'].items():
                        if state['set']:
                            chan, net = key.split('@', 1)
                            liftAfter = self.registryValue('moderation.liftAfter', channel=chan, network=net)
                            if liftAfter:
                                self._schedule('Interlink-lift-%s-%s' % (ban['id'], key),
                                               state['set_at'] + liftAfter, self._liftBan, ban['id'], key)

    def _enforce(self, otherIrc, channel, ban, nicks):
        """Bans (if needed) and kicks the given nicks from channel for ban."""
        chanstate = otherIrc.state.channels.get(channel)
        if not chanstate or not chanstate.isHalfopPlus(otherIrc.nick):
            self.log.warning('Interlink: cannot enforce ban #%s on %s@%s: not opped there',
                             ban['id'], channel, otherIrc.network)
            return False
        nicks = [nick for nick in nicks if not self._isProtected(otherIrc, channel, nick)]
        if not nicks:
            return True
        self._setBan(otherIrc, channel, ban)
        for nick in nicks:
            otherIrc.queueMsg(ircmsgs.kick(channel, nick, ban['kick_reason']))
        return True

    def _bansFor(self, otherIrc, channel):
        """Returns the active bans that apply to channel on otherIrc's network."""
        channelpair = '%s@%s' % (channel, otherIrc.network.lower())
        relays = self._relaysOf(channelpair)
        return [ban for ban in self._activeBans()
                if ban['relay'] in relays and ban['network'] in ('*', otherIrc.network.lower())]

    def _checkUser(self, otherIrc, channel, nick):
        """Kicks (and bans again) nick from channel if it matches a stored ban."""
        if nick == otherIrc.nick:
            return
        try:
            hostmask = otherIrc.state.nickToHostmask(nick)
        except KeyError:
            return
        with self.banLock:
            for ban in self._bansFor(otherIrc, channel):
                if ircutils.hostmaskPatternEqual(ban['mask'], hostmask):
                    was_set = ban['channels'].get('%s@%s' % (channel, otherIrc.network.lower()), {}).get('set')
                    if self._enforce(otherIrc, channel, ban, [nick]):
                        self.saveBans()
                        if not was_set:
                            self._announce([ban['relay']], _('%s (%s) matched ban #%s on %s and was banned again.') %
                                           (nick, ban['mask'], ban['id'], otherIrc.network))
                    return

    def _scanChannel(self, otherIrc, channel):
        with self.banLock:
            for ban in self._bansFor(otherIrc, channel):
                nicks = self._matches(otherIrc, channel, ban['mask'])
                if nicks and self._enforce(otherIrc, channel, ban, nicks):
                    self.saveBans()

    def doJoin(self, irc, msg):
        self.doNonPrivmsg(irc, msg)
        if msg.nick != irc.nick:
            for channel in msg.args[0].split(','):
                self._checkUser(irc, channel, msg.nick)

    def doNick(self, irc, msg):
        self._relayNick(irc, msg)
        for channel in msg.tagged('channels') or ():
            self._checkUser(irc, channel, msg.args[0])

    def doChghost(self, irc, msg):
        for channel, chanstate in irc.state.channels.items():
            if msg.nick in chanstate.users:
                self._checkUser(irc, channel, msg.nick)

    def do315(self, irc, msg):
        """End of WHO: check the channel against mask bans (after we join, or a new mask ban)."""
        channel = msg.args[1]
        if irc.isChannel(channel):
            self.pendingScans.discard((irc.network, channel))
            self._scanChannel(irc, channel)

    def doMode(self, irc, msg):
        self.doNonPrivmsg(irc, msg)
        channel = msg.args[0]
        if not irc.isChannel(channel) or msg.nick == irc.nick:
            return
        key = '%s@%s' % (channel, irc.network.lower())
        with self.banLock:
            changed = False
            for (mode, arg) in ircutils.separateModes(msg.args[1:]):
                if mode != '-b' or not arg:
                    continue
                for ban in self._bansFor(irc, channel):
                    state = ban['channels'].get(key)
                    if not state or not state['set'] or not ircutils.strEqual(ban['mask'], arg):
                        continue
                    # A local op removed the ban: respect it.
                    state['set'] = False
                    changed = True
                    try:
                        schedule.removeEvent('Interlink-lift-%s-%s' % (ban['id'], key))
                    except KeyError:
                        pass
                    if ban['expires'] is None:
                        irc.queueMsg(ircmsgs.notice(msg.nick, _(
                            'You removed %s on %s. It is still a permanent ban on %s and will be set '
                            'again when a matching user joins; to delete it there, type: unban %s') %
                            (ban['mask'], channel, irc.nick, ban['id'])))
                    else:
                        ban['active'] = False
                        ban['removed_by'] = '%s@%s (-b)' % (msg.nick, irc.network)
                        ban['removed_at'] = time.time()
                        self._unschedule(ban)
            if changed:
                self.saveBans()

    def _addBan(self, irc, msg, relays, network, target, reason, duration, force):
        """Shared code of ban and tban."""
        network = network.lower()
        targets = self._targetChannels(relays, network)
        if not targets:
            raise ModerationError(_('The relay has no channel on %s (or I am not connected there).') % network)

        is_mask = '!' in target or '@' in target or '*' in target
        if not is_mask and network == '*':
            raise ModerationError(_('--global only works with masks; to ban a nick, give its network.'))

        if is_mask:
            if not ircutils.isUserHostmask(target):
                raise ModerationError(_('%s is not a valid nick!user@host mask.') % target)
            if network == '*' and ircutils.splitHostmask(target)[2] != '*':
                # Hosts (cloaks, vhosts) differ between networks; nicks and idents don't.
                raise ModerationError(_("%s only matches one network's hosts; give the network instead "
                                        "of --global (global bans match on nick or ident, e.g. "
                                        "spambot*!*@*).") % target)
            mask = target
            nick = None
        else:
            otherIrc, chan = targets[0]
            if target not in otherIrc.state.channels[chan].users:
                raise ModerationError(_('%s is not in %s on %s.') % (target, chan, otherIrc.network))
            if self._isProtected(otherIrc, chan, target):
                raise ModerationError(_('%s is a channel operator in %s on %s.') % (target, chan, otherIrc.network))
            mask = self._makeMask(otherIrc, chan, target)
            if mask is None:
                raise ModerationError(_("I don't know enough about %s to build a ban mask.") % target)
            nick = target

        matched = []
        for otherIrc, chan in targets:
            chanstate = otherIrc.state.channels.get(chan)
            if not chanstate or not chanstate.isHalfopPlus(otherIrc.nick):
                raise ModerationError(_("I'm not a channel operator in %s on %s.") % (chan, otherIrc.network))
            nicks = self._matches(otherIrc, chan, mask)
            protected = [n for n in nicks if self._isProtected(otherIrc, chan, n)]
            if protected:
                raise ModerationError(_('%s would match channel operators in %s on %s: %s') %
                          (mask, chan, otherIrc.network, ', '.join(protected)))
            maxMatches = self.registryValue('moderation.maxMatches', channel=chan, network=otherIrc.network)
            if len(nicks) > maxMatches and not force:
                raise ModerationError(_("%s would match %d users in %s on %s; that's too broad. Give a narrower "
                            "mask, or use --force.") % (mask, len(nicks), chan, otherIrc.network))
            matched.append((otherIrc, chan, nicks))

        setter = '%s@%s' % (msg.nick, irc.network)
        reason = reason or _('No reason given')
        with self.banLock:
            banid = self.bandb['next_id']
            self.bandb['next_id'] += 1
            now = time.time()
            ban = {'id': banid, 'relay': relays[0], 'network': network, 'mask': mask, 'nick': nick,
                   'reason': reason, 'kick_reason': _('Banned by %s: %s') % (setter, reason),
                   'setter': self._caller(irc, msg), 'time': now,
                   'expires': now + duration if duration else None,
                   'active': True, 'removed_by': None, 'removed_at': None, 'channels': {}}
            self.bandb['bans'][str(banid)] = ban
            for otherIrc, chan, nicks in matched:
                self._setBan(otherIrc, chan, ban)
                for n in nicks:
                    otherIrc.queueMsg(ircmsgs.kick(chan, n, ban['kick_reason']))
                if is_mask:
                    # Refresh our view of the channel; do315 kicks anyone else matching.
                    self.pendingScans.add((otherIrc.network, chan))
                    otherIrc.queueMsg(ircmsgs.who(chan, args=('%tuhnairf,1',)))
            if duration:
                self._schedule('Interlink-expire-%s' % banid, ban['expires'], self._expireBan, banid)
            self.saveBans()

        where = _('all networks') if network == '*' else network
        what = '%s (%s)' % (nick, mask) if nick else mask
        length = (_(' for %s') % self._formatDuration(duration)) if duration else ''
        self._announce(relays, _('%s banned %s on %s%s: %s [#%s]') % (setter, what, where, length, reason, banid))

    @staticmethod
    def _banArgs(opts, first, rest):
        """Splits the arguments of ban and tban into (network, target, rest of the line). With
        --global the network is '*' and is not given."""
        if 'global' in opts:
            return '*', first, rest
        if first == '*':
            raise ModerationError(_('Use --global to ban on every network.'))
        words = (rest or '').split(None, 1)
        if not words:
            raise ModerationError(_('Give a nick or mask to ban.'))
        return first, words[0], words[1] if len(words) > 1 else None

    @wrap([getopts({'force': '', 'global': ''}), 'something', additional('text')])
    @private_errors
    def ban(self, irc, msg, args, opts, first, rest):
        """[--force] <network> <nick|mask> [<reason>] | --global [--force] <mask> [<reason>]

        Bans <nick> (using the moderation.banmask setting) or <mask> from the relay's channel on
        <network>, and kicks the matching users. With --global, <mask> is banned on every network
        of the relay; since hosts differ between networks, global masks must match on nick or
        ident only (e.g. spambot*!*@*). The ban is permanent: its +b is removed after
        moderation.liftAfter, but it is set again whenever a matching user joins, until it is
        removed with unban. Masks that would match more than moderation.maxMatches users are
        refused unless --force is given. Channel operators of a relayed channel can use this;
        channel operators elsewhere can't be banned."""
        channel, relays = self._context(irc, msg)
        opts = dict(opts)
        network, target, reason = self._banArgs(opts, first, rest)
        self._addBan(irc, msg, relays, network, target, reason, None, 'force' in opts)

    @wrap([getopts({'force': '', 'global': ''}), 'something', additional('text')])
    @private_errors
    def tban(self, irc, msg, args, opts, first, rest):
        """[--force] <network> <nick|mask> [<duration>] [<reason>] | --global [--force] <mask> [<duration>] [<reason>]

        Like ban, but the ban is lifted after <duration> (e.g. 30m, 2h, 1d; defaults to
        moderation.tbanDuration, 1h) and never set again."""
        channel, relays = self._context(irc, msg)
        opts = dict(opts)
        network, target, text = self._banArgs(opts, first, rest)
        words = (text or '').split(None, 1)
        duration = self._parseDuration(words[0]) if words else None
        if duration:
            reason = words[1] if len(words) > 1 else None
        else:
            duration = self.registryValue('moderation.tbanDuration', channel=channel, network=irc.network)
            reason = text
        self._addBan(irc, msg, relays, network, target, reason, duration, 'force' in opts)

    @wrap(['something', 'nick', additional('text')])
    @private_errors
    def kick(self, irc, msg, args, network, nick, reason):
        """<network> <nick> [<reason>]

        Kicks <nick> from the relay's channel on <network>."""
        channel, relays = self._context(irc, msg)
        targets = self._targetChannels(relays, network)
        if network == '*' or not targets:
            raise ModerationError(_('The relay has no channel on %s (or I am not connected there).') % network)
        otherIrc, chan = targets[0]
        chanstate = otherIrc.state.channels.get(chan)
        if not chanstate or nick not in chanstate.users:
            raise ModerationError(_('%s is not in %s on %s.') % (nick, chan, otherIrc.network))
        if self._isProtected(otherIrc, chan, nick):
            raise ModerationError(_('%s is a channel operator in %s on %s.') % (nick, chan, otherIrc.network))
        if not chanstate.isHalfopPlus(otherIrc.nick):
            raise ModerationError(_("I'm not a channel operator in %s on %s.") % (chan, otherIrc.network))
        setter = '%s@%s' % (msg.nick, irc.network)
        reason = reason or _('No reason given')
        otherIrc.queueMsg(ircmsgs.kick(chan, nick, _('Kicked by %s: %s') % (setter, reason)))
        self._announce(relays, _('%s kicked %s from %s on %s: %s') % (setter, nick, chan, otherIrc.network, reason))

    @wrap([getopts({'global': ''}), 'something', additional('something')])
    @private_errors
    def unban(self, irc, msg, args, opts, first, second):
        """<id> | <network> <nick|mask> | --global <mask>

        Removes a ban set with ban or tban, given by its id (see bans), by the network and the
        banned nick or mask, or by the mask of a global ban, and removes its +b."""
        channel, relays = self._context(irc, msg)
        with self.banLock:
            candidates = [ban for ban in self._activeBans() if ban['relay'] in relays]
            if 'global' in dict(opts):
                found = [ban for ban in candidates if ban['network'] == '*' and
                         ircutils.strEqual(ban['mask'], first)]
            elif second is None:
                found = [ban for ban in candidates if str(ban['id']) == first.lstrip('#')]
            else:
                network = first.lower()
                found = [ban for ban in candidates if ban['network'] == network and
                         (ircutils.strEqual(ban['mask'], second) or
                          (ban['nick'] and ircutils.strEqual(ban['nick'], second)))]
            if not found:
                raise ModerationError(_('No such ban.'))
            setter = '%s@%s' % (msg.nick, irc.network)
            for ban in found:
                self._removeBanFromChannels(ban)
                ban['active'] = False
                ban['removed_by'] = setter
                ban['removed_at'] = time.time()
                self._unschedule(ban)
            self.saveBans()
        for ban in found:
            where = _('all networks') if ban['network'] == '*' else ban['network']
            self._announce(relays, _('%s removed ban #%s (%s) on %s') % (setter, ban['id'], ban['mask'], where))

    @wrap([additional('something')])
    @private_errors
    def bans(self, irc, msg, args, network):
        """[<network>]

        Lists the active bans of this channel's relay, optionally only those on <network>."""
        channel, relays = self._context(irc, msg)
        found = [ban for ban in self._activeBans() if ban['relay'] in relays and
                 (network is None or ban['network'] in (network.lower(), '*'))]
        if not found:
            irc.reply(_('No active bans.'), private=True, notice=True)
            return
        for ban in sorted(found, key=lambda b: b['id']):
            irc.reply(self._describe(ban), private=True, notice=True)

    @wrap(['something'])
    @private_errors
    def baninfo(self, irc, msg, args, which):
        """<id|mask>

        Shows the details of a ban, including removed ones."""
        channel, relays = self._context(irc, msg)
        found = [ban for ban in self.bandb['bans'].values() if ban['relay'] in relays and
                 (str(ban['id']) == which.lstrip('#') or ircutils.strEqual(ban['mask'], which))]
        if not found:
            raise ModerationError(_('No such ban.'))
        for ban in sorted(found, key=lambda b: b['id']):
            irc.reply(self._describe(ban, full=True), private=True, notice=True)

    def _describe(self, ban, full=False):
        where = _('all networks') if ban['network'] == '*' else ban['network']
        when = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ban['time']))
        setter = '%s@%s' % (ban['setter']['nick'], ban['setter']['network'])
        if ban['expires']:
            kind = _('expires %s') % time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ban['expires']))
        else:
            kind = _('permanent')
        s = '#%s %s on %s (%s), by %s on %s: %s' % (ban['id'], ban['mask'], where, kind, setter, when, ban['reason'])
        if full:
            s += ' [%s]' % ban['setter']['hostmask']
            if not ban['active']:
                s += _('; removed by %s on %s') % (ban['removed_by'], time.strftime(
                    '%Y-%m-%d %H:%M UTC', time.gmtime(ban['removed_at'])))
        return s


Class = Interlink


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
