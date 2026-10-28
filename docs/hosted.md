# Hosted: running the server

The hosted mode is the multi-user server: an HTTP app
(`uvicorn --factory libre_mcp.server:build_http_app`, what the container runs)
with one connector per user in claude.ai, ChatGPT or Mistral Vibe, a
collector that keeps every user's history, and an account page per user. For
one person on their own computer, the [standalone mode](standalone.md) is
simpler and needs none of this.

## What it needs: MyAuth

The hosted server keeps no accounts, no passwords and no LibreLinkUp
credentials. It signs users in through **MyAuth**, the author's self-hosted
login service, which also holds each user's LibreLinkUp connection (e-mail,
password, the chosen person) and signs in to LibreLinkUp. **MyAuth is not
published**; this guide describes how the author's own instance is set up,
for transparency. To run the hosted mode elsewhere you would need a service
that answers the same endpoints:

| Endpoint | Asked by | Answer |
|---|---|---|
| `GET /api/session?app=<key>` with the `sso_session` cookie | account and consent pages | 200 with the user (`uid`, `username`, `display_name`, `timezone`, optional `impersonated_by`), 401 not signed in or password change pending, 403 not allowed |
| `GET /api/internal/access?uid=&app=<key>` | every MCP call, every token refresh | 200 with the user while they may use the app, else 403 |
| `GET /api/internal/libre/token?uid=&app=<key>[&renew=1]`, bearer = app key | every poll and live read | the user's LibreLinkUp session: `base_url` (region host), `token`, `expires`, `account_id`, `patient_id`, `patient_name`, `account`; 404 not connected; 502 LibreLinkUp refused the sign-in. `renew=1` (after LibreLinkUp answered 401) asks MyAuth to sign in again |
| `GET /api/internal/connections?uid=&app=<key>`, bearer = app key | account page, `whoami` | the connection state without anything secret: `{"libre": {"connected", "account", "last_error"}}` |
| `/login`, `/logout`, `/password`, `/connections`, `/impersonate/stop` (browser pages, each with `?next=`) | links from this server's pages | sign-in, sign-out, password change, connecting LibreLinkUp, ending "sign in as" |

The internal endpoints must only be reachable from the server's own network,
never through the reverse proxy. The code that asks them is in
`libre_mcp/access/myauth.py`; answers are cached for 30 seconds.

In MyAuth the app is registered with a key (`mcp-libre` by default), the port
of its pages and the platform `libre`, and it gets an app key: the same
random value stands in MyAuth's `APP_KEYS` (`{"mcp-libre": "..."}`) and in
this server's `MYAUTH_APP_KEY`. Users need the app enabled in MyAuth.

### What this server offers MyAuth

Other apps behind the same MyAuth can get a user's glucose - for example a
training app that draws it into an activity. They never ask this server
directly: MyAuth checks which app may have whose glucose and then asks here
(`libre_mcp/internal.py`):

| Endpoint | Answer |
|---|---|
| `GET /internal/readings?uid=&from=&to=` (Unix seconds, at most 36 hours) | the stored history (`readings`, 5-minute values) and the collected minute values (`realtime`), mg/dL, plus the user's `display_unit` |
| `GET /internal/gmi?uid=&at=` | the GMI over the user's GMI period up to `at`, with mean, coverage and `period_days` |

Two locks, no extra secret: a request carrying `X-Forwarded-For` (anything
that came through the reverse proxy) is refused, and the bearer must be this
app's own `MYAUTH_APP_KEY`, which only MyAuth knows besides the server. Do
not route `/internal/*` on the public host.

## Two faces

The server can be reached under two addresses, which may be the same:

| Address (example) | Who | Reachable |
|---|---|---|
| `https://mcp.example.com/libre` | AI clients (claude.ai, ChatGPT, Mistral) | public - the AI providers call it from their data centres |
| `https://home.example.com:1031` | your browser: account page, consent page | may stay private (LAN or VPN), next to MyAuth |

Only the endpoints the AI clients call have to be public: the MCP endpoint,
OAuth token and registration, and the metadata. The account and consent pages
are browser pages behind the MyAuth sign-in; they can stay on a private host,
and the OAuth metadata points the clients' browsers there
(`authorization_endpoint` = `UI_URL`). Unset, everything collapses onto the
host of the request.

## Configuration

Everything is an environment variable; [.env.example](../.env.example) lists them.

| Variable | Example | Used for |
|---|---|---|
| `MYAUTH_APP_KEY` | random (`openssl rand -hex 32`), secret | this app's key in MyAuth: fetching the LibreLinkUp sessions, and the lock on `/internal/*`. The server refuses to start without it |
| `MYAUTH_URL` | `http://myauth:8000` | MyAuth as the container reaches it (internal network) |
| `MYAUTH_PUBLIC_PORT` | `1000` | port of MyAuth's browser pages, on the host the browser used |
| `MYAUTH_APP` | `mcp-libre` | this app's key in MyAuth |
| `PUBLIC_URL` | `https://mcp.example.com/libre` | OAuth issuer, token and registration endpoints |
| `MCP_URL` | the same | connector address, protected-resource metadata |
| `UI_URL` | `https://home.example.com:1031` | `authorization_endpoint` - the consent page, a browser page |
| `FOOTER_NOTE` | `Private service · Owner: ...` | optional text after the version in the page footer |
| `TZ` | `Europe/Berlin` | fallback only; each user's time zone comes from MyAuth |
| `POLL_INTERVAL_S` | `60` (default) | how often the collector wakes up; each user is asked as often as they chose. `0` switches the collector off |
| `LLU_VERSION` | `4.16.0` | the LibreLinkUp app version the client sends; see "When Abbott changes the interface" |

