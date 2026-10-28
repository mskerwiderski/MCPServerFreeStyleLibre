"""The small web surface: the account page (LibreLinkUp state from MyAuth),
history settings and the CSV import, and the page shell every HTML response shares
(msk-ui, the author's design system). Plain HTML strings, no
template engine - this is a handful of pages.

Nobody signs in here. `signed_in` asks MyAuth who the browser belongs to; the
sign-in page, the user's account and the log-out are MyAuth's.

The shell, the address helpers and `signed_in` are copied from
MCPServerGarminConnect (garmin_mcp/web.py); fixes go to both.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import os
from pathlib import Path
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import __version__, csv_import, libre, readings, sensors, stats, times, users
from .access import myauth

STATIC_DIR = Path(__file__).parent / "static"
MAX_CSV_BYTES = 50 * 1024 * 1024
_asset_cache: dict[str, str] = {}

APP_NAME = "Libre MCP"

LOGO = """<svg class="brand-logo" viewBox="0 0 64 64" aria-hidden="true">
<defs><linearGradient id="hdrGrad" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#4f8cff"/><stop offset="1" stop-color="#7b6cff"/></linearGradient></defs>
<rect x="4.5" y="4.5" width="55" height="55" rx="15" fill="#0e141d" stroke="url(#hdrGrad)" stroke-width="3"/>
<path d="M32 13.5C26 21.5 20 28.5 20 36.5a12 12 0 0 0 24 0C44 28.5 38 21.5 32 13.5Z"
 fill="#0e141d" stroke="#0bb4a6" stroke-width="4" stroke-linejoin="round"/>
<polyline points="25,39 29.5,35 33.5,38 39,32" fill="none" stroke="#7b6cff"
 stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="39" cy="32" r="2.2" fill="#4f8cff"/></svg>"""

_ICON_DROP = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor"'
              ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
              '<path d="M12 3.5c-3 4-6 7.5-6 11a6 6 0 0 0 12 0c0-3.5-3-7-6-11Z"/>'
              '<path d="M9 15.5l2-1.8 1.8 1.4 2.2-2.6"/></g></svg>')
_ICON_USER = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor"'
              ' stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="8.5" r="3.8"/>'
              '<path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></g></svg>')
_ICON_LOGOUT = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor"'
                ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M9.5 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3.5"/>'
                '<path d="M15 8l4 4-4 4M19 12H9.5"/></g></svg>')


def esc(s: object) -> str:
    return html_lib.escape(str(s), quote=True)


def asset(rel: str) -> str:
    """Static path with a content hash as ?v= (cache busting)."""
    if rel not in _asset_cache:
        try:
            v = hashlib.md5((STATIC_DIR / rel).read_bytes()).hexdigest()[:8]
        except OSError:
            v = "0"
        _asset_cache[rel] = f"/static/{rel}?v={v}"
    return _asset_cache[rel]


# --- addresses ---------------------------------------------------------------
#
# A hosted server can have two faces (docs/hosted.md):
#   PUBLIC_URL  e.g. https://mcp.example.com/libre
#               issuer and OAuth endpoints, public for the AI clients
#   MCP_URL     the connector address; often the same URL, mapped to /mcp
#   UI_URL      e.g. https://home.example.com:1031
#               account and consent pages, may stay private
# Unset, everything collapses onto the origin of the request (one host).


def _env_url(name: str) -> str:
    return os.environ.get(name, "").rstrip("/")


def request_origin(request: Request) -> str:
    """The origin this browser used - a private host name, a LAN IP, or the
    single host of a simple setup."""
    proto = request.headers.get("x-forwarded-proto", "https")
    return f"{proto}://{request.headers.get('host', 'localhost')}"


def public_base(request: Request) -> str:
    return _env_url("PUBLIC_URL") or request_origin(request)


def mcp_url(request: Request) -> str:
    return _env_url("MCP_URL") or f"{public_base(request)}/mcp"


def ui_base(request: Request) -> str:
    return _env_url("UI_URL") or public_base(request)


def well_known(url: str, name: str) -> str:
    """RFC 8414 / RFC 9728: /.well-known/<name> goes between host and path,
    so every app behind one public host keeps its own metadata."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/.well-known/{name}{p.path.rstrip('/')}"


