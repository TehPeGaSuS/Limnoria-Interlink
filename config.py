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

import supybot.conf as conf
import supybot.registry as registry
try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Interlink')
except:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x


def configure(advanced):
    # This will be called by supybot to configure this module.  advanced is
    # a bool that specifies whether the user identified themself as an advanced
    # user or not.  You should effect your configuration by manipulating the
    # registry as appropriate.
    from supybot.questions import expect, anything, something, yn
    conf.registerPlugin('Interlink', True)


Interlink = conf.registerPlugin('Interlink')

conf.registerChannelValue(Interlink, 'color',
    registry.Boolean(True, _("""Determines whether the bot will color networks and nicks when
    relaying messages.""")))
conf.registerChannelValue(Interlink, 'hostmasks',
    registry.Boolean(True, _("""Determines whether the bot will relay the ident@host of people
    that join or part a channel.""")))
conf.registerChannelValue(Interlink, 'noHighlight',
    registry.Boolean(False, _("""Determines whether the bot should prefix nicks with zero-width
    (ZWS) to prevent them from being highlighted / mentioned by the relayer.""")))
conf.registerChannelValue(Interlink, 'showPrefixes',
    registry.Boolean(False, _("""Determines whether the bot should show status prefixes
    (@, %, +) when relaying.""")))
conf.registerChannelValue(Interlink, 'ignoreRegexp',
    registry.Regexp(None, _("""If configured, text, part, and quit messages matching this regexp
                            will not be relayed. This may be useful for blocking spam.""")))

conf.registerGroup(Interlink, 'antiflood')
conf.registerChannelValue(Interlink.antiflood, 'enable',
    registry.Boolean(False, _("""Determines whether flood prevention should be enabled
    for the given channel.""")))
conf.registerChannelValue(Interlink.antiflood, 'seconds',
    registry.PositiveInteger(20, _("""Determines how many seconds messages should be queued
        for flood protection.""")))
conf.registerChannelValue(Interlink.antiflood, 'maximum',
    registry.PositiveInteger(15, _("""Determines the maximum amount of incoming messages
        the bot will allow from a relay channel before flood protection is triggered.""")))
conf.registerChannelValue(Interlink.antiflood.maximum, 'nonPrivmsgs',
    registry.PositiveInteger(10, _("""Determines the maximum amount of incoming non-PRIVMSG events
        the bot will allow from a relay channel before flood protection is triggered.""")))
conf.registerChannelValue(Interlink.antiflood, 'timeout',
    registry.PositiveInteger(60, _("""Determines the amount of time in seconds the bot should
        block messages when flood protection is triggered.""")))

conf.registerGroup(Interlink, 'events')

_events = {'quit': True, 'join': True, 'part': True,
           'nick': True, 'mode': True, 'kick': True,
           'topic': False}
for ev in _events:
    conf.registerChannelValue(Interlink.events, 'relay%ss' % ev,
        registry.Boolean(_events[ev], """Determines whether the bot should relay %ss.""" % ev))
conf.registerChannelValue(Interlink.events, 'userIgnored',
    registry.SpaceSeparatedListOfStrings(['PRIVMSG', 'MODE'], ("""Determines what events
        the relay should ignore from ignored users. Ignores are added using
        Limnoria's global ignore system.""")))
conf.registerChannelValue(Interlink.events, 'relaySelfMessages',
    registry.Boolean(True, ("""Determines whether the bot should relay its own messages.
        You may wish to disable this if you are running plugins that announce to the same channel
        on multiple networks (e.g. RSS).""")))

conf.registerGroup(Interlink, 'moderation')
conf.registerChannelValue(Interlink.moderation, 'banmask',
    registry.String('*!*@$host', _("""Determines the ban mask used when a nick is banned with
        the ban and tban commands. $nick, $ident, $host and $account are replaced with the
        target's details. This is read for the channel and network the ban is set on.""")))
conf.registerChannelValue(Interlink.moderation, 'maxMatches',
    registry.PositiveInteger(1, _("""Determines how many users in the target channel a ban
        may match. Bans matching more are refused as too broad, unless --force is given.""")))
conf.registerChannelValue(Interlink.moderation, 'liftAfter',
    registry.NonNegativeInteger(3600, _("""Determines after how many seconds the +b of a
        permanent ban is removed from the channel, to keep its ban list short. The ban stays
        in Interlink's database and is set again whenever a matching user joins, until it is
        removed with the unban command. 0 keeps the +b set.""")))
conf.registerChannelValue(Interlink.moderation, 'tbanDuration',
    registry.PositiveInteger(3600, _("""Determines how long, in seconds, a tban lasts when no
        duration is given.""")))
conf.registerChannelValue(Interlink.moderation, 'announce',
    registry.Boolean(True, _("""Determines whether bans, kicks and unbans are announced in
        every channel of the relay.""")))
# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
