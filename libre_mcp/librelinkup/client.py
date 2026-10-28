"""LibreLinkUp: the follower API behind Abbott's LibreLinkUp app.

Abbott offers no API for individuals; this is the one every community tool
uses (Nightscout, pylibrelinkup, GlucoseDirect). It is undocumented and has
changed under everybody before - in October 2025 all clients got 403 until
they sent the new headers. When that happens again, the fix lands in those
projects first; mirror it here. `LLU_VERSION` overrides the app version
without a release, since that is the value Abbott raises most often.

The MyCommons building block `librelinkup`: one client for everybody who
talks to LibreLinkUp, so Abbott's changes are made once.
  login        sign in to a *follower* account (synchronous), hop to the
               account's region; the person with the sensor shares their
               glucose with that account
  patients     who shares their glucose with the account (synchronous)
  graph        one person's current value and the last ~12 hours (async)
A token lasts about half a year; after that, sign in again. Users: Libre MCP
(graph; `login`/`patients` in its standalone mode) and MyAuth (sign-in).

Kept to a small client of its own rather than pylibrelinkup: that library
has no request timeouts (a hanging call would stall a collector) and
validates responses strictly, so an unexpected field - an active glucose
alarm, of all moments - fails the whole read. Here, only the fields that are
used are read, and anything malformed is skipped.

`_client` (async) and `_sync_client` are the mock points of the tests.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

DEFAULT_VERSION = "4.16.0"
TIMEOUT_S = 20
MAX_WAIT_S = 15 * 60                         # Abbott's lockouts stay below this
LOGIN_URL = "https://api.libreview.io"       # global; redirects to the account's region
STEP_PAUSE_S = 60 * 60                       # terms to accept in the app: ask again hourly
NO_PATIENTS = ("Nobody shares their glucose with this LibreLinkUp account yet. In the "
               "FreeStyle Libre app: Menu > Connected Apps > LibreLinkUp > Add Connection "
               "with this e-mail address, then accept the invitation in the LibreLinkUp app.")
_STEPS = {"tou": "accept the terms of use", "pp": "accept the privacy policy",
          "verifyEmail": "confirm the e-mail address"}


class LibreError(RuntimeError):
    """Something went wrong; the message is meant for the person."""


class AuthExpired(LibreError):
    """The token is no longer accepted - sign in again."""


class SignInRejected(LibreError):
    """E-mail or password rejected. Never retry automatically: Abbott locks
    the account after a few failed sign-ins."""


class RateLimited(LibreError):
    def __init__(self, wait_s: int, message: str = "") -> None:
        # Abbott's own lockouts are capped; a pause with a message of ours
        # (a step to complete in the app) keeps its length.
        self.wait_s = int(wait_s) if message else max(60, min(int(wait_s), MAX_WAIT_S))
        super().__init__(message or f"LibreLinkUp asks to wait {self.wait_s // 60} minutes "
                                    "(too many requests or failed logins).")


@dataclass
class Auth:
    base_url: str
    token: str
    expires_s: int
    account_id: str


@dataclass
class Current:
    ts: int
    mg_dl: int
    trend: int            # 1 falling fast .. 3 stable .. 5 rising fast, 0 unknown
    is_high: bool
    is_low: bool


@dataclass
class Graph:
    patient_name: str = ""
    target_low: int | None = None
    target_high: int | None = None
    sensor_serial: str = ""
    sensor_started_s: int | None = None
    # what LibreLinkUp says about the sensor and the Libre app on the phone:
    # warmup_min, type_code (undocumented, shown as is), app_version,
    # alarm_low/alarm_high (mg/dL), alarms (on/off)
    sensor_info: dict = field(default_factory=dict)
    current: Current | None = None
    points: list[tuple[int, int]] = field(default_factory=list)   # (ts, mg/dL)


def _headers(auth: Auth | None = None) -> dict[str, str]:
    h = {"product": "llu.android",
         "version": os.environ.get("LLU_VERSION") or DEFAULT_VERSION,
         "accept": "application/json",
         "content-type": "application/json",
         "cache-control": "no-cache"}
    if auth is not None:
        h["authorization"] = f"Bearer {auth.token}"
        h["account-id"] = hashlib.sha256(auth.account_id.encode()).hexdigest()
    return h


def _lockout_s(body: dict) -> int | None:
    """Abbott reports lockouts inside a 200 as well as with HTTP 429."""
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if body.get("status") == 429 or data.get("message") == "locked":
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        return int(inner.get("lockout") or 300)
    return None


def _client() -> httpx.AsyncClient:
    """The mock point of the tests (async): no test talks to LibreLinkUp."""
    return httpx.AsyncClient(timeout=TIMEOUT_S)


def _sync_client() -> httpx.Client:
    """The mock point of the tests (synchronous)."""
    return httpx.Client(timeout=TIMEOUT_S)


def _answer(r: httpx.Response, auth: Auth | None) -> dict:
    """Check one answer, for async and synchronous requests alike. Refreshes
    `auth.token` from the ticket every authenticated answer carries, so a
    token in regular use does not expire."""
    if r.status_code == 429:
        retry = r.headers.get("retry-after") or ""
        raise RateLimited(int(retry) if retry.isdigit() else 300)
    if r.status_code == 401:
        raise AuthExpired("LibreLinkUp no longer accepts the session.")
    if r.status_code >= 400:
        raise LibreError(f"LibreLinkUp answered with HTTP {r.status_code}. If this "
                         "persists, Abbott may have changed the interface.")
    try:
        body = r.json()
    except ValueError as exc:
        raise LibreError("LibreLinkUp sent an unreadable answer.") from exc
    if not isinstance(body, dict):
        raise LibreError("LibreLinkUp sent an unreadable answer.")
    wait = _lockout_s(body)
    if wait is not None:
        raise RateLimited(wait)
    ticket = body.get("ticket")
    if auth is not None and isinstance(ticket, dict) and ticket.get("token"):
        auth.token = str(ticket["token"])
        auth.expires_s = int(ticket.get("expires") or auth.expires_s)
    return body


async def _send(method: str, url: str, auth: Auth | None = None,
                payload: dict | None = None) -> dict:
    """One async request."""
    try:
        async with _client() as client:
            r = await client.request(method, url, headers=_headers(auth), json=payload)
    except httpx.HTTPError as exc:
        raise LibreError("LibreLinkUp is not reachable right now.") from exc
    return _answer(r, auth)


def _send_sync(http: httpx.Client, method: str, url: str, auth: Auth | None = None,
               payload: dict | None = None) -> dict:
    """One synchronous request on `http` (one client for a whole sign-in)."""
    try:
        r = http.request(method, url, headers=_headers(auth), json=payload)
    except httpx.HTTPError as exc:
        raise LibreError("LibreLinkUp is not reachable right now.") from exc
    return _answer(r, auth)


# --- sign-in (synchronous) ---------------------------------------------------

def _region(region: str) -> str:
    region = region.strip().lower()
    if not re.fullmatch(r"[a-z]{2}[0-9]?", region):
        raise LibreError(f"LibreLinkUp sent an unknown region: {region!r}")
    return "https://api.libreview.ru" if region == "ru" else f"https://api-{region}.libreview.io"


def login(email: str, password: str) -> Auth:
    """Sign in to a follower account. SignInRejected for a wrong e-mail or
    password (never retry that), RateLimited for a lockout or a step the
    person has to complete in the LibreLinkUp app (terms, privacy policy,
    e-mail confirmation)."""
    if not email.strip() or not password:
        raise LibreError("E-mail and password are both needed.")
    base = LOGIN_URL
    with _sync_client() as http:
        for _ in range(3):                               # at most one hop to the region
            body = _send_sync(http, "POST", f"{base}/llu/auth/login",
                              payload={"email": email.strip(), "password": password})
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            if data.get("redirect"):
                base = _region(str(data.get("region") or ""))
                continue
            step = data.get("step") if isinstance(data.get("step"), dict) else {}
            if step.get("type"):
                todo = _STEPS.get(step["type"], f"complete the step {step['type']!r}")
                raise RateLimited(STEP_PAUSE_S, f"Open the LibreLinkUp app once with this "
                                                f"account and {todo}, then try again.")
            ticket = data.get("authTicket") if isinstance(data.get("authTicket"), dict) else {}
            user = data.get("user") if isinstance(data.get("user"), dict) else {}
            if not ticket.get("token") or not user.get("id"):
                raise SignInRejected("LibreLinkUp did not accept this e-mail and password.")
            return Auth(base_url=base, token=str(ticket["token"]),
                        expires_s=int(ticket.get("expires") or time.time() + 3600),
                        account_id=str(user["id"]))
    raise LibreError("LibreLinkUp keeps redirecting the sign-in.")


def patients(auth: Auth) -> list[tuple[str, str]]:
    """(patient id, name) of everybody who shares their glucose with the account."""
    with _sync_client() as http:
        body = _send_sync(http, "GET", f"{auth.base_url}/llu/connections", auth)
    out = []
    for item in body.get("data") or []:
        if isinstance(item, dict) and item.get("patientId"):
            name = f"{item.get('firstName') or ''} {item.get('lastName') or ''}".strip()
            out.append((str(item["patientId"]), name))
    return out


# --- reading (async) -----------------------------------------------------------

async def graph(auth: Auth, patient_id: str) -> Graph:
    pid = str(uuid.UUID(patient_id))                    # never anything else in the path
    body = await _send("GET", f"{auth.base_url}/llu/connections/{pid}/graph", auth)
    if body.get("status") not in (0, None):
        raise LibreError("LibreLinkUp could not load this person's data (the "
                         "sharing may have been ended in the Libre app).")
    return parse_graph(body)


# --- parsing ---------------------------------------------------------------

def parse_time(value) -> int | None:
    """FactoryTimestamp, e.g. "9/28/2026 5:02:31 AM", is UTC."""
    try:
        dt = datetime.strptime(str(value), "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _mg(item: dict) -> int | None:
    try:
        v = round(float(item.get("ValueInMgPerDl")))
    except (TypeError, ValueError):
        return None
    return v if 10 <= v <= 700 else None


def _target(value) -> int | None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if 40 <= v <= 400 else None


def parse_graph(body: dict) -> Graph:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    conn = data.get("connection") if isinstance(data.get("connection"), dict) else {}
    g = Graph(patient_name=f"{conn.get('firstName') or ''} {conn.get('lastName') or ''}".strip(),
              target_low=_target(conn.get("targetLow")),
              target_high=_target(conn.get("targetHigh")))

    sensor = conn.get("sensor") if isinstance(conn.get("sensor"), dict) else None
    if not sensor:
        active = [s.get("sensor") for s in data.get("activeSensors") or []
                  if isinstance(s, dict) and isinstance(s.get("sensor"), dict)]
        sensor = active[-1] if active else {}
    g.sensor_serial = str(sensor.get("sn") or "")
    started = sensor.get("a")
    g.sensor_started_s = int(started) if isinstance(started, (int, float)) and started > 0 else None
    device = conn.get("patientDevice") if isinstance(conn.get("patientDevice"), dict) else {}
    info = {"warmup_min": sensor.get("w"), "type_code": sensor.get("pt"),
            "app_version": device.get("v"), "alarm_low": device.get("ll"),
            "alarm_high": device.get("hl"), "alarms": device.get("alarms"),
            "uom": conn.get("uom")}                 # 1 = mg/dL, 0 = mmol/L in the Libre app
    g.sensor_info = {k: v for k, v in info.items()
                     if isinstance(v, (int, float, str, bool)) and not isinstance(v, dict)}

    cur = conn.get("glucoseMeasurement")
    if isinstance(cur, dict):
        ts, mg = parse_time(cur.get("FactoryTimestamp")), _mg(cur)
        if ts is not None and mg is not None:
            trend = cur.get("TrendArrow")
            g.current = Current(ts, mg, int(trend) if isinstance(trend, int) else 0,
                                bool(cur.get("isHigh")), bool(cur.get("isLow")))

    for item in data.get("graphData") or []:
        if isinstance(item, dict):
            ts, mg = parse_time(item.get("FactoryTimestamp")), _mg(item)
            if ts is not None and mg is not None:
                g.points.append((ts, mg))
    g.points.sort()
    return g