def page(request: Request, title: str, body: str, status: int = 200,
         user: dict | None = None) -> HTMLResponse:
    nav = tabbar = banner = ""
    if user:
        base = request_origin(request)
        account = esc(myauth.public_url(base, "/"))
        logout = esc(myauth.public_url(base, "/logout?next=/login"))
        name = esc(user.get("display_name") or user.get("username") or "")
        nav = (f'<span class="nav-user">{name} <a href="{account}">Account</a>'
               f' <a href="{logout}">Log out</a></span>')
        imp = user.get("impersonated_by")
        if imp:
            # an admin signed in as this user ("sign in as" in MyAuth) - also on
            # the consent page, where "Allow" would count for this user
            admin = esc(imp.get("display_name") or imp.get("username") or "")
            back = _current_url(request) if request.method == "GET" else f"{base}/account"
            stop = esc(myauth.stop_impersonating_url(base, back))
            banner = (f'<div class="imp-banner" role="status"><span>You are signed in as '
                      f'{name} - on behalf of {admin}.</span>'
                      f'<a class="btn btn-sm" href="{stop}">Sign out as {name}</a></div>\n')
        here = "active" if request.url.path.startswith("/account") else ""
        tabbar = (f'<nav class="tabbar-bottom" aria-label="Main navigation">'
                  f'<a class="tab {here}" href="/account">{_ICON_DROP}<span>{APP_NAME}</span></a>'
                  f'<a class="tab" href="{account}">{_ICON_USER}<span>Account</span></a>'
                  f'<a class="tab" href="{logout}">{_ICON_LOGOUT}<span>Log out</span></a></nav>')
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#0a0e13" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#f3f5f9" media="(prefers-color-scheme: light)">
<title>{APP_NAME} · {esc(title)}</title>
<link rel="icon" type="image/svg+xml" href="{asset('favicon.svg')}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{APP_NAME}">
<link rel="apple-touch-icon" href="{asset('apple-touch-icon.png')}">
<link rel="manifest" href="{asset('site.webmanifest')}">
<link rel="stylesheet" href="{asset('ui/tokens.css')}">
<link rel="stylesheet" href="{asset('ui/components.css')}">
<link rel="stylesheet" href="{asset('app.css')}">
</head>
<body>
{banner}<header><a href="/account" class="brand">{LOGO}{APP_NAME}</a><nav>{nav}</nav></header>
<main>{body}</main>
{tabbar}
<footer>{APP_NAME} v{__version__}{_footer_note()}</footer>
</body>
</html>""", status_code=status)


def _footer_note() -> str:
    """FOOTER_NOTE (e.g. "Private service · Owner: ...") follows the version."""
    note = os.environ.get("FOOTER_NOTE", "").strip()
    return f" · {esc(note)}" if note else ""


# --- who is asking -----------------------------------------------------------


def _current_url(request: Request) -> str:
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{request_origin(request)}{request.url.path}{query}"


def _cross_site(request: Request) -> bool:
    """Apps behind one MyAuth usually share a host name, and cookies do not separate
    ports - a form on another app's page would arrive here with the MyAuth
    cookie. The Origin header is what tells them apart."""
    origin = request.headers.get("origin")
    return bool(origin) and origin.rstrip("/") != request_origin(request)


async def signed_in(request: Request) -> tuple[dict | None, Response | None]:
    """The MyAuth user behind this browser, with their local row brought up to
    date - or the response to send instead: MyAuth's sign-in page, "no access",
    or "sign-in unavailable"."""
    if request.method == "POST" and _cross_site(request):
        return None, page(request, "Refused", "<h1>Refused</h1><p>This form was sent "
                          "from another site.</p>", 403)
    try:
        status, body = await myauth.session_of(request.cookies.get(myauth.SSO_COOKIE))
    except myauth.Unavailable:
        return None, page(request, "Sign-in unavailable",
                          "<h1>Sign-in unavailable</h1><p>MyAuth, the sign-in service, "
                          "is not answering. Please try again in a moment.</p>", 503)
    if status == 200 and "uid" in body:
        users.ensure_user(int(body["uid"]), str(body.get("username") or ""),
                          str(body.get("display_name") or ""))
        times.use(body.get("timezone"))
        return body, None
    if status == 403:
        account = esc(myauth.public_url(request_origin(request), "/"))
        return None, page(request, "No access",
                          "<h1>No access</h1><p>Your MyAuth account is not enabled for "
                          f"{APP_NAME}. An administrator can change that in "
                          f'<a href="{account}">MyAuth</a>.</p>', 403)
    target = myauth.login_url(request_origin(request), _current_url(request),
                              password_change=body.get("detail") == "password_change_required")
    if request.method in ("GET", "HEAD"):
        return None, RedirectResponse(target, status_code=303)
    return None, page(request, "Signed out", "<h1>Signed out</h1><p>Your MyAuth session "
                      f'has ended. <a href="{esc(target)}">Sign in again</a>.</p>', 401)


# --- account -----------------------------------------------------------------


# Live account page: every minute (and when the tab becomes visible again) the
# LibreLinkUp, statistics and sensor cards are fetched as HTML from /account/live
# and swapped in; the period picker does the same instead of reloading. Pauses
# while the tab is hidden. Without JavaScript the page still works (GET form).
_LIVE_JS = """<script>
(function () {
  var REFRESH_MS = 60000, timer = null;
  var picker = function () { return document.querySelector('#stats select[name=period]'); };
  function period() { var s = picker(); return s ? s.value : ''; }
  function load() {
    fetch('/account/live?period=' + encodeURIComponent(period()),
          {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        ['libre', 'stats', 'sensor'].forEach(function (k) {
          var el = document.getElementById(k + '-body');
          if (el && d[k] != null) el.innerHTML = d[k];
        });
        var u = document.getElementById('live-updated');
        if (u) u.textContent = 'Updated ' + d.updated;
        wire();
      }).catch(function () {});
  }
  function wire() {
    var s = picker();
    if (!s || s.dataset.live) return;
    s.dataset.live = '1';
    s.removeAttribute('onchange');
    s.addEventListener('change', function () {
      history.replaceState(null, '', '/account?period=' + encodeURIComponent(s.value) + '#stats');
      load();
    });
  }
  function start() { stop(); timer = setInterval(load, REFRESH_MS); }
  function stop() { if (timer) clearInterval(timer); timer = null; }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); } else { load(); start(); }
  });
  wire();
  start();
})();
</script>"""


_COPY_JS = """<script>
function copyText(id, btn) {
  const field = document.getElementById(id);
  navigator.clipboard.writeText(field.value).then(function () {
    const before = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(function () { btn.textContent = before; }, 1500);
  }, function () { field.select(); });
}
</script>"""


def _months_label(m: int) -> str:
    if m == 0:
        return "no limit"
    return f"{m // 12} years" if m >= 24 and m % 12 == 0 else (
        "1 year" if m == 12 else f"{m} months")


async def _libre_card(request: Request, uid: int) -> str:
    """The connection is MyAuth's; here its state, whose glucose, and what the
    collector did."""
    conn = await libre.state(uid)
    acc = libre.account(uid) or {}
    last = readings.latest(uid)
    manage = esc(myauth.public_url(request_origin(request), "/connections#pf-libre"))
    rows = []
    if conn.get("connected") is None:
        rows.append('<div class="status-line"><span class="badge muted">unknown</span>'
                    ' MyAuth is not answering right now.</div>')
    elif conn.get("connected"):
        rows.append('<div class="status-line"><span class="badge ok">connected</span>'
                    ' in MyAuth</div>')
        if acc.get("patient_name") or conn.get("account"):
            rows.append(f'<p>Reads the glucose of <b>'
                        f'{esc(acc.get("patient_name") or conn.get("account"))}</b>.</p>')
        if conn.get("last_error"):
            rows.append(f'<p class="error">MyAuth: {esc(conn["last_error"])}</p>')
    else:
        rows.append('<div class="status-line"><span class="badge warn">not connected</span></div>'
                    '<p class="muted">LibreLinkUp is connected once in MyAuth, for every app '
                    'that reads glucose. Nothing is collected until then.</p>')
    live = readings.realtime_latest(uid)
    unit = users.display_unit(uid)
    if live and (not last or live[0] > last[0]):
        rows.append(f'<p class="muted">Last reading: <b>{_g(live[1], unit)} {UNIT_LABEL[unit]}</b> at '
                    f'{esc(times.fmt(live[0]).replace("T", " "))} <span class="small">(minute value; '
                    f'the 5-minute history follows about 20 minutes later)</span></p>')
    elif last:
        rows.append(f'<p class="muted">Last reading: <b>{_g(last[1], unit)} {UNIT_LABEL[unit]}</b> at '
                    f'{esc(times.fmt(last[0]).replace("T", " "))}</p>')
    if conn.get("connected") and acc.get("last_error"):
        rows.append(f'<p class="error">Last attempt failed: {esc(acc["last_error"])}</p>')
    label = "Manage in MyAuth" if conn.get("connected") else "Connect LibreLinkUp in MyAuth"
    css = "btn-ghost" if conn.get("connected") else "btn"
    rows.append(f'<a class="{css}" href="{manage}">{label} ↗</a>')
    every = users.poll_minutes(uid)
    options = "".join(f'<option value="{m}"{" selected" if m == every else ""}>'
                      f'{"1 minute" if m == 1 else f"{m} minutes"}</option>'
                      for m in users.POLL_CHOICES)
    rows.append(f"""<form method="post" action="/account/poll" class="form-inline">
