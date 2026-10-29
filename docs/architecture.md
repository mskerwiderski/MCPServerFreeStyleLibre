# How it works

The design decisions behind the server, and the LibreLinkUp quirks they answer.
For setting it up see the [README](../README.md); for the tools,
[tools.md](tools.md).

## Module map

```
libre_mcp/
  cli.py          libre-mcp login/status/logout/stdio/collect (standalone)
  local.py        standalone: session file, LocalSource, user 0
  server.py       hosted HTTP app: MCP endpoint, OAuth routes, pages, collector
  tools.py        the eight MCP tools; docstrings are what the model reads
  libre.py        session source (MyAuth or local), polling, the collector
  readings.py     stored readings and minute values, dedupe, retention
  sensors.py      sensor history: reported or inferred from gaps
  stats.py        CGM metrics, daily pattern, episodes, downsampling
  csv_import.py   LibreView export (hosted account page)
  times.py        the user's time zone, day boundaries, time formatting
  internal.py     /internal/readings and /internal/gmi for MyAuth
  web.py          account page, page shell, signed_in (on access.web;
                  addresses come from mcp_oauth.addresses)
  oauth.py        thin layer over mcp_oauth: consent and error page
  users.py        local side of a MyAuth user, per-user settings
  db.py           SQLite schema and migrations
  access/         MyAuth client                    (vendored, see below)
  librelinkup/    LibreLinkUp client, as `llu`     (vendored)
  mcp_oauth/      OAuth 2.1 authorization server   (vendored)
  static/         icons, app.css; ui/ is msk-ui    (vendored)
```

## Two modes, one code path

The tools never know where the LibreLinkUp session comes from. `libre.py` asks
a **source**:

- `MyAuthSource` (hosted): MyAuth holds the follower account's e-mail and
  password, signs in, and hands out the session - region host, token, account
  id, whose glucose - at `/api/internal/libre/token`. On a 401 from
  LibreLinkUp the server asks again with `renew=1` and MyAuth signs in anew.
  Nothing about the login is stored on this server.
- `local.LocalSource` (standalone, set with `libre.use_source`): the session
  file written by `libre-mcp login`. The password is never stored; the token
  lasts about half a year, LibreLinkUp renews it with every answer, and
  `keep` saves the renewed one. When it is no longer accepted, the tools say
  "run `libre-mcp login` again" - the server never signs in by itself.

Everything after the session - fetching the graph, storing readings, sensors,
statistics, the tools - is the same code. Standalone, the person is user 0 in
a local database; stdio means stdout belongs to the MCP protocol.

## One mechanism for every user (hosted)

The MCP endpoint resolves the bearer token to a user, asks MyAuth whether that
user may still use the app (`/api/internal/access`, cached 30 seconds), puts
the user into a `ContextVar` (`users.CURRENT_USER`) and its time zone into
another (`times.use`), and only then hands the request to the MCP session
manager. The tools read the `ContextVar`. That is the whole tenancy
mechanism, and it is why no request can reach another user's data.

`users.id` is the MyAuth uid. The username is kept for one case: a MyAuth
database rebuilt from scratch hands the same uid to somebody else. When the
name behind a uid changes, everything stored for the previous owner is dropped
before the new one can see it - these are other people's health data. The
same holds when MyAuth reports a different person (`patient_id`) for a user's
LibreLinkUp connection: the old readings are not theirs and are deleted.

## The collector and the history

LibreLinkUp only ever shows the **last 12 hours**: the smoothed 5-minute
history (graph data) and the current value. So the server keeps its own
history. The collector (`libre.run_collector`) wakes every `POLL_INTERVAL_S`
(60 s) and polls every user who is due (`users.poll_minutes`: 1, 3, 5, 15, 30
or 60 minutes, default 1). Standalone it runs while `libre-mcp stdio` runs;
`libre-mcp collect` does one round for a scheduled job.

Two kinds of values, kept apart on purpose:

