# MCPServerFreeStyleLibre

Read-only MCP server for FreeStyle Libre glucose (package `libre_mcp`, command
`libre-mcp`): standalone for one person (`libre-mcp stdio`) or hosted and
multi-tenant behind MyAuth (`libre_mcp.server:build_http_app`). Reads through
LibreLinkUp (the unofficial follower API), collects its own history and takes
LibreView CSV exports for older data. Public repository.

**Read [docs/architecture.md](docs/architecture.md) before changing anything.**
It carries the design decisions and the LibreLinkUp quirks behind them; this
file only adds what is specific to working in this repository. Overview:
[README.md](README.md).

Documentation, code comments and the web UI are English. A sibling of Garmin
MCP (MCPServerGarminConnect): same pattern (MyAuth, OAuth, path on a public
host, msk-ui).

The author's own hosted instance is operated from a private infrastructure
repo; nothing about it (host names, addresses, keys, deploy commands) belongs
here. Examples use placeholders such as `https://mcp.example.com/libre`.

## Modules

| File | What |
|---|---|
| `librelinkup/` | LibreLinkUp client (sign-in, patients, graph), imported as `llu`: a **copy** of the MyCommons building block `librelinkup`, never edited here |
| `local.py`, `cli.py` | Standalone mode: `libre-mcp login/status/logout/stdio/collect`, session in `~/.config/libre-mcp/session.json` (0600, no password), readings in `~/.local/share/libre-mcp/libre.db`; `LocalSource` replaces MyAuth via `libre.use_source` (docs/standalone.md) |
| `libre.py` | Session from the source (`MyAuthSource` hosted, `local.LocalSource` standalone), `refresh`, whose readings, error handling, collector |
| `readings.py` | Storing readings (dedupe ±120 s), reading them, retention (sensors included) |
| `sensors.py` | Sensor history: reported (LibreLinkUp) or inferred from gaps; which sensor measured which reading (`of`, `spans`), figures per sensor (`history`) |
| `stats.py` | Metrics (consensus 2019), daily pattern, episodes, downsampling |
| `csv_import.py` | LibreView export, columns by header, de/en, mg/dL + mmol/L |
| `times.py` | Local time: the user's zone from MyAuth (`times.use`), else `TZ` |
| `tools.py` | The eight MCP tools; docstrings are UI for the model. Answers with readings name the sensors (`sensors`), parameter `sensor` limits to one |
| `web.py` | Account page: state from MyAuth + poll interval, statistics with a period filter 1 h-12 months (`?period=`, default 24 h; figures + inline SVG without script, from 1 month daily means with range; up to 2 days a dashed tail of minute values, because LibreLinkUp's history lags ~20 min), sensor/app details, retention, CSV upload; **live** without reload: `_LIVE_JS` fetches `/account/live` every minute (LibreLinkUp, statistics and sensor cards as HTML) and pauses while the tab is hidden; page shell |
| `internal.py` | `GET /internal/readings` for MyAuth: history (`readings`) and minute values (`realtime`) of a window (≤ 36 h) for other apps; `GET /internal/gmi?uid&at`: GMI over the user's GMI period up to `at` (with mean, coverage, `period_days`); both with `display_unit`; Docker network only + own `MYAUTH_APP_KEY` |
| `oauth.py` | OAuth for the MCP endpoint: thin layer over the MyCommons building block `mcp_oauth` (`libre_mcp/mcp_oauth/` is a copy), keeps the consent and error page. The refresh token stays the same; earlier access tokens stay valid until they expire |
| `users.py`, `db.py`, `server.py` | as in Garmin MCP |
| `access/` | Sign-in, access checks and the LibreLinkUp session through MyAuth: a **copy** of the MyCommons building block `access`, never edited here; `__init__.py` calls `myauth.configure(app="mcp-libre", platform="LibreLinkUp")` |

## Rules that are easy to break

- **Vendored building blocks are never edited here.** `access/`,
  `librelinkup/` and `mcp_oauth/` are copies from the author's private repo
  MyCommons: change the block there, run its tests, bump its version, then
  `MyCommons/sync.sh <block> libre_mcp` (and into its other users).
  `tests/test_access_copy.py` fails locally when a copy is out of date
  (skipped when MyCommons is not cloned next to this repo).
- **Shared pattern with Garmin MCP:** page shell, addresses and `signed_in` in
  `web.py`, `_McpEndpoint` in `server.py`. Fix a bug there in both repos.
- **No health data in the repo.** No real exports, no real values in tests
  (`*.csv` is ignored). Tests use made-up values and the fictitious family
  "Muster".
- **No test talks to MyAuth or LibreLinkUp.** Mock points:
  `access.myauth._fetch_session`, `_fetch_access`, `_fetch_libre`,
  `_fetch_connections` (`FakeMyAuth`) and `llu._client` (HTTP transport,
  `FakeLLU` in `conftest.py`).
- **The LibreLinkUp connection lives in MyAuth** in the hosted mode (platform
  `libre`): password, sign-in, choice of the person, lockouts. No login here,
  no password, no token on disk; the session comes from MyAuth per poll (30 s
  cache), on 401 with `renew=1`. When MyAuth reports a different
  `patient_id`, the stored readings are deleted.
- **Standalone mode** (`local.py`, `cli.py`): one user (0), session file
  without password; it never signs in by itself - an expired session asks for
  `libre-mcp login`. stdio means stdout belongs to the MCP protocol - never
  print there in that path. Guide: docs/standalone.md.
- **Read LLU answers tolerantly.** Only the fields needed, skip anything
  broken; strict models fail e.g. on an active alarm.
- **Every tool needs `_guard`** and a compact answer (no raw data).
- **Only history values go into `readings`** (graph data, CSV type 0), not the
  minute-by-minute current value or scans - otherwise shares and coverage are
  wrong. The collector keeps the current value apart in `realtime` (polled
  every minute): LibreLinkUp never hands out past minute values, only whoever
  asks in that minute has them. For other apps only (`internal.py`), not for
  the tools.
- **`db.conn()` skips its commit when an exception passes through.** Write,
  leave the block, then raise.
- **Every POST acting for the signed-in user goes through `web.signed_in`**
  (it also checks `Origin`: apps behind one MyAuth share one cookie host).
- **"Sign in as" (MyAuth):** when `/api/session` reports `impersonated_by`,
  `web.page` shows a banner on every page with a user (the consent page too)
  with a link to MyAuth's `/impersonate/stop?next=<current URL>`.
- **Poll interval per user** (`users.poll_minutes`, 1/3/5/15/30/60 min,
  default 1): the collector wakes every `POLL_INTERVAL_S` (60 s) and only asks
  who is due (`libre._due`). Above 1 min there are no minute values.
- **Display unit per user** (`users.unit`: auto/mgdl/mmol, default auto = like
  the Libre app according to LibreLinkUp's `uom`, 0 = mmol/L): only the
  account page converts (`web._g`, 18.0156); stored and handed to tools/apps
  is still mg/dL. Ranges in mmol/L: 3.9-10.0.
- **GMI period per user** (`users.gmi_days`: 14/30/90 days, default 14 =
  consensus): "the" GMI - tile on the account page (independent of the
  statistics period) and `/internal/gmi` for other apps. The period is this
  setting, never the asking app's. LibreLinkUp delivers no GMI; it is always
  computed here from the history (`stats.summarize`); `get_glucose_stats`
  computes it over the requested period and reports the setting as
  `gmi_setting_days`.
- **Display unit for other apps:** `users.display_unit` resolves "auto"
  (mgdl/mmol); `/internal/*` return it as `display_unit`, the values stay
  mg/dL.
- **Sensor details** come from the same graph answer
  (`libre_accounts.sensor_info`, JSON): warm-up, type code (undocumented,
  shown raw), app version, alarm limits, current value + trend. Serial number
  and activation live in the table `sensors`, not in `libre_accounts`.
- **Time decides which sensor measured which reading** (`sensors.of`): the
  one started last before it. Never store a sensor per reading; then a sensor
  found later (CSV) does not re-sort the readings by itself. Reported sensors
  (`source='llu'`) are facts; `gap`/`first` are estimated and recomputed on
  CSV import, a new reported sensor and server start (`sensors.rebuild`),
  never maintained by hand.
- **Readings for other apps only through MyAuth** (`internal.py`): MyAuth
  decides which app gets whose glucose (`/api/internal/glucose`) and asks with
  our app key. No key per app, no public path.
- **The msk-ui copy in `static/ui/` is never edited here**; it comes from the
  author's design system. App styles go into `static/app.css`, tokens only.
- **No host-specific values in the repo.** Addresses and keys of a hosted
  instance are environment variables (`.env.example`, docs/hosted.md).

## Working on it

```bash
uv sync --extra dev
uv run pytest
uvx ruff check --select E9,F libre_mcp tests
```

Tests never touch the network or the real `~/.config/libre-mcp`; `conftest.py`
points every path at a temporary directory and fakes MyAuth and LibreLinkUp.
Running a hosted instance: [docs/hosted.md](docs/hosted.md).