Also set by the image: `MCP_DB=/data/app.db`, the SQLite file in the volume.

## Container

[docker-compose.yml](../docker-compose.yml) builds the image and keeps `/data`
in a volume. It has no host port: put it behind your reverse proxy, in the
same Docker network as MyAuth. A `docker-compose.override.yml` next to it
(compose reads it automatically; it is git-ignored) adds that network, e.g.:

```yaml
services:
  mcp-libre:
    networks:
      - myauth_net

networks:
  myauth_net:
    external: true
```

Then:

```bash
cp .env.example .env        # fill in MYAUTH_APP_KEY and the addresses
docker compose up -d --build
```

Updating is the same command after pulling the new code.

## Reverse proxy

Private site (account and consent pages), Caddy as an example:

```
home.example.com:1031 {
	reverse_proxy mcp-libre:8000
	encode gzip
}
```

Public site, when the server lives under a path prefix (`/libre`) on a host
shared with other MCP servers:

| Public path | To the app | Why |
|---|---|---|
| `/libre` | `/mcp` | the MCP endpoint (the connector address) |
| `/libre/oauth/token`, `/libre/oauth/register` | same, prefix stripped | code exchange, refresh, client registration |
| `/libre/.well-known/oauth-*` | same, prefix stripped | metadata, for clients that append |
| `/.well-known/oauth-protected-resource/libre` | `/.well-known/oauth-protected-resource` | RFC 9728 location (path inserted) |
| `/.well-known/oauth-authorization-server/libre` | `/.well-known/oauth-authorization-server` | RFC 8414 location (path inserted) |

The two `.well-known` forms with the path after `.well-known` are what lets
several MCP servers share one host, each with its own metadata. Everything
else - account page, consent page, `/internal/*` - should answer 404 on the
public host.

Check afterwards, from anywhere:
`https://mcp.example.com/.well-known/oauth-protected-resource/libre` shows the
metadata, `https://mcp.example.com/libre` answers 401 (no token),
`https://mcp.example.com/libre/account` answers 404.

## The collector

It runs inside the server process: 30 seconds after the start, then every
`POLL_INTERVAL_S` it asks LibreLinkUp for the 12-hour graph of every user who
is due - each user picks 1, 3, 5, 15, 30 or 60 minutes on the account page
(default 1). Only polling every minute keeps the minute values (the live
value the sensor shows), which LibreLinkUp never hands out later. Each poll
uses the session MyAuth hands out; when LibreLinkUp answers 401 the server
asks again with `renew=1`, and MyAuth signs in anew. After each round,
readings older than each user's retention are deleted. The state per user
(last success, last error) is on the account page and in `whoami`.

- **Password rejected** (when MyAuth signs in again): MyAuth discards it,
  collecting stops, and MyAuth's account page asks for a new connection. Never
  retried: Abbott locks the account after a few failed sign-ins.
- **Abbott locks briefly** (HTTP 429 or "locked"): the server pauses that user
  as asked (at most 15 minutes), then continues.
- **New terms of use:** open the LibreLinkUp app once and accept them; MyAuth
  tries again hourly, and everything continues by itself.

## When Abbott changes the interface

Signs: the account page shows "LibreLinkUp answered with HTTP 403" (or
similar) for all users at once. Most often Abbott wants a newer app version.
Check what the community changed -
[nightscout-connect](https://github.com/nightscout/nightscout-connect) and
[pylibrelinkup](https://github.com/robberwick/pylibrelinkup) - and mirror it
in the LibreLinkUp client (`libre_mcp/librelinkup/`, a vendored building
block, see [architecture.md](architecture.md)) and in MyAuth's sign-in. If it
is only the version: set `LLU_VERSION` (here and in MyAuth) and restart, no
release needed.

## Data and backup

Everything is in one SQLite file in the volume (`/data/app.db`): users and
their settings, the readings (with whose glucose they are and the target
range), the minute values, the sensors, OAuth clients and tokens. The
LibreLinkUp connection is MyAuth's. A Libre 3 delivers a reading every five
minutes, a good 100,000 per person and year - the file stays small anyway.
Back up the volume and the `.env` with `MYAUTH_APP_KEY`.

At startup the server recomputes the estimated sensors (inferred from gaps in
the history) for all users; the sensors LibreLinkUp reported stay as they
are. Retention deletes a sensor once none of its readings is left.

## Who may use it

Only MyAuth decides: a user needs the app enabled there. Revoking it or
blocking the user cuts their connectors off within the 30-second cache. There
is nothing to administer on this server; each user can delete their own data
on the account page. If MyAuth reports a different person for a user's
LibreLinkUp connection, that user's stored readings are dropped: they are
somebody else's glucose.

## Troubleshooting

- **Account page says "Sign-in unavailable", MCP answers 503:** MyAuth is down
  or not in the server's network (`MYAUTH_URL`).
- **The consent page does not load when adding a connector:** the browser
  cannot reach `UI_URL` or MyAuth - in a private setup, the device is not on
  the LAN or VPN.
- **"Nobody shares their glucose with this account":** the invitation from the
  FreeStyle Libre app has not been accepted in LibreLinkUp yet.
- **The newest readings are missing:** the phone app uploads irregularly in
  the background, so LibreLinkUp can lag up to half an hour behind. Nothing is
  lost: every poll fetches the last 12 hours again.
- **Statistics with a low `coverage_pct`:** the period lies (partly) before the
  connection - import the LibreView CSV on the account page.
- **Everything public answers nothing:** check the public host and the reverse
  proxy routes above.
- **Logs:** `docker logs --tail 50 mcp-libre`.