- **`readings`** - the 5-minute history (graph data, and record type 0 from the
  LibreView CSV). Statistics, coverage and the tools use only these, so shares
  stay shares of evenly spaced values. The same reading arrives many times
  (every poll sees 12 hours again, a CSV overlaps); a reading counts as known
  when one exists within 120 seconds of it, since the CSV has minutes only.
- **`realtime`** - the current value at every poll, about one per minute. These
  minute values exist only where somebody asked at that minute; LibreLinkUp
  never hands them out later. Only other apps use them (`internal.py`).

Scans from the CSV are never imported: they are extra spot readings taken
whenever somebody looked at the phone, and would weight the statistics
towards those moments. Retention (`users.history_months`, default 24 months,
or unlimited) applies on writing and after every collector round.

## Sensors

A reading carries no sensor: neither LibreLinkUp's graph nor the CSV says
which sensor measured it. Sensors are worn one after the other, so time
decides - a reading belongs to the sensor with the latest start at or before
it (`sensors.of`). Nothing is stored per reading, so a sensor found later
sorts the history by itself.

- **Reported** (`llu`): LibreLinkUp names the current sensor with serial
  number and exact activation at every poll. Facts, never recomputed.
- **Inferred** (`gap`, `first`): a sensor change leaves a gap of at least the
  one-hour warm-up, so a gap of 55 minutes or more (a bit under the hour: the
  CSV has minutes only) that no reported sensor explains starts a new sensor,
  activated one warm-up before the first reading after it. `first` is the
  sensor running when the stored history begins. A gap within a reported
  sensor's time is lost data, not a change.

Inferred sensors are recomputed (`sensors.rebuild`) after a CSV import, when a
new sensor is reported and at every server start - never maintained by hand.
Status: `warming_up`, `running` (a reading within the last three hours; minute
values count as a sign of life), `ended`.

## Statistics

`stats.py` is pure functions on `(ts, mg/dL)` series, following the
international consensus on CGM reporting (Battelino et al., Diabetes Care
2019): time in ranges <54, 54-69, 70-180, 181-250, >250 mg/dL, mean, SD, CV
(stable at 36 % or below), GMI (3.31 + 0.02392 × mean) and coverage (at least
70 % for a period to count). Percentages are shares of readings, which equal
shares of time because the history is evenly spaced.

The daily pattern is the ambulatory glucose profile: percentiles per local
hour. Episodes are runs below or above a threshold lasting at least
`min_minutes`; a gap of more than 30 minutes ends one. Long series are
condensed into clock-aligned buckets with mean, min and max, so an answer
never exceeds `max_points` rows - raw series would eat the context window the
conversation needs.

The GMI shown on the account page and given to other apps is computed over the
user's own GMI period (`users.gmi_days`: 14, 30 or 90 days), whatever period
is on screen; LibreLinkUp delivers no GMI. The display unit (`users.unit`:
like the Libre app, mg/dL or mmol/L) only changes the account page; everything
stored and everything the tools and other apps get stays mg/dL.

## Time zones

Readings are stored in UTC; people ask in their own time. In the hosted mode
MyAuth hands over each user's zone with every answer; standalone it is `$TZ`
or the computer's. The zone sets day boundaries (`2026-09-28` means that local
day), how the LibreView CSV (local times only) is read, and every time a tool
returns. In the autumn hour that exists twice, the first occurrence wins.

## Other apps (hosted)

Other apps behind the same MyAuth can get a user's glucose - for example to
draw it into a workout - but never directly: MyAuth decides which app may have
whose glucose, then asks `/internal/readings` (history and minute values of a
window of at most 36 hours) or `/internal/gmi` here. Two locks without a new
secret: anything carrying `X-Forwarded-For` came through the reverse proxy and
is refused, and the bearer must be this app's own MyAuth key. Details in
[hosted.md](hosted.md).

## OAuth (hosted)

claude.ai offers no field for a static bearer header, so the server is its own
OAuth 2.1 authorization server: discovery (RFC 8414, RFC 9728), dynamic client
registration, PKCE S256, authorization code and refresh grant. The consent
page identifies the user through their MyAuth session; every token and code
carries the MyAuth uid. A refresh only succeeds while MyAuth still allows the
user the app.

