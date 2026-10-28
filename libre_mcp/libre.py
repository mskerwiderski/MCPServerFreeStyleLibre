"""A user's LibreLinkUp readings: polling with the session from MyAuth, and the collector.

The LibreLinkUp account is connected in MyAuth (account page, "LibreLinkUp"),
so every app behind that MyAuth can read the same glucose. MyAuth keeps the e-mail and
password, signs in, never retries a rejected password, and hands this server
the session: region host, token, account id and whose glucose it reads.
Nothing about the login is stored here. The readings themselves come straight
from LibreLinkUp - MyAuth passes no data through.

LibreLinkUp only ever shows the last 12 hours, so the history is what this
server collects itself: as often as each user chose (1 to 60 minutes, default
one minute) it fetches the graph of every user with a connection in MyAuth and
stores the readings it does not have yet - and the current value, which
LibreLinkUp never hands out later: past minute values exist only where somebody
asked at that minute (table `realtime`, like the GGlance data field on the
watch does live). The sensor in the same answer goes into the sensor history
(sensors.py); sensor and app details land in `libre_accounts.sensor_info`.

Kept here per user (`libre_accounts`): whose glucose the stored readings are
(`patient_id` - when MyAuth reports somebody else, the old readings are
dropped), target range, and the collector's state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from . import readings, sensors, times, users
from .librelinkup import client as llu
from .access import myauth
from .db import conn, now_ms

log = logging.getLogger("libre_mcp")

NOT_CONNECTED = ("LibreLinkUp is not connected yet - connect it on your MyAuth "
                 "account page (LibreLinkUp)")

_locks: dict[int, asyncio.Lock] = {}


class NotConnected(RuntimeError):
    """No LibreLinkUp connection for this user (in MyAuth, or no local sign-in)."""


class MyAuthSource:
    """Where the LibreLinkUp session comes from in the hosted mode: MyAuth signs in
    and hands it out. The standalone mode swaps in its own source
    (local.LocalSource, `use_source`)."""

    not_connected = NOT_CONNECTED

    async def session(self, uid: int, renew: bool) -> dict | None:
        try:
            return await myauth.libre_session(uid, renew)
        except myauth.Unavailable as exc:
            raise llu.LibreError("MyAuth, which holds the LibreLinkUp connection, is not "
                                 "answering. Try again in a moment.") from exc
        except myauth.LibreRefused as exc:
            raise llu.LibreError(f"{exc} (see LibreLinkUp on your MyAuth account page)") from exc

    async def state(self, uid: int) -> dict:
        try:
            return await myauth.libre_state(uid)
        except myauth.Unavailable:
            return {"connected": None}

    def keep(self, uid: int, auth: llu.Auth) -> None:
        """A token LibreLinkUp renewed in passing: MyAuth renews its own."""


_source = MyAuthSource()


def use_source(source) -> None:
    """Take the LibreLinkUp session from `source` instead of MyAuth."""
    global _source
    _source = source


def _lock(uid: int) -> asyncio.Lock:
    return _locks.setdefault(uid, asyncio.Lock())


def forget(uid: int) -> None:
    _locks.pop(uid, None)


# --- stored state ------------------------------------------------------------


def account(uid: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM libre_accounts WHERE user_id=?", (uid,)).fetchone()
    return dict(row) if row else None


def user_ids() -> list[int]:
    """Everybody known here; whether they connected LibreLinkUp, MyAuth says."""
    with conn() as c:
        return [r["id"] for r in c.execute("SELECT id FROM users")]


def _update(uid: int, **fields) -> None:
    fields["updated_at_ms"] = now_ms()
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO libre_accounts (user_id, updated_at_ms)"
                  " VALUES (?,?)", (uid, now_ms()))
        c.execute(f"UPDATE libre_accounts SET {cols} WHERE user_id=?",
                  (*fields.values(), uid))


async def state(uid: int) -> dict:
    """The connection: {connected, account, last_error}; MyAuth not answering
    reads as {connected: None}."""
    return await _source.state(uid)


# --- reading -----------------------------------------------------------------


async def _session(uid: int, renew: bool = False) -> tuple[llu.Auth, dict]:
    s = await _source.session(uid, renew)
    if s is None:
        raise NotConnected(_source.not_connected)
    return llu.Auth(str(s["base_url"]), str(s["token"]), int(s.get("expires") or 0),
                    str(s["account_id"])), s


def _adopt(uid: int, s: dict) -> None:
    """Somebody else's glucose in MyAuth now: the stored readings are not theirs."""
    acc = account(uid)
    if acc and acc["patient_id"] and acc["patient_id"] != s["patient_id"]:
        readings.delete_all(uid)
    _update(uid, patient_id=str(s["patient_id"]), patient_name=str(s.get("patient_name") or ""))


