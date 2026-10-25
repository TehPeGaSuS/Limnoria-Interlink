###
# Copyright (c) 2015,2021 James Lu
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
import random
import re
import string
import sys
import time
import typing
import uuid

from supybot.test import *

class InterlinkDBTestCase(PluginTestCase):
    plugins = ('Interlink',)

    def testAdd(self):
        self.assertNotError("interlink add test #channel1@somenet #channel2@othernet")
        self.assertRegexp("interlink list", "#channel1@somenet")
        self.assertRegexp("interlink list", "#channel2@othernet")
        # Incorrect amount of arguments
        self.assertError("interlink add test")
        self.assertError("interlink add test afdasfader agkajeoig #validchannel@othernet")
        # needs at least 2 channels
        self.assertError("interlink add nonexistant-relay #channel1@somenet")

    def testSet(self):
        self.assertNotError("interlink set test #channel1@somenet #channel2@othernet")
        self.assertRegexp("interlink list", "#channel1@somenet")
        self.assertRegexp("interlink list", "#channel2@othernet")
        self.assertError("interlink set test")
        self.assertError("interlink set test #channel1@somenet")

    def testList(self):
        # This should error if no relays are defined
        self.assertError("interlink list")
        self.assertNotError("interlink set abcd #moo@moo #test@test")
        self.assertRegexp("interlink list", "#moo@moo")
        self.assertRegexp("interlink list", "#test@test")

    def testSetCaseInsensitive(self):
        self.assertError("interlink set foobar #deV@overdrive-irc #DEV@overdrive-irc")
        self.assertError("interlink set foobar #dev@overdrive-irc #DEV@OVERdrive-IRC")
        self.assertError("interlink set foobar #dev@overdrive-irc #dev@OVERdrive-IRC")
        self.assertNotError("interlink set abcd #moo@moo #MOO@mOO #test@test")
        self.assertRegexp("interlink list", "#moo@moo")
        self.assertRegexp("interlink list", "#test@test")

    def testAddCaseInsensitive(self):
        self.assertError("interlink add foobar #deV@overdrive-irc #DEV@overdrive-irc")
        self.assertError("interlink add foobar #dev@overdrive-irc #DEV@OVERdrive-IRC")
        self.assertError("interlink add foobar #dev@overdrive-irc #dev@OVERdrive-IRC")
        self.assertNotError("interlink add abcd #moo@moo #MOO@mOO #test@test")
        self.assertRegexp("interlink list", "#moo@moo")
        self.assertRegexp("interlink list", "#test@test")

    def testRemoveEntireRelay(self):
        self.assertError("interlink remove some-relay")
        self.assertNotError("interlink add some-relay #channel1@somenet #channel2@othernet #abcd@test123")
        # 'remove' without arguments removes an entire relay
        self.assertNotError("interlink remove some-relay")
        # No relays should be defined now.
        self.assertError("interlink list")

    def testRemoveChannelsFromExistingRelay(self):
        self.assertNotError("interlink add some-relay #channel1@somenet #channel2@othernet #abcd@test123")
        # This should give a warning (removing a channel that's not in the specified relay)
        self.assertRegexp("interlink remove some-relay #asfdafaergea@random",
                          'not found in the original relay: '
                          '#asfdafaergea@random')
        self.assertNotError("interlink remove some-relay #abcd@test123")
        self.assertRegexp("interlink list", "#channel1@somenet")
        self.assertRegexp("interlink list", "#channel2@othernet")
        self.assertNotRegexp("interlink list", "#abcd@test123")

    def testAutoremoveWhenLessThanTwoChannels(self):
        self.assertNotError("interlink add some-relay #channel1@somenet #channel2@othernet #abcd@test123")
        self.assertNotError("interlink remove some-relay #abcd@test123 #channel2@othernet")
        self.assertError("interlink list")

    def testAddChannelsToExistingRelay(self):
        self.assertNotError("interlink add test #channel1@somenet #channel2@othernet")
        self.assertNotError("interlink add test #somewhereElse@mynet")
        self.assertRegexp("interlink list", "#channel1@somenet")
        self.assertRegexp("interlink list", "#channel2@othernet")
        self.assertRegexp("interlink list", "#somewhereelse@mynet")

    def testClear(self):
        self.assertNotError("interlink set test #channel1@somenet #channel2@othernet")
        self.assertNotError("interlink clear")
        self.assertError("interlink list")


class InterlinkTwoNetworks(PluginTestCase):
    """Two test networks with a relay between a channel on each."""
    plugins = ('Interlink', 'Admin', 'Utilities')
    # Disable colours to make output checking more predictable
    config = {'plugins.Interlink.color': False}
    timeout = 3

    @staticmethod
    def randomString(length=12):
        return ''.join(random.choice(string.ascii_letters) for _ in range(length))

    @staticmethod
    def _createChannel(irc, channelname):
        """
        Prepares a channel's state for Relay.
        """
        channel = irclib.ChannelState()
        # Add the bot to the channel. The bot needs to be in both ends of a relay for it to relay messages.
        channel.addUser(irc.nick)
        irc.state.channels[channelname] = channel
        return channel

    def setUp(self):
        super().setUp()
        # These test network names are defined in scripts/supybot-test. Anything unknown will fail with:
        #     supybot.registry.NonExistentRegistryEntry: 'net1' is not a valid entry in 'supybot.networks'
        self.irc1 = getTestIrc("testnet1")
        self.irc1.nick = 'supy-' + self.randomString(7)
        self.chan1name = '#' + uuid.uuid4().hex
        self.chan1 = self._createChannel(self.irc1, self.chan1name)

        self.irc2 = getTestIrc("testnet2")
        self.irc2.nick = 'relay-' + self.randomString(6)
        self.chan2name = '#' + uuid.uuid4().hex
        self.chan2 = self._createChannel(self.irc2, self.chan2name)
        self.assertNotError("interlink set testRelay %s@testnet1 %s@testnet2" % (self.chan1name, self.chan2name))

    def getCommandResponse(self, irc):
        # Adapted from PluginTestCase._feedMsg() to account for other irc objects
        response = irc.takeMsg()
        fed = time.time()
        while response is None and time.time() - fed < self.timeout:
            time.sleep(0.05)
            drivers.run()
            response = irc.takeMsg()
        return response