<label>Ask LibreLinkUp every <select name="minutes">{options}</select></label>
<button class="btn-ghost">Save</button></form>
<p class="hint">Every minute keeps the minute values - the live value the sensor shows,
which LibreLinkUp never hands out later (other apps can put them into activities).
Longer intervals keep only the 5-minute history, which nothing loses up to 12 hours.</p>""")
    return "".join(rows)


_TREND = {1: "↓", 2: "↘", 3: "→", 4: "↗", 5: "↑"}

# Display unit (stored values stay mg/dL): users.display_unit.
MG_PER_MMOL = 18.0156
UNIT_LABEL = {"mgdl": "mg/dL", "mmol": "mmol/L"}


def _g(mg, unit: str) -> str:
    """A glucose value in the display unit, without the unit."""
    if mg is None or mg == "?":
        return "?"
    return f"{float(mg) / MG_PER_MMOL:.1f}" if unit == "mmol" else str(round(float(mg)))


def _when(ts: int | None) -> str:
    return esc((times.fmt(ts) or "").replace("T", " "))


def _sensor_card(uid: int) -> str:
    """The sensor worn now - with what LibreLinkUp tells about it and the Libre
    app on the phone - and every sensor before it (sensors.py)."""
    import json
    acc = libre.account(uid) or {}
    try:
        info = json.loads(acc.get("sensor_info") or "{}")
    except ValueError:
        info = {}
    worn = sensors.history(uid)
    if not worn and not info:
        return '<p class="muted">No sensor details yet - they come with the next poll.</p>'
    rows = []
    unit = users.display_unit(uid)
    if info.get("current_mg") is not None:
        arrow = _TREND.get(info.get("trend"), "")
        rows.append(("Current value", f'<b>{_g(info["current_mg"], unit)} {UNIT_LABEL[unit]} {arrow}</b> at '
                     f'{_when(info.get("current_ts"))}'))
    cur = worn[-1] if worn else None
    if cur and cur["serial"]:
        rows.append(("Serial number", f'<code>{esc(cur["serial"])}</code>'))
    if cur:
        days = cur["days"]
        state = {"running": f"day {int(days) + 1} ({days:.1f} days in use; a Libre 3 lasts "
                            f"14 days, a Libre 3 Plus 15)",
                 "warming_up": "warming up",
                 "ended": f"ended after {days:.1f} days"}[cur["status"]]
        rows.append(("Activated", f'{"" if cur["source"] == "llu" else "≈ "}{_when(cur["started_s"])} '
                     f'<span class="muted">· sensor {cur["number"]} · {state}</span>'))
    if info.get("warmup_min"):
        rows.append(("Warm-up", f'{info["warmup_min"]} minutes'))
    if info.get("type_code") is not None:
        rows.append(("Sensor type code", f'{esc(info["type_code"])} '
                     '<span class="muted">(LibreLinkUp\'s own code)</span>'))
    if info.get("app_version"):
        rows.append(("Libre app on the phone", f'version {esc(info["app_version"])}'))
    if info.get("alarm_low") or info.get("alarm_high"):
        state = "on" if info.get("alarms") else "off"
        rows.append(("Alarms in the app", f'{state} · low {_g(info.get("alarm_low", "?"), unit)}, '
                     f'high {_g(info.get("alarm_high", "?"), unit)} {UNIT_LABEL[unit]}'))
    return ('<table class="kv">' + "".join(
        f'<tr><td class="muted">{k}</td><td>{v}</td></tr>' for k, v in rows) + "</table>"
        + _sensor_history(worn, unit))


def _sensor_history(worn: list[dict], unit: str) -> str:
    """Every stored sensor, newest first: when it ran and what it measured."""
    if not worn:
        return ""
    badge = {"running": '<span class="badge ok">running</span>',
             "warming_up": '<span class="badge muted">warming up</span>'}
    body = []
    for s in reversed(worn):
        estimated = "" if s["source"] == "llu" else "≈ "
        figures = (f'<td class="num">{_g(s["mean"], unit)}</td><td class="num">{s["in_range_pct"]} %</td>'
                   if s["readings"] else '<td class="num muted">-</td><td class="num muted">-</td>')
        serial = (f'<code>{esc(s["serial"])}</code>' if s["serial"]
                  else '<span class="muted">-</span>')
        body.append(
            f'<tr><td class="num">{s["number"]}</td><td>{estimated}{_when(s["started_s"])}</td>'
            f'<td>{badge.get(s["status"]) or _when(s["last_s"]) or "-"}</td>'
            f'<td class="num">{s["days"]:.1f}</td>{figures}<td>{serial}</td></tr>')
    u = UNIT_LABEL[unit]
    notes = [f"Mean in {u}; in range = {_g(70, unit)}-{_g(180, unit)} {u}. A Libre 3 lasts 14 days, "
             "a Libre 3 Plus 15 - one that ended clearly earlier failed or came off."]
    if any(s["source"] != "llu" for s in worn):
        notes.append("≈ estimated: worn before this server knew the sensor (LibreView import or "
                     "older history). A new sensor leaves a gap of at least an hour, so its start "
                     "is the first reading after such a gap minus the warm-up hour; there is no "
                     "serial number.")
    if worn[0]["source"] == "first":
        notes.append("The first sensor was already running when the stored history begins and "
                     "may have started earlier.")
    return (f'<h3>All sensors ({len(worn)})</h3><div class="tablewrap"><table class="sensors">'
            '<thead><tr><th class="num">#</th><th>Started</th><th>Last reading</th>'
            '<th class="num">Days</th><th class="num">Mean</th><th class="num">In range</th>'
            '<th>Serial number</th></tr></thead><tbody>' + "".join(body) + '</tbody></table></div>'
            + "".join(f'<p class="muted small">{n}</p>' for n in notes))


# Statistics period filter: (key, label, seconds). Default: the last 24 hours.
_MONTH_S = 30.4375 * 86400
PERIODS = (("12m", "12 months", int(12 * _MONTH_S)), ("6m", "6 months", int(6 * _MONTH_S)),
           ("3m", "3 months", int(3 * _MONTH_S)), ("4w", "4 weeks", 28 * 86400),
           ("2w", "2 weeks", 14 * 86400), ("7d", "7 days", 7 * 86400),
           ("3d", "3 days", 3 * 86400), ("2d", "2 days", 2 * 86400),
           ("24h", "24 hours", 86400), ("12h", "12 hours", 12 * 3600),
           ("6h", "6 hours", 6 * 3600), ("3h", "3 hours", 3 * 3600),
           ("2h", "2 hours", 2 * 3600), ("1h", "1 hour", 3600))
DEFAULT_PERIOD = "24h"
CHART_POINTS = 400


def _period(key: str | None) -> tuple[str, str, int]:
    return next((p for p in PERIODS if p[0] == key),
                next(p for p in PERIODS if p[0] == DEFAULT_PERIOD))


def _tick_label(ts: int, span_s: int) -> str:
    from datetime import datetime
    d = datetime.fromtimestamp(ts, times.zone())
    if span_s <= 2 * 86400:
        return d.strftime("%H:%M")
    if span_s <= 120 * 86400:
        return d.strftime("%d %b").lstrip("0")
    return d.strftime("%b %y")


def _chart(points: list[tuple[int, int]], start: int, end: int,
           target: tuple[int, int], tail: list[tuple[int, int]] = (), unit: str = "mgdl",
           changes: list[tuple[int, int]] = ()) -> str:
    """Inline SVG for the period (no script): target band, the glucose line broken
    at gaps, five time marks. Long periods are averaged into buckets (beyond a
    month: days) - the line is the mean, a light band behind it the min-max
    range of each bucket. `tail`: minute values after the last history point
    (LibreLinkUp's history lags ~20 min), drawn apart up to now. `changes`:
    (start, number) of the sensors started in the period, a dotted line each."""
    W, H, L, B, T = 600, 160, 34, 18, 6
    span = max(1, end - start)
    # beyond a month one point per day: finer buckets only draw the daily rhythm
    bucket, rows = stats.downsample(
        points, CHART_POINTS if span <= 31 * 86400 else max(31, span // 86400))
    top = max(250, max((r[-1] if bucket else r[1] for r in rows), default=0) + 10)
    lo_v = 40
    gap_s = max(30 * 60, 2 * bucket)

    def x(ts):
        return L + (ts - start) / span * (W - L - 4)

    def y(mg):
        mg = max(lo_v, min(top, mg))
        return T + (top - mg) / (top - lo_v) * (H - T - B)

    segs, cur, prev = [], [], None
    for r in rows:
        ts = r[0] + bucket // 2
        if prev is not None and ts - prev > gap_s:
            segs.append(cur); cur = []
        cur.append(r if not bucket else [ts, *r[1:]])
        prev = ts
    if cur:
        segs.append(cur)
    parts = []
    if bucket:                                          # min-max band per segment
        for seg in segs:
            if len(seg) > 1:
                upper = " ".join(f"{x(r[0]):.1f},{y(r[3]):.1f}" for r in seg)
                lower = " ".join(f"{x(r[0]):.1f},{y(r[2]):.1f}" for r in reversed(seg))
                parts.append(f'<polygon class="g-range" points="{upper} {lower}"/>')
    for seg in segs:
        pts = " ".join(f"{x(r[0]):.1f},{y(r[1]):.1f}" for r in seg)
        parts.append(f'<polyline class="g-line" points="{pts}"/>' if len(seg) > 1 else
                     f'<circle class="g-dot" cx="{x(seg[0][0]):.1f}" cy="{y(seg[0][1]):.1f}" r="2"/>')
    if tail:                                            # minute values, broken at gaps too
        seq = list(tail)
        if points and seq[0][0] - points[-1][0] <= gap_s:
            seq.insert(0, points[-1])                       # joins the history line
        runs, run = [], [seq[0]]
        for a, b in zip(seq, seq[1:]):
            if b[0] - a[0] > gap_s:
                runs.append(run); run = []
            run.append(b)
        runs.append(run)
        for run in runs:
            parts.append('<polyline class="g-live" points="'
                         + " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in run) + '"/>'
                         if len(run) > 1 else
                         f'<circle class="g-live-dot" cx="{x(run[0][0]):.1f}" '
                         f'cy="{y(run[0][1]):.1f}" r="2.5"/>')
    tlo, thi = target
    band = (f'<rect class="g-band" x="{L}" y="{y(thi):.1f}" width="{W - L - 4}" '
            f'height="{y(tlo) - y(thi):.1f}"/>')
    for ts, number in changes:                          # labelled while they fit
        band += f'<line class="g-sensor" x1="{x(ts):.1f}" x2="{x(ts):.1f}" y1="{T}" y2="{H - B}"/>'
        if len(changes) <= 12:
            anchor = "end" if x(ts) > W - 30 else "start"
            band += (f'<text class="g-lab" x="{x(ts) + (-3 if anchor == "end" else 3):.1f}" '
                     f'y="{T + 9}" text-anchor="{anchor}">#{number}</text>')
    ylab = "".join(f'<text class="g-lab" x="{L - 4}" y="{y(v) + 3:.1f}" text-anchor="end">{_g(v, unit)}</text>'
                   f'<line class="g-grid" x1="{L}" x2="{W - 4}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                   for v in (tlo, thi))
    ticks = [start + span * i // 4 for i in range(5)]
    xlab = "".join(f'<text class="g-lab" x="{x(t):.1f}" y="{H - 4}" text-anchor="'
                   f'{"start" if i == 0 else "end" if i == len(ticks) - 1 else "middle"}">'
                   f'{esc(_tick_label(t, span))}</text>' for i, t in enumerate(ticks))
    return (f'<svg class="g24" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="Glucose over the chosen period">{band}{ylab}{"".join(parts)}{xlab}</svg>')


def _stats_card(uid: int, period_key: str | None) -> str:
    """Figures and a small chart for the chosen period (5-minute history)."""
    key, label, span = _period(period_key)
    options = "".join(f'<option value="{k}"{" selected" if k == key else ""}>Last {lab}</option>'
                      for k, lab, _ in PERIODS)
    chosen = users.unit(uid)
    unit = users.display_unit(uid)
    auto = f"Like the Libre app ({UNIT_LABEL[unit]})" if chosen == "auto" else "Like the Libre app"
    units = "".join(f'<option value="{k}"{" selected" if k == chosen else ""}>{lab}</option>'
                    for k, lab in (("auto", auto), ("mgdl", "mg/dL"), ("mmol", "mmol/L")))
    gmi_days = users.gmi_days(uid)
    gmis = "".join(f'<option value="{d}"{" selected" if d == gmi_days else ""}>{d} days</option>'
                   for d in users.GMI_CHOICES)
    picker = (f'<div class="pickers"><form method="get" action="/account#stats" class="form-inline">'
              f'<label>Show <select name="period" onchange="this.form.submit()">{options}'
              f'</select></label><noscript><button class="btn-ghost">Show</button></noscript></form>'
              f'<form method="post" action="/account/unit" class="form-inline">'
              f'<input type="hidden" name="period" value="{key}">'
              f'<label>Unit <select name="unit" onchange="this.form.submit()">{units}</select></label>'
              f'<noscript><button class="btn-ghost">Save</button></noscript></form>'
              f'<form method="post" action="/account/gmi" class="form-inline">'
              f'<input type="hidden" name="period" value="{key}">'
              f'<label>GMI over <select name="days" onchange="this.form.submit()">{gmis}</select></label>'
              f'<noscript><button class="btn-ghost">Save</button></noscript></form></div>')
    acc = libre.account(uid) or {}
    end = int(libre.now_ms() / 1000)
    start = end - span
    points = readings.between(uid, start, end + 1)
    if not points:
        return picker + f'<p class="muted">No readings in the last {label}.</p>'
    # up to now: the minute values after the last history point (short periods)
    tail = (readings.realtime_between(uid, points[-1][0] + 1, end + 1)
            if span <= 2 * 86400 else [])
    target = (acc.get("target_low") or 70, acc.get("target_high") or 180)
    changes = [(w["started_s"], w["number"]) for w in sensors.all_of(uid)
               if start < w["started_s"] <= end]
    s = stats.summarize(points, span, target)
    tir = s["time_in_ranges_pct"]
    below = round(tir["very_low"] + tir["low"], 1)
    above = round(tir["high"] + tir["very_high"], 1)
    u = UNIT_LABEL[unit]
    tiles = [("Mean", f'{_g(s["mean"], unit)} {u}'),
             (f"In range {_g(70, unit)}-{_g(180, unit)}", f'{tir["in_range"]} %'),
             (f"Below {_g(70, unit)}", f"{below} %"), (f"Above {_g(180, unit)}", f"{above} %"),
             (f"Min / max ({u})", f'{_g(s["min"], unit)} / {_g(s["max"], unit)}'),
             ("Variability (CV)", f'{s["cv_pct"]} %')]
    if "own_target" in s and tuple(target) != (70, 180):
        tiles.insert(2, (f'Own target {_g(target[0], unit)}-{_g(target[1], unit)}',
                         f'{s["own_target"]["in_target_pct"]} %'))
    # "The" GMI: always over the user's GMI period up to now, whatever the period
    # picker shows - the same value other apps get (internal.get_gmi).
    g = stats.summarize(readings.between(uid, end - gmi_days * 86400, end + 1), gmi_days * 86400)
    if g["readings"]:
        tiles.append((f"GMI ({gmi_days} days)", f'{g["gmi_pct"]} %'))
    grid = "".join(f'<div class="kpi"><span class="muted small">{k}</span><b>{v}</b></div>'
                   for k, v in tiles)
    gmi_cov = f', {g["coverage_pct"]} % covered' if g["readings"] else ", no readings yet"
    note = (" Long periods are averaged: the line is the mean, the light band the range."
            if len(points) > CHART_POINTS else "")
    live = (f" The dashed end are minute values up to {esc((times.fmt(tail[-1][0]) or '')[11:16])}"
            f" - the history arrives about 20 minutes later; the figures use the history only."
            if tail else "")
    worn = (" Dotted vertical lines mark a new sensor (numbers as under Sensor)."
            if changes else "")
    return (f'{picker}<div class="kpis">{grid}</div>'
            f'{_chart(points, start, end, target, tail, unit, changes)}'
            f'<p class="muted small">{s["readings"]:,} readings, {s["coverage_pct"]} % of the '
            f'period covered. The green band is your target range from the Libre app. '
            f'The GMI (estimated HbA1c) always covers the last {gmi_days} days{gmi_cov}.'
            f'{note}{live}{worn}</p>')


def _history_card(uid: int) -> str:
    s = readings.summary(uid)
    months = users.history_months(uid)
    if s["count"]:
        worn = len(sensors.all_of(uid))
        stored = (f'<p><b>{s["count"]:,}</b> readings from '
                  f'{esc(times.fmt(s["first"])[:10])} to {esc(times.fmt(s["last"])[:10])}, '
                  f'measured by <b>{worn}</b> sensor{"" if worn == 1 else "s"}.</p>')
    else:
        stored = '<p class="muted">No readings stored yet.</p>'
    options = "".join(f'<option value="{m}"{" selected" if m == months else ""}>'
                      f'{_months_label(m)}</option>' for m in users.HISTORY_CHOICES)
    return f"""{stored}
<form method="post" action="/account/history" class="form-inline">
<label>Keep readings for <select name="months">{options}</select></label>
<button class="btn-ghost">Save</button></form>
<p class="hint">Older readings are deleted automatically. Shortening the period
deletes them right away.</p>
<h3>Import from LibreView</h3>
<p class="muted small">LibreLinkUp only shows the last 12 hours, so this server
collects from the day you connect. Your earlier history: on
<a href="https://www.libreview.com" rel="noopener" target="_blank">libreview.com</a>
open <i>Glucose History</i>, choose <i>Download glucose data</i>, and upload the
CSV here. Readings that are already stored are skipped.</p>
<form method="post" action="/account/import" enctype="multipart/form-data" class="form-inline">
<input type="file" name="file" accept=".csv,text/csv" required>
<button class="btn-ghost">Import</button></form>"""


async def get_account(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    uid = int(user["uid"])
    url = mcp_url(request)
    return page(request, "Account", f"""<h1>{APP_NAME}</h1>
<p class="muted small live-note"><span id="live-updated">Updated {esc(_now_label())}</span>
· refreshes every minute</p>
<div class="card"><h2 class="first">LibreLinkUp</h2><div id="libre-body">{await _libre_card(request, uid)}</div></div>
<div class="card" id="stats"><h2 class="first">Statistics</h2><div id="stats-body">{_stats_card(uid, request.query_params.get("period"))}</div></div>
<div class="card"><h2 class="first">Sensor</h2><div id="sensor-body">{_sensor_card(uid)}</div></div>
<div class="card"><h2 class="first">History</h2>{_history_card(uid)}</div>
<div class="card"><h2 class="first">Use it in Claude, ChatGPT or Mistral</h2>
<p>Add a custom connector with this address - the same one for every client,
exactly as shown:</p>
<textarea id="mcp_url" class="connector-url" rows="1" readonly>{esc(url)}</textarea>
<div class="actions"><button type="button" onclick="copyText('mcp_url', this)">Copy address</button></div>
<p class="muted small">Adding it asks you once to confirm access, signed in with MyAuth. Do
that on a device that can reach MyAuth (it may be private). Afterwards the
connector works from anywhere. There is nothing else to enter: no API key, no token.</p>
</div>{_COPY_JS}
<div class="card"><h2 class="first">Your data on this server</h2>
<p class="muted">Every stored reading and the connectors you allowed. Deleting them
disconnects every connector; your MyAuth account and its LibreLinkUp connection
stay as they are.</p>
<form method="post" action="/account/delete"
onsubmit="return confirm('Delete all stored readings and connector authorizations?')">
<button class="btn-danger">Delete my data</button></form></div>{_LIVE_JS}""", user=user)


def _now_label() -> str:
    return (times.fmt(int(libre.now_ms() / 1000)) or "")[11:16]


async def get_live(request: Request) -> Response:
    """The changing cards of the account page as HTML, for _LIVE_JS."""
    from starlette.responses import JSONResponse
    user, err = await signed_in(request)
    if err is not None:
        return JSONResponse({"detail": "signed_out"}, status_code=401)
    uid = int(user["uid"])
    return JSONResponse({"libre": await _libre_card(request, uid),
                         "stats": _stats_card(uid, request.query_params.get("period")),
                         "sensor": _sensor_card(uid), "updated": _now_label()},
                        headers={"Cache-Control": "no-store"})


async def post_delete_account(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    users.delete_user(int(user["uid"]))
    return RedirectResponse("/account", status_code=303)


async def post_history(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    form = await request.form()
    try:
        users.set_history_months(int(user["uid"]), int(str(form.get("months") or "")))
    except ValueError:
        return page(request, "History", "<h1>History</h1><p class=\"error\">Please choose "
                    "one of the offered periods.</p>", 400, user=user)
    readings.prune(int(user["uid"]))
    return RedirectResponse("/account", status_code=303)


async def post_unit(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    form = await request.form()
    try:
        users.set_unit(int(user["uid"]), str(form.get("unit") or ""))
    except ValueError:
        return page(request, "Unit", "<h1>Unit</h1><p class=\"error\">Please choose one of "
                    "the offered units.</p>", 400, user=user)
    period = _period(str(form.get("period") or ""))[0]
    return RedirectResponse(f"/account?period={period}#stats", status_code=303)


async def post_gmi(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    form = await request.form()
    try:
        users.set_gmi_days(int(user["uid"]), int(str(form.get("days") or "")))
    except ValueError:
        return page(request, "GMI", "<h1>GMI</h1><p class=\"error\">Please choose one of "
                    "the offered periods.</p>", 400, user=user)
    period = _period(str(form.get("period") or ""))[0]
    return RedirectResponse(f"/account?period={period}#stats", status_code=303)


async def post_poll(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    form = await request.form()
    try:
        users.set_poll_minutes(int(user["uid"]), int(str(form.get("minutes") or "")))
    except ValueError:
        return page(request, "LibreLinkUp", "<h1>LibreLinkUp</h1><p class=\"error\">Please "
                    "choose one of the offered intervals.</p>", 400, user=user)
    return RedirectResponse("/account", status_code=303)


async def post_import(request: Request) -> Response:
    user, err = await signed_in(request)
    if err is not None:
        return err
    uid = int(user["uid"])
    form = await request.form()
    upload = form.get("file")
    data = await upload.read(MAX_CSV_BYTES + 1) if hasattr(upload, "read") else b""
    if len(data) > MAX_CSV_BYTES:
        message, status = "The file is larger than 50 MB.", 400
    else:
        try:
            points = await asyncio.to_thread(csv_import.parse, data)
        except csv_import.CsvError as exc:
            message, status = str(exc), 400
        else:
            new = await asyncio.to_thread(readings.add, uid, points, "csv")
            await asyncio.to_thread(sensors.rebuild, uid)
            worn = len(sensors.all_of(uid))
            message = (f"{len(points):,} readings in the file, {new:,} of them new. "
                       f"The rest were already stored or are older than your history "
                       f"setting. The history now holds {worn} sensor"
                       f"{'' if worn == 1 else 's'}.")
            status = 200
    css = "" if status == 200 else ' class="error"'
    return page(request, "Import", f"""<h1>Import from LibreView</h1>
<div class="card"><p{css}>{esc(message)}</p>
<a class="btn" href="/account">Back to the account</a></div>""", status, user=user)
