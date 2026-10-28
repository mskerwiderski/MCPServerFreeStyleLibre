# Standalone on your own machine

No server, no MyAuth, no accounts: the server runs on your own computer for
one person, and Claude Code or Claude Desktop starts it there. You sign in to
LibreLinkUp once in a terminal; while Claude runs the server, it collects the
readings and the tools work like the remote connector.

```
Claude Code / Claude Desktop ── starts ──▶ libre-mcp stdio ──▶ LibreLinkUp
                                               │
                                               ├─ ~/.config/libre-mcp/session.json
                                               └─ ~/.local/share/libre-mcp/libre.db
```

## 1. Install

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/mskerwiderski/MCPServerFreeStyleLibre
libre-mcp --help
```

`uv tool install` puts `libre-mcp` into `~/.local/bin` (run
`uv tool update-shell` once if that is not on your `PATH`). To update, run the
same command with `--reinstall`. From a local clone instead:
`uv tool install --reinstall /path/to/MCPServerFreeStyleLibre`.

## 2. Sign in to LibreLinkUp

LibreLinkUp is Abbott's app for followers: the person with the sensor shares
their glucose with a follower account (FreeStyle Libre app: Menu > Connected
Apps > LibreLinkUp > Add Connection). Sign in with that **follower** account:

```bash
libre-mcp login
```

It asks for the e-mail and password and, if several people share with the
account, whose glucose to read.

```bash
libre-mcp status       # who, whose glucose, how many readings are stored
libre-mcp logout       # delete the session (the readings stay)
```

**Your password is never stored**, only the session LibreLinkUp hands out. It
lasts about half a year and LibreLinkUp renews it with every answer, so in
regular use it keeps going. When LibreLinkUp stops accepting it, the tools say
so: run `libre-mcp login` again. A wrong password is never retried by itself -
Abbott locks the account after a few failed sign-ins.

## 3. Add it to Claude

**Claude Code** (this only reaches Claude Code, not Claude Desktop):

```bash
claude mcp add --scope user libre -- libre-mcp stdio
claude mcp list              # libre ... Connected
```

**Claude Desktop** (a separate configuration): Settings → Developer → Edit
Config, then in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "libre": {
      "command": "/Users/<you>/.local/bin/libre-mcp",
      "args": ["stdio"]
    }
  }
}
```

Claude Desktop needs the full path (`which libre-mcp` prints it) and a
complete restart (Cmd+Q). Ask "What is my glucose right now?" to check.

## Collecting readings

LibreLinkUp only ever shows the last 12 hours, so the history is what the
server collects itself: when it starts, and then every minute **while Claude
runs it**. Gaps longer than 12 hours stay gaps. To keep the history complete,
run one collection round regularly, for example hourly:

```bash
libre-mcp collect
```

On a Mac that is a launchd job (or `crontab -e` with
`0 * * * * ~/.local/bin/libre-mcp collect`). Optional - without it, the server
simply has the history of the times you used it plus 12 hours each.

## What is stored, and where

- **`~/.config/libre-mcp/session.json`** (or `$XDG_CONFIG_HOME/libre-mcp/`, or
  `LIBRE_MCP_SESSION`): region host, token, account id, whose glucose, your
  e-mail. Readable by you only (0600, directory 0700). No password.
- **`~/.local/share/libre-mcp/libre.db`** (or `$XDG_DATA_HOME/libre-mcp/`, or
  `MCP_DB`): the readings, minute values and sensors - health data, in a
  directory only you can open. Readings older than two years are deleted
  when the server collects.
- Times are shown in `$TZ`, else your computer's time zone.

The LibreView CSV import is a feature of the hosted account page; the
standalone mode has its history from the day you sign in.

## Removing it again

```bash
claude mcp remove libre --scope user      # Claude Code
libre-mcp logout                          # delete the session
rm -r ~/.local/share/libre-mcp            # the readings
uv tool uninstall libre-mcp               # the program
```

For Claude Desktop, delete the `"libre"` entry from its config file.

## How it relates to the hosted mode

Same tools, same code. The difference is only where the LibreLinkUp session
comes from: in the hosted mode from MyAuth (`MyAuthSource`), here from the
session file (`local.LocalSource`). See [hosted.md](hosted.md) and
[architecture.md](architecture.md).