async def refresh(uid: int) -> llu.Graph:
    """Current value and the last 12 hours from LibreLinkUp; new readings are
    stored. Success or the reason for failure is recorded on the account."""
    async with _lock(uid):
        acc = account(uid) or {}
        blocked = acc.get("blocked_until_ms") or 0
        if blocked > now_ms():
            raise llu.LibreError(f"LibreLinkUp asked to pause until "
                                 f"{times.fmt(blocked // 1000)}.")
        try:
            auth, s = await _session(uid)
            try:
                graph = await llu.graph(auth, s["patient_id"])
            except llu.AuthExpired:
                auth, s = await _session(uid, renew=True)
                graph = await llu.graph(auth, s["patient_id"])
        except llu.RateLimited as exc:
            _update(uid, last_poll_ms=now_ms(), last_error=str(exc),
                    blocked_until_ms=now_ms() + exc.wait_s * 1000)
            raise
        except llu.LibreError as exc:
            _update(uid, last_poll_ms=now_ms(), last_error=str(exc))
            raise
        _source.keep(uid, auth)
        _adopt(uid, s)
        readings.add(uid, graph.points, "llu")
        if graph.current is not None:
            readings.add_realtime(uid, graph.current.ts, graph.current.mg_dl)
        fields = dict(last_poll_ms=now_ms(), last_ok_ms=now_ms(), last_error=None,
                      blocked_until_ms=None)
        if graph.patient_name:
            fields["patient_name"] = graph.patient_name
        if graph.target_low and graph.target_high:
            fields.update(target_low=graph.target_low, target_high=graph.target_high)
        if graph.sensor_started_s and sensors.seen(
                uid, graph.sensor_serial, graph.sensor_started_s,
                graph.sensor_info.get("type_code"), graph.sensor_info.get("warmup_min")):
            sensors.rebuild(uid)                 # inferred sensors give way to a reported one
        if graph.sensor_info or graph.current is not None:
            info = dict(graph.sensor_info)
            if graph.current is not None:
                info.update(current_ts=graph.current.ts, current_mg=graph.current.mg_dl,
                            trend=graph.current.trend)
            fields["sensor_info"] = json.dumps(info)
        _update(uid, **fields)
        return graph


# --- collector ---------------------------------------------------------------


def poll_interval_s() -> int:
    """How often the collector wakes up; 0 switches it off (tests). Each user
    is then asked as often as they chose (`users.poll_minutes`, 1..60 min) -
    one minute keeps the minute values, which are gone afterwards."""
    return int(os.environ.get("POLL_INTERVAL_S", "60"))


def _due(uid: int) -> bool:
    last = (account(uid) or {}).get("last_poll_ms")
    slack_ms = 5_000                                  # the wake-up drifts by a second or two
    return last is None or now_ms() - last >= users.poll_minutes(uid) * 60_000 - slack_ms


async def poll_all() -> None:
    for uid in user_ids():
        if not _due(uid):
            continue
        try:
            await refresh(uid)
        except NotConnected:
            pass                                             # not connected in MyAuth
        except llu.LibreError as exc:
            log.warning("LibreLinkUp poll for user %s failed: %s", uid, exc)
        except Exception:                                    # noqa: BLE001
            log.exception("LibreLinkUp poll for user %s crashed", uid)
    readings.prune_all()


async def run_collector(interval_s: int) -> None:
    await asyncio.sleep(min(30, interval_s))
    while True:
        await poll_all()
        await asyncio.sleep(interval_s)
