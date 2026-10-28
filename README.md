# libre-mcp

**Your FreeStyle Libre glucose, for Claude.** A read-only
[MCP](https://modelcontextprotocol.io) server that lets Claude (and, in the
hosted mode, ChatGPT and Mistral Vibe) answer questions about your sensor
glucose: the current value, the curve of any period, time in range, the
typical day, lows and highs, and every sensor you wore. Next to a Garmin MCP
server, glucose and training can be put side by side.

```
You:  How was my glucose during Sunday's long run?
You:  How much time was I in range last month?
You:  Are there recurring lows at night?
You:  How long did my last sensors actually last?
```

It never writes to LibreLinkUp. Every tool is read-only.

**Not a medical device.** Never base a treatment decision on it.

---

## Quick start: on your own computer

The standalone mode is for one person: Claude Code or Claude Desktop starts
the server on your machine, and it reads LibreLinkUp with your own session.
No server, no accounts. You need Python 3.12 or newer,
[uv](https://docs.astral.sh/uv/), and a LibreLinkUp **follower** account the
person with the sensor shares their glucose with.

```bash
uv tool install git+https://github.com/mskerwiderski/MCPServerFreeStyleLibre
libre-mcp login                                    # follower e-mail and password
claude mcp add --scope user libre -- libre-mcp stdio
```

Then ask Claude "What is my glucose right now?". Claude Desktop, collecting
the history, where things are stored, removing it again:
[docs/standalone.md](docs/standalone.md).

## Hosted: many users, any AI client

The same tools also run as a remote MCP server (an HTTP app in a container)
with its own OAuth 2.1 authorization server, so claude.ai (browser and phone),
ChatGPT and Mistral Vibe can add it as a connector. It is multi-tenant: each
user's connector only ever sees that user's glucose. Each user has an account
page with live statistics, the sensor history, a LibreView CSV import and
settings.

Users sign in through **MyAuth**, the author's self-hosted login service. It
decides who may use the server and holds the LibreLinkUp connections. **MyAuth
is not published**, so the hosted mode will not run without it (or a service
answering the same few endpoints, listed in [docs/hosted.md](docs/hosted.md)).
It is documented here for transparency and for the author's own instance; for
yourself, use the standalone mode above.

## Documentation

| Guide | What is in it |
|---|---|
| [Standalone on your own machine](docs/standalone.md) | `libre-mcp login`, Claude Desktop / Claude Code over stdio, collecting, the files |
| [Install for Claude](docs/install-claude.md) | Standalone and hosted, claude.ai, Claude Code, troubleshooting |
| [Install for ChatGPT](docs/install-chatgpt.md) | Hosted only: developer mode, custom connector, troubleshooting |
| [Install for Mistral Vibe](docs/install-mistral.md) | Hosted only: custom MCP connector, OAuth, troubleshooting |
| [Hosted: your account](docs/account.md) | Follower account, connecting LibreLinkUp in MyAuth, CSV import, settings |
| [Usage examples](docs/usage.md) | What to ask, what comes back, what the numbers mean |
| [Tool reference](docs/tools.md) | Every tool, its parameters and an example response |
| [Hosted: running the server](docs/hosted.md) | Requirements, configuration, reverse proxy, container, collector |
| [How it works](docs/architecture.md) | Design decisions: collector, sensors, statistics, tenancy |

## Where the data comes from

Abbott offers no API to individuals. The server reads through **LibreLinkUp**,
Abbott's app for followers (family members who watch someone's glucose) - the
same unofficial interface Nightscout and other Libre tools use. It only ever
shows the **last 12 hours**. So the server keeps its own history: it asks
LibreLinkUp regularly (every minute by default) and stores what is new.

- **Standalone:** it collects while Claude runs it; `libre-mcp collect` does
  one round for a scheduled job, so the history has no gaps.
- **Hosted:** it collects all the time, for every connected user, as often as
  each user chose (1 to 60 minutes). Older readings come in once through the
  **CSV export from LibreView**, uploaded on the account page.

## Sensors

Every reading belongs to a sensor, and the server keeps the **history of all
sensors**: number, serial number, start, last reading, days worn, and what it
measured (coverage, mean, time in range, share below 70 mg/dL). Neither
LibreLinkUp nor the CSV names the sensor per reading, but sensors run one after
the other: a reading belongs to the sensor that started last before it.

- **Reported:** LibreLinkUp names the current sensor with its serial number
  and exact start at every poll, so every sensor worn while the collector runs
  is known.
- **Estimated:** older sensors (CSV import, history from before) are
  recognised by the gap of at least an hour each change leaves (the warm-up).
  Start = first reading after the gap minus an hour, no serial number. A gap
  while a reported sensor was running counts as lost data, not as a change.

Every answer with readings names the sensors behind them, and the period tools
take `sensor` to stay with one.

## Good to know

Abbott can change the interface at any time (last time: October 2025). Then
collecting stops until the client is adapted; gaps of up to 12 hours fill
themselves afterwards. Use this for your own glucose and for people who share
theirs with you.

Responses are deliberately small: long periods come back condensed into time
buckets, and statistics replace raw series where they answer the question.
Ranges follow the international consensus on CGM reporting (<54, 54-69,
70-180, 181-250, >250 mg/dL). Values are always mg/dL in the tools.

## Development

```bash
uv sync --extra dev
```

```bash
uv run pytest
```

122 tests, no network access required - LibreLinkUp and MyAuth are faked, and
all glucose values in the tests are made up (no real exports, `*.csv` is
ignored). See [docs/architecture.md](docs/architecture.md) for how the pieces
fit together. Some folders are vendored copies of shared building blocks and
are not edited here; the architecture document says which.

## Disclaimer

**Not a medical device.** The values come from an unofficial interface and can
be late, missing or wrong; never base a treatment decision on them.

An independent, unofficial project, not affiliated with or endorsed by Abbott.
FreeStyle Libre, LibreLinkUp and LibreView are trademarks of Abbott. The
LibreLinkUp interface is undocumented and may change or stop working at any
time.

## License

MIT. See [LICENSE](LICENSE).
