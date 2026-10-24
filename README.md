**Interlink** is a relayer for Limnoria, designed with two-way relays between networks in mind, that lets channel ops on any network moderate the relay (ban, kick) on the other networks. It is a fork of [RelayNext](https://github.com/jlu5/Limnoria-RelayNext) by James Lu.

Interlink supports relaying between channels with different names, and stores its entries in a database instead of the configuration, preventing various forms of entry corruption.

## Install

Due to technical limitations, installation via Limnoria's PluginDownloader or pip is currently unavailable.

### Via Git checkout

Clone the Git repository somewhere and add the *parent* directory to your bot's `config directories.plugins` setting.
Make sure the directory that you save the plugin to matches the plugin name:

```
$ git clone https://github.com/TehPeGaSuS/Interlink Interlink
```

After cloning, you can update your copy of the plugin in place by entering the Interlink directory and running:

```
$ git pull
```

## Usage

### Creating a new relay

Creating a relay is simple, simply run:

* `interlink add Your-relay-name #channel1@networkOne #somewhere@networkTwo`

on your bot. **You must specify a name for the relay! (replacing `your-relay-name` with whatever)**

### Modifying relays

Once a relay is created, you can manipulate these entries further using the `set`, `add`, and `remove` commands.

* `interlink add Your-relay-name #somewhere@overTheRainbow`
* `interlink remove Your-relay-name #channel1@networkOne`

Relays are case-insensitive and require at least two channels to relay between. When the last two channels are removed, the relay is automatically deleted.

Note: The `set` command **replaces** the relay in question, while `add` **adds** channels to it. Both will create a new relay if the name you specify doesn't already exist.

### Listing defined relays

The `list` command will list all relays defined.

* `interlink list`

### Removing/clearing relays

You can remove relays (or channels within them) using the `remove` command. There is also a `clear` command which clears all relays.

To remove channels from an existing relay:

* `interlink remove Your-relay-name #channel1@networkone #blah@networktwo`

Or, to delete an entire relay:

* `interlink remove Your-relay-name`

Relays require at least two channels to relay between. When the last two channels are removed, the relay is automatically deleted.

## Cross-network moderation

Networks stay independent: modes, ops and bans are never synced between them. Instead, channel operators (halfop or higher) of a relayed channel can ban and kick users on the relay's other networks with these commands, typed in the relayed channel. The commands are not relayed, errors are sent to you as a private notice, and every action is announced in all the relay's channels. Channel operators (halfop or higher) on the target network can't be banned or kicked.

| Command | What it does |
|---|---|
| `ban [--force] <network> <nick\|mask> [<reason>]` | Bans the nick (using the `moderation.banmask` setting, `*!*@$host` by default) or the mask on that network, and kicks the matching users |
| `ban --global [--force] <mask> [<reason>]` | Bans the mask on every network of the relay (see below) |
| `tban [--global] [--force] ... [<duration>] [<reason>]` | Like `ban`, but lifted after the duration (`30m`, `2h`, `1d`, `1h30m`...; `moderation.tbanDuration`, 1h, by default) and never set again |
| `kick <network> <nick> [<reason>]` | Kicks the nick |
| `unban <id>`, `unban <network> <nick\|mask>` or `unban --global <mask>` | Removes a ban and its +b |
| `bans [<network>]` | Lists the relay's active bans |
| `baninfo <id\|mask>` | Shows a ban's details, including removed ones |

For example, from `#chat` on Libera: `!ban oftc spammer advertising`.

- `<network>` is the network's name in Limnoria. Bans are per network: a nick ban uses that user's host on that network, and cloaks or vhosts don't match anywhere else.
- **Global bans** (`--global`) are for patterns that are the same on every network: nicks and idents, which users choose themselves (e.g. `ban --global spambot*!*@*` or `ban --global *!*spam@*` against a bot wave). A global mask must have `*` as its host part; host bans always name their network.
- **Permanent bans** (`ban`): the +b is removed after an hour (`moderation.liftAfter`) to keep the channel's ban list short, but the ban stays in Interlink. Whenever a matching user joins, changes nick or changes host, the +b is set again and they are kicked with the original reason, until the ban is removed with `unban`.
- **If a network's op removes one of these bans** with `-b`, Interlink respects it: it doesn't set it back right away. For a permanent ban, that op is told that the ban is still on the bot and how to delete it (`unban <id>`); it's set again when a matching user joins. A `tban` removed by hand is closed.
- **Too broad**: a ban that would match more than `moderation.maxMatches` users (1 by default) in the target channel is refused, unless `--force` is given. A ban that would match a channel operator is always refused.
- Mask bans also check everyone already in the channel (Interlink refreshes its view of the channel with WHO).
- Bans are stored in `data/Interlink-bans.json` with who set them (nick, network and hostmask), when, the reason and the expiry, and who removed them.
- The bot needs to be a channel operator in the target channel.

Limnoria's Channel plugin also has `ban`, `kick` and `unban` commands. If it's loaded, make Interlink's the default ones (`defaultplugin ban Interlink`, and the same for `kick` and `unban`), or call them as `interlink ban ...`; Channel's stay available as `channel ban ...`.

## Configuration

Most of the following options are also configurable per channel. For any channel-specific variables, settings on the target relay channel are used for NICK and QUIT messages, while settings on the source channel are applied for all others. This is because nick changes and quits are not bound to any channels.

### Relaying non-PRIVMSG events

Interlink supports relaying the following non-PRIVMSG events: joins, kicks, mode changes, nick changes, parts, quits, and topic changes. Each of these can be turned on and off using configuration variables, and have the following defaults:

- `config plugins.Interlink.events.relayjoins`: `True`
- `config plugins.Interlink.events.relaykicks`: `True`
- `config plugins.Interlink.events.relaymodes`: `True`
- `config plugins.Interlink.events.relaynicks`: `True`
- `config plugins.Interlink.events.relayparts`: `True`
- `config plugins.Interlink.events.relayquits`: `True`
- `config plugins.Interlink.events.relaytopics`: `False`

Note: Topic relaying will only show topic *changes* in a channel. **It does not yet sync topics between channels!**

### Ignoring users
Interlink uses Supybot's built in ignore system. However, you can set which messages you want to ignore (from ignored users) using `config plugins.Interlink.events.userignored`. This is NOT a list of users to ignore, which is defined with Limnoria's global ignore system (eg. with the command `admin ignore add *!*@spammer.example`).

This key takes a space separated list, and defaults to ignoring `PRIVMSG` and `MODE`. **If you want to disable the ignore feature entirely, simply set this value blank: `config plugins.Interlink.events.userignored ""`**

### Highlight prevention
One annoying aspect of relays is that when someone is on multiple relayed channels with the same nick, they will be spammed with highlights whenever they speak. Interlink can mitigate this behavior by inserting an invisible space in the middle of the nick.

You can turn this prefixing on via:
* `config plugins.Interlink.noHighlight True`

### Flood prevention
Flood prevention in Interlink can be enabled with the following configuration option:
* `config plugins.Interlink.antiflood.enable True`

When flood prevention is triggered, a warning message is displayed and all further messages of the matched type will be ignored for a certain amount of seconds. For example, in the event of a netsplit, QUIT and JOIN messages will be blocked to prevent the bot from flooding itself off, allowing conversations to be relayed mostly unaffected.

The following options allow you to fine tune Interlink's flood prevention:

* `plugins.Interlink.antiflood.maximum`: configure the maximum amount of messages (PRIVMSGs) allowed in *X* seconds before flood prevention is triggered.
* `plugins.Interlink.antiflood.maximum.nonPrivmsgs`: configure the maximum amount of each non-PRIVMSG events (joins, quits, etc.) allowed in *X* seconds before flood prevention is triggered.
* `plugins.Interlink.antiflood.seconds`: configure the *X* amount of seconds mentioned above.
* `plugins.Interlink.antiflood.timeout`: determines the amount of seconds the bot will wait before flood prevention expires (messages are relayed as normal again).

### Miscellaneous formatting options

- You can enable/disable colors via the configuration variable `plugins.Interlink.color`
- Hostmask display can be configured via `plugins.Interlink.hostmasks`. It defaults to True to make impersonating nicks more difficult, but you can turn this off if you wish.
- Prefix display (`@`, `%`, or `+` in front of nicks) can be toggled via `plugins.Interlink.showPrefixes`.