class InterlinkTestCase(InterlinkTwoNetworks):

    ### Events
    def testPrivmsg(self):
        msg = ircmsgs.privmsg(self.chan1name, "hello world", prefix='abc!def@ghi.jkl')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 <abc> hello world', output.args[1])

    def testLongLinesAreSplit(self):
        # https://github.com/jlu5/Limnoria-RelayNext/issues/5
        words = ['word%03d' % n for n in range(150)]  # about 1200 bytes
        self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, ' '.join(words), prefix='longtalker!~lt@talk.example'))
        lines = []
        while True:
            output = self.getCommandResponse(self.irc2)
            if output is None:
                break
            lines.append(output.args[1])
        self.assertGreater(len(lines), 1, lines)
        nick, ident, host = ircutils.splitHostmask(self.irc2.prefix)
        prefix = '%s!%s@%s' % (self.irc2.nick, ident, 'x' * 64)  # the host the bot assumes for itself
        for line in lines:
            self.assertTrue(line.startswith('\x02[testnet1]\x02 <longtalker> '), line)
            raw = ':%s PRIVMSG %s :%s\r\n' % (prefix, self.chan2name, line)
            self.assertLessEqual(len(raw.encode('utf-8')), 512)
        rebuilt = ' '.join(line[len('\x02[testnet1]\x02 <longtalker> '):] for line in lines)
        self.assertEqual(rebuilt, ' '.join(words))

    def testLongMultibyteWordIsSplit(self):
        word = '\u00e9' * 400  # 800 bytes with no spaces
        self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, word, prefix='longtalker!~lt@talk.example'))
        lines = []
        while True:
            output = self.getCommandResponse(self.irc2)
            if output is None:
                break
            lines.append(output.args[1])
        self.assertGreater(len(lines), 1, lines)
        self.assertEqual(''.join(l[len('\x02[testnet1]\x02 <longtalker> '):] for l in lines), word)

    def drain(self, irc):
        lines = []
        while True:
            output = self.getCommandResponse(irc)
            if output is None:
                return lines
            lines.append(output)

    def channelValue(self, name, network, channel):
        value = conf.supybot.plugins.interlink
        for part in name.split('.'):
            value = value.get(part)
        return value.getSpecific(network=network, channel=channel, fallback_to_channel=False)

    ### Formats
    def testCustomFormat(self):
        with conf.supybot.plugins.interlink.format.prefix.context('[$network] '), \
                conf.supybot.plugins.interlink.format.privmsg.context('$nick: $text (in $channel)'):
            # Variables inside the relayed text are left alone
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, 'hello $nick $channel', prefix='abc!def@ghi.jkl'))
            output = self.getCommandResponse(self.irc2)
            self.assertEqual('[testnet1] abc: hello $nick $channel (in %s)' % self.chan1name, output.args[1])

    def testCustomFormatWithoutText(self):
        with conf.supybot.plugins.interlink.format.privmsg.context('<$nick> said something'):
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, 'secret', prefix='abc!def@ghi.jkl'))
            output = self.getCommandResponse(self.irc2)
            self.assertEqual('\x02[testnet1]\x02 <abc> said something', output.args[1])

    def testSplitKeepsTextAfterTheMessage(self):
        words = ['word%03d' % n for n in range(150)]
        with conf.supybot.plugins.interlink.format.privmsg.context('<$nick> $text [$network]'):
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, ' '.join(words), prefix='longtalker!~lt@talk.example'))
            lines = [m.args[1] for m in self.drain(self.irc2)]
        self.assertGreater(len(lines), 1, lines)
        head, tail = '\x02[testnet1]\x02 <longtalker> ', ' [testnet1]'
        for line in lines:
            self.assertTrue(line.startswith(head) and line.endswith(tail), line)
        # Words are never cut
        rebuilt = [line[len(head):-len(tail)].split(' ') for line in lines]
        self.assertEqual(sum(rebuilt, []), words)

    ### Directions
    def testSendOnlyChannel(self):
        with self.channelValue('direction', 'testnet2', self.chan2name).context('send'):
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, 'not shown', prefix='abc!def@ghi.jkl'))
            self.assertIsNone(self.getCommandResponse(self.irc2))
            self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'shown', prefix='abc!def@ghi.jkl'))
            output = self.getCommandResponse(self.irc1)
            self.assertEqual('\x02[testnet2]\x02 <abc> shown', output.args[1])

    def testReceiveOnlyChannel(self):
        with self.channelValue('direction', 'testnet2', self.chan2name).context('receive'):
            self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'not shown', prefix='abc!def@ghi.jkl'))
            self.assertIsNone(self.getCommandResponse(self.irc1))
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, 'shown', prefix='abc!def@ghi.jkl'))
            output = self.getCommandResponse(self.irc2)
            self.assertEqual('\x02[testnet1]\x02 <abc> shown', output.args[1])

    ### Topic sync
    def testTopicSync(self):
        with conf.supybot.plugins.interlink.topicSync.context(True):
            self.irc1.feedMsg(ircmsgs.topic(self.chan1name, 'New topic', prefix='user5!~test@user/test'))
            output = self.getCommandResponse(self.irc2)
            self.assertEqual(('TOPIC', (self.chan2name, 'New topic')), (output.command, output.args))
            # Services putting their own topic back is not copied back
            self.irc2.feedMsg(ircmsgs.topic(self.chan2name, 'Locked topic', prefix='ChanServ!services@services.example'))
            self.assertIsNone(self.getCommandResponse(self.irc1))

    def testTopicSyncNeedsBothChannels(self):
        with self.channelValue('topicSync', 'testnet1', self.chan1name).context(True):
            self.irc1.feedMsg(ircmsgs.topic(self.chan1name, 'New topic', prefix='user5!~test@user/test'))
            self.assertIsNone(self.getCommandResponse(self.irc2))

    def testTopicSyncSkipsSameTopic(self):
        self.chan2.topic = 'Same topic'
        with conf.supybot.plugins.interlink.topicSync.context(True):
            self.irc1.feedMsg(ircmsgs.topic(self.chan1name, 'Same topic', prefix='user5!~test@user/test'))
            self.assertIsNone(self.getCommandResponse(self.irc2))

    ### Relay index
    def testChannelInTwoRelaysIsRelayedOnce(self):
        self._createChannel(self.irc2, '#third')
        self.assertNotError("interlink add otherRelay %s@testnet1 %s@testnet2 #third@testnet2"
                            % (self.chan1name, self.chan2name))
        self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, 'hello', prefix='abc!def@ghi.jkl'))
        targets = sorted(m.args[0] for m in self.drain(self.irc2))
        self.assertEqual(targets, sorted([self.chan2name, '#third']))

    def testRemovedRelayStopsRelaying(self):
        self.assertNotError("interlink remove testRelay")
        self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, 'hello', prefix='abc!def@ghi.jkl'))
        self.assertIsNone(self.getCommandResponse(self.irc2))

    def testPrivmsgAction(self):
        msg = ircmsgs.privmsg(self.chan2name, "\x01ACTION waves hello\x01", prefix='jlu5!~jlu5@user/jlu5')
        self.irc2.feedMsg(msg)  # irc2 is source
        output = self.getCommandResponse(self.irc1)
        self.assertEqual(self.chan1name, output.args[0])
        self.assertEqual('\x02[testnet2]\x02 * jlu5 waves hello', output.args[1])

    def testPrivmsgIgnoreCTCPs(self):  # irc2 is source
        self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, "\x01VERSION\x01", prefix='StatServ!StatServ@services.mytestnet.internal'))
        # Check that the first message isn't relayed
        self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, "dummy test message", prefix='StatServ!StatServ@services.mytestnet.internal'))
        output = self.getCommandResponse(self.irc1)
        self.assertEqual(self.chan1name, output.args[0])
        self.assertIn("dummy test", output.args[1])
        self.assertNotIn("VERSION", output.args[1])

    def testJoin(self):
        msg = ircmsgs.join(self.chan1name, prefix='testUser1!testuser@example.com')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 testUser1 (testuser@example.com) has joined %s' % self.chan1name, output.args[1])

    def testPartBare(self):
        msg = ircmsgs.part(self.chan1name, prefix='foo!bar@baz')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 foo (bar@baz) has left %s' % self.chan1name, output.args[1])

    def testPartWithReason(self):
        msg = ircmsgs.part(self.chan1name, 'foobar', prefix='foo!bar@baz')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 foo (bar@baz) has left %s (foobar)' % self.chan1name, output.args[1])

    def testTopic(self):
        with conf.supybot.plugins.interlink.events.relayTopics.context(True):
            msg = ircmsgs.topic(self.chan1name, 'Some topic text', prefix='user5!~test@user/test')
            self.irc1.feedMsg(msg)
            output = self.getCommandResponse(self.irc2)
            self.assertEqual(self.chan2name, output.args[0])
            self.assertEqual('\x02[testnet1]\x02 user5 set topic on %s to: Some topic text' % self.chan1name, output.args[1])

    def testKick(self):
        msg = ircmsgs.kick(self.chan1name, 'soccerball', 'some ambiguous reason', prefix='foo!bar@foobar.example')
        self.irc1.state.nicksToHostmasks['soccerball'] = 'soccerball!dummy@dummy.test.client'
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 soccerball (dummy@dummy.test.client) has been kicked from %s by foo '
                         '(some ambiguous reason)' % self.chan1name, output.args[1])

    def testMode(self):
        msg = ircmsgs.mode(self.chan1name, ('-m'), prefix='foo!bar@foobar.example')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 foo (bar@foobar.example) set mode -m on %s' % self.chan1name, output.args[1])

    def testModeWithArgs(self):
        msg = ircmsgs.mode(self.chan1name, ('+ov', 'foo', 'foo'), prefix='foo!bar@foobar.example')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        self.assertEqual('\x02[testnet1]\x02 foo (bar@foobar.example) set mode +ov foo foo on %s' % self.chan1name, output.args[1])

    def testQuit(self):
        msg = ircmsgs.quit('goodbye', prefix='Tester1234!bar@example.org')
        self.irc1.feedMsg(msg)  # Because the source is not in the channel, their message doesn't get relayed
        self.chan1.addUser('nobody')
        msg = ircmsgs.quit('Leaving', prefix='Nobody!~nobody@test.user')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        # XXX: this should probably show the hostmask for consistency?
        self.assertEqual('\x02[testnet1]\x02 Nobody has quit (Leaving)', output.args[1])

    def testNick(self):
        msg = ircmsgs.nick('OldNick', prefix='oldnick!~ident@hidden-1234abcd.example.net')
        self.irc1.feedMsg(msg)  # Because the source is not in the channel, their message doesn't get relayed
        self.chan1.addUser('OldNick')
        msg = ircmsgs.nick('newnick', prefix='OldNick!~ident@hidden-1234abcd.example.net')
        self.irc1.feedMsg(msg)
        output = self.getCommandResponse(self.irc2)
        self.assertEqual(self.chan2name, output.args[0])
        # XXX: this should probably show the hostmask for consistency?
        self.assertEqual('\x02[testnet1]\x02 OldNick is now known as newnick', output.args[1])

    ### Display options
    def testToggleEvents(self):
        with conf.supybot.plugins.interlink.events.relayJoins.context(False):
            with conf.supybot.plugins.interlink.events.relayParts.context(False):
                self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='ChanServ!ChanServ@services.testnet.internal'))
                self.irc2.feedMsg(ircmsgs.part(self.chan2name, prefix='ChanServ!ChanServ@services.testnet.internal'))
                self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'Your channel is now registered =]', prefix='ChanServ!ChanServ@services.testnet.internal'))
        output = self.getCommandResponse(self.irc1)
        self.assertEqual(self.chan1name, output.args[0])
        self.assertEqual('\x02[testnet2]\x02 <ChanServ> Your channel is now registered =]', output.args[1])

    def testRelayColorNoHostmask(self):
        with conf.supybot.plugins.interlink.color.context(True):
            with conf.supybot.plugins.interlink.hostmasks.context(False):
                # Join message
                self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='ChanServ!ChanServ@services.testnet.internal'))
                output = self.getCommandResponse(self.irc1)
                self.assertRegex(output.args[1], '\x02\\[\x03\\d{1,2}testnet2\x03\\]\x02 \x03\\d{1,2}ChanServ\x03 has joined')

                # PRIVMSG
                self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'Your channel is now registered =]', prefix='ChanServ!ChanServ@services.testnet.internal'))
                output = self.getCommandResponse(self.irc1)
                self.assertRegex(output.args[1], '\x02\\[\x03\\d{1,2}testnet2\x03\\]\x02 <\x03\\d{1,2}ChanServ\x03> Your channel is now registered =]')

                # Nick change
                self.chan2.addUser('old345')
                self.irc2.feedMsg(ircmsgs.nick('new123', prefix='old345!old345@Clk-12345678.lan'))
                output = self.getCommandResponse(self.irc1)
                self.assertRegex(output.args[1], '\x03\\d{1,2}old345\\x03 is now known as \x03\\d{1,2}new123\\x03')

    def testBlockHighlightsSimple(self):
        with conf.supybot.plugins.interlink.noHighlight.context(True):
            with conf.supybot.plugins.interlink.hostmasks.context(False):
                # Join message
                self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='ChanServ!ChanServ@services.testnet.internal'))
                output = self.getCommandResponse(self.irc1)
                self.assertIn('C\u200bhanServ has joined', output.args[1])
                self.assertNotIn('ChanServ', output.args[1])

                # PRIVMSG
                self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'hi everyone', prefix='samplenick!user@host'))
                output = self.getCommandResponse(self.irc1)
                self.assertIn('<s\u200bamplenick>', output.args[1])
                self.assertNotIn('<samplenick>', output.args[1])

    def testBlockHighlightsNick(self):
        with conf.supybot.plugins.interlink.noHighlight.context(True):
            with conf.supybot.plugins.interlink.hostmasks.context(False):
                # Nick change
                self.chan2.addUser('old345')
                self.irc2.feedMsg(ircmsgs.nick('new123', prefix='old345!old345@Clk-12345678.lan'))
                output = self.getCommandResponse(self.irc1)
                self.assertIn('o\u200bld345', output.args[1])
                self.assertNotIn('old345', output.args[1])
                self.assertIn('n\u200bew123', output.args[1])
                self.assertNotIn('new123', output.args[1])

    def testBlockHighlightsKick(self):
        with conf.supybot.plugins.interlink.noHighlight.context(True):
            with conf.supybot.plugins.interlink.hostmasks.context(False):
                # KICK
                self.irc2.state.nicksToHostmasks['evilbot'] = 'evilbot!bot@test.client'
                self.chan2.addUser('evilbot')
                self.irc2.feedMsg(ircmsgs.kick(self.chan2name, 'evilbot', 'spam', prefix='a!b@c.d.e.f'))
                output = self.getCommandResponse(self.irc1)
                self.assertIn('e\u200bvilbot', output.args[1])
                self.assertNotIn('evilbot', output.args[1])
                self.assertIn('a\u200b', output.args[1])

    def testBlockHighlightsColor(self):
        with conf.supybot.plugins.interlink.color.context(True):
            with conf.supybot.plugins.interlink.noHighlight.context(True):
                # PRIVMSG
                self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'The quick brown fox jumps over the lazy dog', prefix='Limnoria!limnoria@bot/limnoria'))
                output = self.getCommandResponse(self.irc1)
                self.assertRegex(output.args[1], '<\x03\\d{1,2}L\u200bimnoria\x03>')

    def testShowPrefixes(self):  # currently only implemented for PRIVMSG
        with conf.supybot.plugins.interlink.showPrefixes.context(True):
            users = {"foo": "@", "bar": "%", "baz": "+"}
            for nick, status in users.items():
                self.chan2.addUser(status+nick)
                self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'Hi', prefix='%s!user@monotonous.example' % nick))
                output = self.getCommandResponse(self.irc1)
                self.assertIn('<%s%s>' % (status, nick), output.args[1])
                self.assertNotIn('<%s>' % nick, output.args[1])

    def testShowPrefixesColorNoHighlight(self):
        with conf.supybot.plugins.interlink.showPrefixes.context(True), \
                    conf.supybot.plugins.interlink.color.context(True), \
                    conf.supybot.plugins.interlink.noHighlight.context(True):
            users = {"foo": "@", "bar": "%", "baz": "+"}
            for nick, status in users.items():
                self.chan2.addUser(status+nick)
                self.irc2.feedMsg(ircmsgs.privmsg(self.chan2name, 'Hi', prefix='%s!user@monotonous.example' % nick))
                output = self.getCommandResponse(self.irc1)
                self.assertRegex(output.args[1], '<[%s]\x03\\d{1,2}%s\u200b%s\x03>' % (status, nick[0], nick[1:]))

    ### Ignores
    def testIgnore(self):
        self.assertNotError("admin ignore add *!*@*/bot/*")
        self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "Message from bot", prefix='testbot!testbot@user/test/bot/testbot'))
        self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "Message from user", prefix='tester!test@user/test'))
        output = self.getCommandResponse(self.irc2)
        self.assertEqual('\x02[testnet1]\x02 <tester> Message from user', output.args[1])

    def testIgnoreRegexp(self):
        cfgvar = conf.supybot.plugins.interlink.ignoreRegexp
        try:
            cfgvar.set("m/xnopyt/")
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "!ud xnopyt", prefix='abc!def@efg.user'))
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "!ud IRC", prefix='abc!def@efg.user'))
            output = self.getCommandResponse(self.irc2)
            self.assertIn('<abc> !ud IRC', output.args[1])
        finally:
            cfgvar.set(cfgvar._default)

    @unittest.skip("Not working yet...")
    def testRelaySelfMessages(self):
        self.irc1.nick = 'RelayBot'
        self.chan1.addUser('RelayBot')
        self.chan1.addUser('caller')
        with conf.supybot.plugins.interlink.events.relaySelfMessages.context(True):
            cmd = "RelayBot: echo abcd"
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, cmd, prefix='caller!caller@abc.internal'))
            output = self.getCommandResponse(self.irc2)
            self.assertIn('<caller> ' + cmd, output.args[1])
            output = self.getCommandResponse(self.irc2)
            self.assertIn('<RelayBot> abcd', output.args[1])
        with conf.supybot.plugins.interlink.events.relaySelfMessages.context(False):
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "Interlink: echo xyzzy", prefix='caller!caller@abc.internal'))
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "\o/", prefix='botty!mcbotface@bots.fancy.example'))
            output = self.getCommandResponse(self.irc2)
            self.assertIn('<botty> \o/', output.args[1])

    ### Antiflood
    def testAntifloodSingleSource(self):
        max_messages = random.randint(4, 10)
        timeout = random.randint(10, 120)
        with conf.supybot.plugins.interlink.antiflood.enable.context(True), \
                conf.supybot.plugins.interlink.antiflood.timeout.context(timeout), \
                conf.supybot.plugins.interlink.antiflood.maximum.context(max_messages), \
                conf.supybot.plugins.interlink.antiflood.seconds.context(60):
            prefix = 'spammer!spammer@aaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaa'
            for i in range(max_messages*2):
                text = "This is message #%i" % i
                self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, text, prefix=prefix))

            expected_msgs = ["<spammer> This is message #%i" % i for i in range(max_messages)]
            expected_msgs += ['*** Flood detected', '<spammer> All clear']
            timeFastForward(timeout//2)
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "This message will also get filtered", prefix=prefix))
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "This message will also get filtered!!!11!", prefix=prefix))
            timeFastForward(timeout//2 + 2)
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "All clear", prefix=prefix))

            for expected_msg in expected_msgs:
                output = self.getCommandResponse(self.irc2)
                print("Expected substring:", expected_msg, file=sys.stderr)
                print("Actual:", output, file=sys.stderr)
                self.assertIn(expected_msg, output.args[1])

    def testAntifloodMultiSource(self):
        max_messages = random.randint(5, 15)
        with conf.supybot.plugins.interlink.antiflood.enable.context(True), \
                conf.supybot.plugins.interlink.antiflood.timeout.context(30), \
                conf.supybot.plugins.interlink.antiflood.maximum.context(max_messages), \
                conf.supybot.plugins.interlink.antiflood.seconds.context(60):
            for i in range(max_messages*2):
                text = "This is message #%i" % i
                self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, text, prefix='spam%s!foo@test.user' % i))

            expected_msgs = ["<spam%i> This is message #%i" % (i, i) for i in range(max_messages)]
            expected_msgs += ['*** Flood detected', '<notSpam> All clear']
            timeFastForward(15)
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "This message will also get filtered", prefix='spamLater!foo@test.user.'))
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "This message will also get filtered", prefix='spamLater!foo@test.user.'))
            timeFastForward(16)
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "All clear", prefix='notSpam!foo@test.user.'))

            for expected_msg in expected_msgs:
                output = self.getCommandResponse(self.irc2)
                print("Expected substring:", expected_msg, file=sys.stderr)
                print("Actual:", output, file=sys.stderr)
                self.assertIn(expected_msg, output.args[1])

    def testAntifloodFastExpire(self):
        # In this test, messages are sent with a higher interval than "antiflood.seconds",
        # so flood protection should never trigger.
        expiry = random.randint(3, 120)
        print("Antiflood expiry set to", expiry, "seconds", file=sys.stderr)
        with conf.supybot.plugins.interlink.antiflood.enable.context(True), \
                conf.supybot.plugins.interlink.antiflood.timeout.context(30), \
                conf.supybot.plugins.interlink.antiflood.maximum.context(1), \
                conf.supybot.plugins.interlink.antiflood.seconds.context(expiry):
            expected_msgs = []
            for i in range(12):
                text = "This is message #%i" % i
                timeFastForward(expiry+1)
                self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, text, prefix='spam%s!foo@test.user' % i))
                expected_msgs.append("<spam%i> %s" % (i, text))

            for expected_msg in expected_msgs:
                output = self.getCommandResponse(self.irc2)
                print("Expected substring:", expected_msg, file=sys.stderr)
                print("Actual:", output, file=sys.stderr)
                self.assertIn(expected_msg, output.args[1])

    def testAntifloodCountsChannelSpecific(self):
        # Check that antiflood is triggered on a per-channel basis

        self._createChannel(self.irc1, "#foo")
        self._createChannel(self.irc2, "#bar")
        self.assertNotError("interlink add otherRelay #foo@testnet1 #bar@testnet2")

        max_messages = 3
        with conf.supybot.plugins.interlink.antiflood.enable.context(True), \
                conf.supybot.plugins.interlink.antiflood.timeout.context(30), \
                conf.supybot.plugins.interlink.antiflood.maximum.context(max_messages), \
                conf.supybot.plugins.interlink.antiflood.seconds.context(42):
            for i in range(max_messages*2):
                text = "This is message #%i" % i
                self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, text, prefix='spam%i!aaaa@test.user' % i))

            # Without any fast forwarding, the message sent to #foo should go through
            self.irc1.feedMsg(ircmsgs.privmsg("#foo", "evening", prefix='jlu5!~jlu5@bnc.jlu5.com'))

            expected_msgs = [("<spam%i> This is message #%i" % (i, i), self.chan2name)
                             for i in range(max_messages)]
            expected_msgs += [('*** Flood detected on %s' % self.chan1name, self.chan2name),
                              ('<jlu5> evening', "#bar")]

            for expected_tuple in expected_msgs:
                expected_msg, output_channel = expected_tuple
                output = self.getCommandResponse(self.irc2)
                print("Expected substring:", expected_msg, file=sys.stderr)
                print("Actual:", output, file=sys.stderr)
                self.assertEqual(output_channel, output.args[0])
                self.assertIn(expected_msg, output.args[1])

    def testAntifloodJoin(self):
        # Check that antiflood counts are per command
        max_messages = random.randint(3, 8)
        with conf.supybot.plugins.interlink.antiflood.enable.context(True), \
                conf.supybot.plugins.interlink.antiflood.timeout.context(30), \
                conf.supybot.plugins.interlink.antiflood.maximum.nonPrivmsgs.context(max_messages), \
                conf.supybot.plugins.interlink.antiflood.seconds.context(60), \
                conf.supybot.plugins.interlink.hostmasks.context(False):
            for i in range(max_messages*2):
                prefix = '%s!%s@%s.%s' % (self.randomString(), self.randomString(), self.randomString(), self.randomString())
                self.irc1.feedMsg(ircmsgs.join(self.chan1name, prefix=prefix))
            self.irc1.feedMsg(ircmsgs.privmsg(self.chan1name, "This message will go through", prefix='nobody!~nobody@nogroup.'))
            self.irc1.feedMsg(ircmsgs.part(self.chan1name, "As will this one", prefix='nobody!~nobody@nogroup.'))

            expected_msgs = [" has joined %s" % self.chan1name for i in range(max_messages)]
            expected_msgs += [
                re.compile(r'\*\*\* Flood detected on.*, not relaying JOINs for .* seconds'),
                '<nobody> This message will go through',
                'nobody has left %s (As will this one)' % self.chan1name
            ]

            for expected_msg in expected_msgs:
                output = self.getCommandResponse(self.irc2)
                print("Expected:", expected_msg, file=sys.stderr)
                print("Actual:", output, file=sys.stderr)
                # https://stackoverflow.com/a/34178375
                if isinstance(expected_msg, typing.Pattern):
                    self.assertRegex(output.args[1], expected_msg)
                else:
                    self.assertIn(expected_msg, output.args[1])

    def testAntifloodJoinPart(self):
        # Check that antiflood counts are per command (variant)
        max_messages = random.randint(3, 15)
        with conf.supybot.plugins.interlink.antiflood.enable.context(True), \
                conf.supybot.plugins.interlink.antiflood.timeout.context(30), \
                conf.supybot.plugins.interlink.antiflood.maximum.nonPrivmsgs.context(max_messages), \
                conf.supybot.plugins.interlink.antiflood.seconds.context(60):
            expected_msgs = []

            for i in range(int(max_messages*1.5)+1):
                prefix = '%s!%s@%s.%s' % (self.randomString(random.randint(9, 21)), self.randomString(8),
                                                      self.randomString(20), self.randomString())
                self.irc1.feedMsg(ircmsgs.join(self.chan1name, prefix=prefix))
                self.irc1.feedMsg(ircmsgs.part(self.chan1name, self.randomString(30), prefix=prefix))
                if i < max_messages:
                    expected_msgs += [" has joined %s" % self.chan1name, " has left %s" % self.chan1name]

            expected_msgs += [
                re.compile(r'\*\*\* Flood detected on.*, not relaying JOINs for .* seconds'),
                re.compile(r'\*\*\* Flood detected on.*, not relaying PARTs for .* seconds'),
            ]

            for expected_msg in expected_msgs:
                output = self.getCommandResponse(self.irc2)
                print("Expected:", expected_msg, file=sys.stderr)
                print("Actual:", output, file=sys.stderr)
                # https://stackoverflow.com/a/34178375
                if isinstance(expected_msg, typing.Pattern):
                    self.assertRegex(output.args[1], expected_msg)
                else:
                    self.assertIn(expected_msg, output.args[1])

    ### !nicks command
    def testNicksCommand(self):
        for nick in {'@Vancouver', 'Burnaby', 'Richmond', '%Surrey', '@citiesBot', 'Delta'}:
            self.chan1.addUser(nick)
        for nick in {'+alpha', 'Beta', '@GAMMA', '%delta', 'Epsilon'}:
            self.chan2.addUser(nick)

        # The current implementation sorts nicks alphabetically, without looking at status prefixes
        # The order of the two replies is not deterministic, so I've folded them both into the same regexp
        str1 = r'7 users in %s on testnet1: Burnaby, @citiesBot, Delta, .*%s' % (self.chan1name, self.irc1.nick)
        str2 = r'6 users in %s on testnet2: \+alpha, Beta, %%delta, Epsilon' % self.chan2name
        userlist_re = re.compile('%s|%s' % (str1, str2))
        expected_msgs = [
            re.compile(userlist_re),
            re.compile(userlist_re),
            'Total users across 2 channels: 13. Unique nicks: 12',
            re.compile('^13$')
        ]
        seen = set()

        self.irc1.feedMsg(ircmsgs.privmsg(self.irc1.nick, 'nicks %s' % self.chan1name, prefix='citiesBot!cities@a.b.c.d.e.f'))
        self.irc1.feedMsg(ircmsgs.privmsg(self.irc1.nick, 'nicks %s --count' % self.chan1name.upper(), prefix='citiesBot!cities@a.b.c.d.e.f'))
        for expected_msg in expected_msgs:
            output = self.getCommandResponse(self.irc1)
            print("Expected:", expected_msg, file=sys.stderr)
            print("Actual:", output, file=sys.stderr)

            if isinstance(expected_msg, typing.Pattern):  # https://stackoverflow.com/a/34178375
                self.assertRegex(output.args[1], expected_msg)
            else:
                self.assertIn(expected_msg, output.args[1])
            self.assertNotIn(output.args[1], seen) # Make sure all messages are unique
            self.assertEqual(output.args[0], 'citiesBot')  # Replies should be in private to prevent highlight spam
            seen.add(output.args[1])

    def testNicksCommandErrors(self):
        for nick in {'ocean', 'River'}:
            self.chan1.addUser(nick)
        for nick in {'LAKE', 'Ocean'}:
            self.chan2.addUser(nick)

        # Error: caller not in channel
        self.irc1.feedMsg(ircmsgs.privmsg(self.irc1.nick, 'nicks %s' % self.chan1name, prefix='anon!anon@aaaaa.'))
        # Error: unknown channel
        self.irc1.feedMsg(ircmsgs.privmsg(self.irc1.nick, 'nicks #asfdsafas', prefix='ocean!~ocean@oceans.away'))

        # Error: channel has no relays
        chan3 = self._createChannel(self.irc1, "#news")
        chan3.addUser("wanderer")
        self.irc1.feedMsg(ircmsgs.privmsg(self.irc1.nick, 'nicks #news', prefix='wanderer!~ident@random.wanderer'))

        self.irc1.feedMsg(ircmsgs.privmsg(self.irc1.nick, 'echo [nicks #%s]' % self.chan1name, prefix='river!~stream@rapids.internal'))

        expected_msgs = [
            re.compile('You are not in %r' % self.chan1name),
            re.compile('Unknown channel'),
            re.compile("No relays for '#news' exist"),
            re.compile("cannot be nested")
        ]

        for expected_msg in expected_msgs:
            output = self.getCommandResponse(self.irc1)
            print("Expected:", expected_msg, file=sys.stderr)
            print("Actual:", output, file=sys.stderr)

            if isinstance(expected_msg, typing.Pattern):  # https://stackoverflow.com/a/34178375
                self.assertRegex(output.args[1], expected_msg)
            else:
                self.assertIn(expected_msg, output.args[1])

    # TODO: relaySelfMessages
    # TODO: a >= 3 net relay?