On refresh **the refresh token stays the same**: a refresh hands out a new
access token and extends the refresh token's lifetime by another 30 days.
Earlier access tokens stay valid until they expire (at most an hour). That
way one connector used from several places (two Claude sessions, the phone)
never signs itself out by rotating the token another place still holds. The
shared part is the vendored block `mcp_oauth`; `oauth.py` keeps only the
consent and error pages.

## LibreLinkUp quirks worth knowing

- **Unofficial and undocumented.** It is the follower API behind Abbott's
  LibreLinkUp app, the one Nightscout and every other community tool uses. It
  has changed under everybody before (October 2025: 403 for all clients until
  they sent new headers). The app version it expects can be raised with
  `LLU_VERSION` without a release.
- **Read tolerantly.** Only the fields that are used are read, and anything
  malformed is skipped - strict models fail on unexpected items, for example
  an active glucose alarm.
- **Lockouts** come as HTTP 429 or as `"locked"` inside a 200. The server
  pauses that user as asked (at most 15 minutes) and does not retry before.
  A rejected password is never retried: Abbott locks the account after a few
  failed sign-ins.
- **Terms of use, privacy policy, e-mail confirmation** can block the sign-in
  until they are accepted once in the LibreLinkUp app.
- **Lag.** The phone uploads in the background and irregularly, so the newest
  values can be up to half an hour late; every poll fetches 12 hours again, so
  nothing is lost.
- **Privacy in logs.** LibreLinkUp URLs carry the patient id, so `httpx` request
  logging is turned down.

## Vendored code

`access/`, `librelinkup/` and `mcp_oauth/` are complete copies of building
blocks from the author's private repository **MyCommons**, where each block has
its own tests and version (in its `__init__.py`). A script there
(`MyCommons/sync.sh <block> libre_mcp`) copies a block into every project
using it, so a fix is made once. **Never edit them here** - a change made only
in this repo would be overwritten by the next sync; changes go upstream.
`tests/test_access_copy.py` compares the copies with MyCommons when it is
cloned next to this repo, and is skipped otherwise (in CI, and for everyone
else).

- `access/` - MyAuth client: sessions, access checks, the LibreLinkUp session
  and connection state. `__init__.py` calls
  `myauth.configure(app="mcp-libre", platform="LibreLinkUp")`. `access.web` is the browser
  sign-in core and the cross-site check that `web.signed_in` uses.
- `librelinkup/` - the LibreLinkUp client (sign-in, patients, graph), shared
  with MyAuth, which signs in with it.
- `mcp_oauth/` - the shared OAuth 2.1 authorization server (see OAuth above)
  and its addresses (`mcp_oauth.addresses`: PUBLIC_URL, MCP_URL, UI_URL).

`static/ui/` is likewise a copy of **msk-ui**, the design system of the
author's web apps; app styles go into `static/app.css`, never into the copy.

## Tests

122 tests, no network: LibreLinkUp is faked at the HTTP transport (`FakeLLU`,
`llu._client`) and MyAuth at its fetch functions (`FakeMyAuth`:
`access.myauth._fetch_session`, `_fetch_access`, `_fetch_libre`,
`_fetch_connections`). All glucose values are made up - no real exports ever
go into the repository (`*.csv` is ignored). The HTTP surface is driven
through Starlette's `TestClient`, including a full OAuth handshake; the
standalone mode is tested end to end over stdio.

The ones that matter most:

- `test_two_accounts_never_see_each_other` - the tenancy boundary
- `test_revoking_the_app_in_myauth_cuts_the_connector_off` - revocation works
- `test_a_reassigned_uid_loses_everything` - a rebuilt MyAuth cannot leak
- `test_somebody_else_in_myauth_drops_the_old_readings` - no mixed-up glucose
- `test_only_myauth_from_the_docker_network` - the internal endpoints stay internal
- `test_an_active_alarm_and_odd_items_do_not_break_the_read` - tolerant parsing
