"""Readings for other apps behind the same MyAuth, handed out by MyAuth only.

`GET /internal/readings?uid=<MyAuth uid>&from=<unix s>&to=<unix s>` answers the
stored history (`readings`, smoothed 5-minute values) and the collected current
values (`realtime`, about one per minute) of one user for a short window, in
mg/dL - e.g. for a training app that puts them into an activity.

`GET /internal/gmi?uid=<MyAuth uid>&at=<unix s>` answers the GMI of the user's
GMI period (`users.gmi_days`) up to `at`, with mean and coverage - the period
is the user's setting here, not the asking app's. Both answers carry the
user's display unit (`display_unit`: mgdl or mmol); values stay mg/dL.

Nobody calls these directly: MyAuth checks which app may ask for whose glucose
(`/api/internal/glucose`) and then asks here.

Two locks, no new secret:
- Docker network only. Caddy adds `X-Forwarded-For` to everything it passes
  on, so a request carrying it came from outside and is refused.
- `Authorization: Bearer` must be this app's own MyAuth key
  (`MYAUTH_APP_KEY`), which only MyAuth knows besides us.
"""

from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import readings, stats, users
from .access import myauth

MAX_WINDOW_S = 36 * 3600          # longer than any activity worth a glucose line


def _deny(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _check(request: Request) -> JSONResponse | None:
    if "x-forwarded-for" in request.headers:
        return _deny(403, "internal_only")
    key = myauth.app_secret()
    auth = request.headers.get("authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not key or not hmac.compare_digest(given, key):
        return _deny(401, "bad_key")
    return None


async def get_readings(request: Request) -> JSONResponse:
    if (denied := _check(request)) is not None:
        return denied
    try:
        uid = int(request.query_params["uid"])
        start = int(request.query_params["from"])
        end = int(request.query_params["to"])
    except (KeyError, ValueError):
        return _deny(400, "uid_from_to_required")
    if end <= start or end - start > MAX_WINDOW_S:
        return _deny(400, "bad_window")
    if users.get_user(uid) is None:
        return _deny(404, "unknown_user")
    points = readings.between(uid, start, end)
    live = readings.realtime_between(uid, start, end)
    return JSONResponse({"unit": "mg/dL", "display_unit": users.display_unit(uid),
                         "readings": [[ts, mg] for ts, mg in points],
                         "realtime": [[ts, mg] for ts, mg in live]},
                        headers={"Cache-Control": "no-store"})


async def get_gmi(request: Request) -> JSONResponse:
    if (denied := _check(request)) is not None:
        return denied
    try:
        uid = int(request.query_params["uid"])
        at = int(request.query_params["at"])
    except (KeyError, ValueError):
        return _deny(400, "uid_at_required")
    if users.get_user(uid) is None:
        return _deny(404, "unknown_user")
    days = users.gmi_days(uid)
    start = at - days * 86400
    s = stats.summarize(readings.between(uid, start, at + 1), days * 86400)
    body = {"unit": "mg/dL", "display_unit": users.display_unit(uid), "period_days": days,
            "from": start, "to": at, "readings": s["readings"]}
    if s["readings"]:
        body.update(mean=s["mean"], gmi_pct=s["gmi_pct"], coverage_pct=s["coverage_pct"])
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