class InterlinkModerationTestCase(InterlinkTwoNetworks):
    """Cross-network ban, tban, kick and unban."""

    def setUp(self):
        super().setUp()
        self.plugin = self.irc.getCallback('Interlink')
        self.plugin.bandb = {'next_id': 1, 'bans': {}}
        # The bot is opped on both ends.
        for chan, irc in ((self.chan1, self.irc1), (self.chan2, self.irc2)):
            chan.addUser('@' + irc.nick)
        self.addUser(self.irc1, self.chan1, '@alice', 'alice!~alice@op.testnet1')
        self.addUser(self.irc1, self.chan1, 'nobody', 'nobody!~nobody@user.testnet1')
        self.addUser(self.irc2, self.chan2, 'bob', 'bob!~bob@bob.example')
        self.addUser(self.irc2, self.chan2, '@carol', 'carol!~carol@op.testnet2')
        self.drain(self.irc1)
        self.drain(self.irc2)

    @staticmethod
    def addUser(irc, chanstate, nick, hostmask):
        chanstate.addUser(nick)
        irc.state.nicksToHostmasks[nick.lstrip('@%+')] = hostmask

    def drain(self, irc, seconds=0.5):
        """Returns every message irc sends in the next few seconds."""
        out = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            drivers.run()
            m = irc.takeMsg()
            while m is not None:
                out.append(m)
                m = irc.takeMsg()
            time.sleep(0.02)
        return out

    def command(self, text, nick='alice', irc=None, channel=None):
        irc = irc or self.irc1
        channel = channel or (self.chan1name if irc is self.irc1 else self.chan2name)
        hostmask = irc.state.nicksToHostmasks.get(nick, '%s!~%s@somewhere' % (nick, nick))
        irc.feedMsg(ircmsgs.privmsg(channel, '%s: %s' % (irc.nick, text), prefix=hostmask))

    @staticmethod
    def find(msgs, command, *args):
        return [m for m in msgs if m.command == command and all(a in m.args for a in args)]

    def notices(self, msgs, nick):
        return [m.args[1] for m in msgs if m.command == 'NOTICE' and m.args[0] == nick]

    def testBanNick(self):
        self.command('ban testnet2 bob spamming')
        out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', '*!*bob@bob.example'), out2)
        kicks = self.find(out2, 'KICK', self.chan2name, 'bob')
        self.assertTrue(kicks, out2)
        self.assertEqual(kicks[0].args[2], 'Banned by alice@testnet1: spamming')
        out1 = self.drain(self.irc1)
        announce = [m.args[1] for m in out1 if m.command == 'PRIVMSG' and m.args[0] == self.chan1name]
        self.assertTrue(any('alice@testnet1 banned bob (*!*bob@bob.example) on testnet2: spamming [#1]' in a
                            for a in announce), out1)
        # The command itself is not relayed
        self.assertFalse([m for m in out2 if m.command == 'PRIVMSG' and 'ban testnet2' in m.args[1]], out2)
        ban = self.plugin.bandb['bans']['1']
        self.assertEqual((ban['setter']['nick'], ban['setter']['network'], ban['reason']),
                         ('alice', 'testnet1', 'spamming'))
        self.assertIsNone(ban['expires'])

    def testOnlyOps(self):
        self.command('ban testnet2 bob', nick='nobody')
        out1 = self.drain(self.irc1)
        self.assertTrue(any('must be a channel operator' in n for n in self.notices(out1, 'nobody')), out1)
        self.assertFalse(self.find(self.drain(self.irc2), 'KICK'))

    def testHalfopsCanUseIt(self):
        self.addUser(self.irc1, self.chan1, '%hal', 'hal!~hal@half.testnet1')
        self.command('kick testnet2 bob go away', nick='hal')
        self.assertTrue(self.find(self.drain(self.irc2), 'KICK', self.chan2name, 'bob'))

    def testOpsCannotBeTargeted(self):
        self.addUser(self.irc2, self.chan2, '%halfop2', 'halfop2!~h@half.testnet2')
        for target in ('carol', 'halfop2'):
            self.command('ban testnet2 %s' % target)
            out1 = self.drain(self.irc1)
            self.assertTrue(any('is a channel operator' in n for n in self.notices(out1, 'alice')), out1)
        self.command('ban testnet2 *!*@op.testnet2')
        out1 = self.drain(self.irc1)
        self.assertTrue(any('would match channel operators' in n for n in self.notices(out1, 'alice')), out1)
        self.assertFalse(self.find(self.drain(self.irc2), 'MODE'))

    def testErrorsArePrivateAndNotRelayed(self):
        self.command('ban testnet2 nosuchnick')
        out1 = self.drain(self.irc1)
        self.assertTrue(any('nosuchnick is not in' in n for n in self.notices(out1, 'alice')), out1)
        self.assertFalse([m for m in self.drain(self.irc2) if m.command == 'PRIVMSG'])

    def testBotNotOpped(self):
        self.chan2.removeUser(self.irc2.nick)
        self.chan2.addUser(self.irc2.nick)
        self.command('ban testnet2 bob')
        out1 = self.drain(self.irc1)
        self.assertTrue(any("I'm not a channel operator" in n for n in self.notices(out1, 'alice')), out1)

    def testTooBroad(self):
        self.addUser(self.irc2, self.chan2, 'bob2', 'bob2!~bob@bob.example')
        self.command('ban testnet2 bob')
        out1 = self.drain(self.irc1)
        self.assertTrue(any('too broad' in n and '--force' in n for n in self.notices(out1, 'alice')), out1)
        self.command('ban --force testnet2 bob')
        out2 = self.drain(self.irc2)
        self.assertEqual(len(self.find(out2, 'KICK', self.chan2name)), 2, out2)

    def testDefaultMaskMatchesWithOrWithoutTilde(self):
        self.command('ban testnet2 bob')
        out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', '*!*bob@bob.example'), out2)
        # The same user coming back with identd running
        self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='bob!bob@bob.example'))
        self.assertTrue(self.find(self.drain(self.irc2), 'KICK', self.chan2name), out2)

    def testSharedHostWithOtherIdentIsNotTooBroad(self):
        self.addUser(self.irc2, self.chan2, 'erin', 'erin!~erin@bob.example')
        self.command('ban testnet2 bob')
        out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', '*!*bob@bob.example'), out2)
        self.assertEqual(len(self.find(out2, 'KICK', self.chan2name)), 1, out2)

    def banmask(self, network=None, channel=None):
        return conf.supybot.plugins.interlink.moderation.banmask.getSpecific(
            network=network, channel=channel, fallback_to_channel=False)

    def testBanmaskPerNetwork(self):
        with self.banmask('testnet2').context('*!*@$host'):
            self.command('ban testnet2 bob')
            out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', '*!*@bob.example'), out2)

    def testBanmaskPerChannelBeatsNetwork(self):
        with self.banmask('testnet2').context('*!*@$host'), \
                self.banmask('testnet2', self.chan2name).context('$nick!*@*'):
            self.command('ban testnet2 bob')
            out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', 'bob!*@*'), out2)

    def testTban(self):
        self.command('tban testnet2 bob')
        self.drain(self.irc2)
        ban = self.plugin.bandb['bans']['1']
        self.assertAlmostEqual(ban['expires'] - ban['time'], 3600, delta=2)
        self.addUser(self.irc2, self.chan2, 'dave', 'dave!~dave@dave.example')
        self.command('tban testnet2 dave 30m flooding again')
        out1 = self.drain(self.irc1)
        ban = self.plugin.bandb['bans']['2']
        self.assertAlmostEqual(ban['expires'] - ban['time'], 1800, delta=2)
        self.assertEqual(ban['reason'], 'flooding again')
        self.assertTrue(any('for 30m: flooding again' in m.args[1] for m in out1 if m.command == 'PRIVMSG'), out1)

    def testTbanExpires(self):
        self.command('tban testnet2 bob 1s')
        out2 = self.drain(self.irc2, 3)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '-b', '*!*bob@bob.example'), out2)
        self.assertFalse(self.plugin.bandb['bans']['1']['active'])
        # Never set again
        self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='bob!~bob@bob.example'))
        self.assertFalse(self.find(self.drain(self.irc2), 'KICK'))

    def testPermanentBanIsLiftedAndSetAgain(self):
        with conf.supybot.plugins.interlink.moderation.liftAfter.context(1):
            self.command('ban testnet2 bob')
            out2 = self.drain(self.irc2, 3)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '-b', '*!*bob@bob.example'), out2)
        ban = self.plugin.bandb['bans']['1']
        self.assertTrue(ban['active'])
        # bob comes back: +b again, kicked with the stored reason, announced
        self.chan2.removeUser('bob')
        self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='bob!~bob@bob.example'))
        out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', '*!*bob@bob.example'), out2)
        kicks = self.find(out2, 'KICK', self.chan2name, 'bob')
        self.assertTrue(kicks and kicks[0].args[2] == 'Banned by alice@testnet1: No reason given', out2)
        out1 = self.drain(self.irc1)
        self.assertTrue(any('matched ban #1' in m.args[1] for m in out1 if m.command == 'PRIVMSG'), out1)

    def testManualUnbanOfPermanentBan(self):
        self.command('ban testnet2 bob')
        self.drain(self.irc2)
        self.irc2.feedMsg(ircmsgs.unban(self.chan2name, '*!*bob@bob.example', prefix='carol!~carol@op.testnet2'))
        out2 = self.drain(self.irc2)
        notes = self.notices(out2, 'carol')
        self.assertTrue(any('You removed *!*bob@bob.example on %s' % self.chan2name in n and 'unban 1' in n
                            for n in notes), out2)
        self.assertFalse(self.find(out2, 'MODE', self.chan2name, '+b'))  # not put back right away
        self.assertTrue(self.plugin.bandb['bans']['1']['active'])
        # ... but enforced on the next join
        self.chan2.removeUser('bob')
        self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='bob!~bob@bob.example'))
        out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '+b', '*!*bob@bob.example'), out2)
        self.assertTrue(self.find(out2, 'KICK', self.chan2name, 'bob'), out2)

    def testManualUnbanOfTban(self):
        self.command('tban testnet2 bob')
        self.drain(self.irc2)
        self.irc2.feedMsg(ircmsgs.unban(self.chan2name, '*!*bob@bob.example', prefix='carol!~carol@op.testnet2'))
        self.drain(self.irc2)
        ban = self.plugin.bandb['bans']['1']
        self.assertFalse(ban['active'])
        self.assertEqual(ban['removed_by'], 'carol@testnet2 (-b)')

    def testUnban(self):
        self.command('ban testnet2 bob')
        self.drain(self.irc2)
        self.command('unban 1')
        out2 = self.drain(self.irc2)
        self.assertTrue(self.find(out2, 'MODE', self.chan2name, '-b', '*!*bob@bob.example'), out2)
        out1 = self.drain(self.irc1)
        self.assertTrue(any('alice@testnet1 removed ban #1' in m.args[1] for m in out1 if m.command == 'PRIVMSG'), out1)
        self.chan2.removeUser('bob')
        self.irc2.feedMsg(ircmsgs.join(self.chan2name, prefix='bob!~bob@bob.example'))
        self.assertFalse(self.find(self.drain(self.irc2), 'KICK'))
        # unban by network and nick
        self.addUser(self.irc2, self.chan2, 'dave', 'dave!~dave@dave.example')
        self.command('ban testnet2 dave')
        self.drain(self.irc2)
        self.command('unban testnet2 dave')
        self.assertTrue(self.find(self.drain(self.irc2), 'MODE', self.chan2name, '-b', '*!*dave@dave.example'))

    def testGlobalBan(self):
        self.addUser(self.irc1, self.chan1, 'spambot1', 'spambot1!~x@host.one')
        self.addUser(self.irc2, self.chan2, 'spambot2', 'spambot2!~y@host.two')
        self.command('ban --global spambot*!*@*')
        out1 = self.drain(self.irc1)
        out2 = self.drain(self.irc2)
        for out, chan, nick in ((out1, self.chan1name, 'spambot1'), (out2, self.chan2name, 'spambot2')):
            self.assertTrue(self.find(out, 'MODE', chan, '+b', 'spambot*!*@*'), out)
            self.assertTrue(self.find(out, 'KICK', chan, nick), out)
            self.assertTrue([m for m in out if m.command == 'WHO' and m.args[0] == chan], out)
        self.assertTrue(any('on all networks' in m.args[1] for m in out1 if m.command == 'PRIVMSG'), out1)
        # Enforced on every network
        self.irc1.feedMsg(ircmsgs.join(self.chan1name, prefix='spambot3!~z@elsewhere'))
        self.assertTrue(self.find(self.drain(self.irc1), 'KICK', self.chan1name, 'spambot3'))
        self.command('unban --global spambot*!*@*')
        self.assertTrue(self.find(self.drain(self.irc2), 'MODE', self.chan2name, '-b', 'spambot*!*@*'))

    def testGlobalBanNeedsHostIndependentMask(self):
        self.command('ban --global *!*@evil.example')
        out1 = self.drain(self.irc1)
        self.assertTrue(any("only matches one network's hosts" in n for n in self.notices(out1, 'alice')), out1)
        self.command('ban --global bob')
        self.assertTrue(any('only works with masks' in n for n in self.notices(self.drain(self.irc1), 'alice')))
        self.command('ban * spambot*!*@*')
        self.assertTrue(any('Use --global' in n for n in self.notices(self.drain(self.irc1), 'alice')))
        self.assertFalse(self.find(self.drain(self.irc2), 'MODE'))

    def testGlobalTban(self):
        self.command('tban --global *!*spam@* 2h bot wave')
        self.drain(self.irc2)
        ban = self.plugin.bandb['bans']['1']
        self.assertEqual((ban['network'], ban['mask'], ban['reason']), ('*', '*!*spam@*', 'bot wave'))
        self.assertAlmostEqual(ban['expires'] - ban['time'], 7200, delta=2)

    def testNickChangeIntoBan(self):
        self.command('ban testnet2 nastynick!*@*')
        self.drain(self.irc2)
        self.irc2.feedMsg(ircmsgs.IrcMsg(prefix='bob!~bob@bob.example', command='NICK', args=('nastynick',)))
        self.assertTrue(self.find(self.drain(self.irc2), 'KICK', self.chan2name, 'nastynick'))

    def testKick(self):
        self.command('kick testnet2 bob calm down')
        out2 = self.drain(self.irc2)
        kicks = self.find(out2, 'KICK', self.chan2name, 'bob')
        self.assertTrue(kicks and kicks[0].args[2] == 'Kicked by alice@testnet1: calm down', out2)
        self.assertFalse(self.find(out2, 'MODE'))

    def testBansAndBaninfo(self):
        self.command('ban testnet2 bob spam links')
        self.drain(self.irc2)
        self.command('bans')
        out1 = self.drain(self.irc1)
        lines = self.notices(out1, 'alice')
        self.assertTrue(any(l.startswith('#1 *!*bob@bob.example on testnet2 (permanent), by alice@testnet1') and
                            l.endswith('spam links') for l in lines), out1)
        self.command('unban 1')
        self.drain(self.irc1)
        self.command('baninfo 1')
        lines = self.notices(self.drain(self.irc1), 'alice')
        self.assertTrue(any('alice!~alice@op.testnet1' in l and 'removed by alice@testnet1' in l for l in lines), lines)

    def testBanDatabaseIsSaved(self):
        import json
        from Interlink import plugin as interlink_plugin
        self.command('tban testnet2 bob 2h spam')
        self.drain(self.irc2)
        with open(interlink_plugin.bans_filename) as f:
            saved = json.load(f)
        ban = saved['bans']['1']
        self.assertEqual((ban['mask'], ban['reason'], ban['setter']['hostmask']),
                         ('*!*bob@bob.example', 'spam', 'alice!~alice@op.testnet1'))


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
