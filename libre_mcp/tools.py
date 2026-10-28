"""The MCP tools. Read-only: nothing here writes to LibreLinkUp.

Docstrings are the tool descriptions the model sees, so they say when to reach
for which tool. Every answer is compact - glucose series are long, and a raw
dump burns the context the conversation needs.

All history comes from this server's own database (LibreLinkUp itself only
keeps 12 hours); only get_current_glucose asks LibreLinkUp live. Every answer
with readings names the sensors that measured them (`sensors`), and those
tools take `sensor` to stay with one of them (sensors.py).
"""

from __future__ import annotations

import functools
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import libre, readings, sensors, stats, times, users
from .librelinkup import client as llu

MAX_POINTS = 1000
LIFETIME_DAYS = {"libre_3": 14, "libre_3_plus": 15}

TRENDS = {1: "falling fast", 2: "falling", 3: "stable", 4: "rising", 5: "rising fast"}
START = {"llu": "reported", "gap": "estimated", "first": "history_start"}


def _guard(fn):
    """Turn our own failures into ToolError, which the SDK passes through to
    the client. Without this the model only ever sees "Error executing tool
    <name>" - and the most common failure here (not connected yet) is exactly
    the one the user needs to read."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (libre.NotConnected, llu.LibreError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapper


def _uid() -> int:
    return users.current_user_id()


def _target(acc: dict | None) -> tuple[int, int] | None:
    if acc and acc["target_low"] and acc["target_high"]:
        return acc["target_low"], acc["target_high"]
    return None


def _window(uid: int, date_from: str | None, date_to: str | None, sensor,
            default_hours: int) -> tuple[int, int]:
    """[start, end) of a request: the dates, or the chosen sensor's run -
    narrowed by whichever dates are given as well."""
    if sensor is None or not str(sensor).strip():
        return times.parse_range(date_from, date_to, default_hours)
    s = sensors.find(uid, sensor)
    start, end = s["started_s"], s["until_s"] or int(time.time()) + 1
    if date_from or date_to:
        lo, hi = times.parse_range(date_from, date_to, default_hours)
        start, end = max(start, lo) if date_from else start, min(end, hi) if date_to else end
        if start >= end:
            raise ValueError(f"Sensor {s['number']} was not worn then: it ran from "
                             f"{times.fmt(s['started_s'])} to "
                             f"{times.fmt(s['until_s']) or 'now'}.")
    return start, end


def _points(uid: int, start: int, end: int) -> list[tuple[int, int]]:
    pts = readings.between(uid, start, end)
    if not pts:
        s = readings.summary(uid)
        known = (f"stored readings cover {times.fmt(s['first'])} to {times.fmt(s['last'])}"
                 if s["count"] else "no readings are stored yet")
        raise ValueError(f"No readings between {times.fmt(start)} and {times.fmt(end)}; {known}.")
    return pts


def _measured_by(uid: int, pts: list[tuple[int, int]]) -> list[dict]:
    """Which sensors the readings came from (numbers as in list_sensors)."""
    return [{"sensor": sp["number"], "serial": sp["serial"], "from": times.fmt(sp["first"]),
             "to": times.fmt(sp["last"]), "readings": sp["readings"]}
            for sp in sensors.spans(sensors.all_of(uid), pts)]


def _sensor_entry(s: dict) -> dict:
    out = {"number": s["number"], "serial": s["serial"] or None, "start": START[s["source"]],
           "started_at": times.fmt(s["started_s"]), "last_reading": times.fmt(s["last_s"]),
           "days": s["days"], "status": s["status"], "readings": s["readings"]}
    if s["readings"]:
        out.update({k: s[k] for k in ("coverage_pct", "mean", "in_range_pct", "below_70_pct")})
    return out


def _collector(acc: dict | None, conn: dict) -> dict:
    """`connected` is MyAuth's word (None: MyAuth not answering)."""
    out = {"connected": conn.get("connected")}
    if conn.get("last_error"):
        out["myauth_error"] = conn["last_error"]
    if acc and acc["last_ok_ms"]:
        out["last_success"] = times.fmt(acc["last_ok_ms"] // 1000)
    if acc and acc["last_error"]:
        out["last_error"] = acc["last_error"]
    return out


def register(server: MCPServer) -> MCPServer:

    @server.tool()
    @_guard
    async def whoami() -> dict:
        """Who this connector belongs to, whose glucose it reads, and how much
        history is stored. Call it when the other tools report missing data:
        it shows whether LibreLinkUp is connected (in MyAuth) and the
        collector's last success and error."""
        uid = _uid()
        user = users.get_user(uid) or {}
        acc = libre.account(uid)
        s = readings.summary(uid)
        months = users.history_months(uid)
        return {
            "user": user.get("display_name") or user.get("username"),
            "glucose_of": (acc or {}).get("patient_name") or None,
            "collector": _collector(acc, await libre.state(uid)),
            "history": {"readings": s["count"], "first": times.fmt(s["first"]),
                        "last": times.fmt(s["last"]), "sensors": len(sensors.all_of(uid)),
                        "kept_months": months or "unlimited"},
            "timezone": times.zone().key,
        }

    @server.tool()
    @_guard
    async def get_current_glucose() -> dict:
        """The latest sensor glucose in mg/dL with its trend arrow, asked live
        from LibreLinkUp. It is as fresh as the phone's last upload - the
        iPhone app uploads irregularly in the background, so check
        minutes_ago. Falls back to the newest stored reading when LibreLinkUp
        does not answer."""
        uid = _uid()
        try:
            graph = await libre.refresh(uid)
        except llu.LibreError as exc:
            last = readings.latest(uid)
            if last is None:
                raise
            return {"mg_dl": last[1], "measured_at": times.fmt(last[0]),
                    "minutes_ago": round((time.time() - last[0]) / 60),
                    "live": False, "note": f"LibreLinkUp: {exc}"}
        cur = graph.current
        if cur is None:
            raise ValueError("LibreLinkUp reports no current value (sensor warming up "
                             "or not uploading).")
        out = {"mg_dl": cur.mg_dl, "trend": TRENDS.get(cur.trend, "unknown"),
               "measured_at": times.fmt(cur.ts),
               "minutes_ago": round((time.time() - cur.ts) / 60), "live": True}
        if cur.is_low or cur.is_high:
            out["flag"] = "below the alarm limit" if cur.is_low else "above the alarm limit"
        acc = libre.account(uid)
        if _target(acc):
            out["target_range"] = "{}-{}".format(*_target(acc))
        return out

    @server.tool()
    @_guard
    async def get_glucose_readings(date_from: str | None = None,
                                   date_to: str | None = None,
                                   max_points: int = 300,
                                   sensor: int | str | None = None) -> dict:
        """Sensor glucose (mg/dL) over a period, oldest first.

        date_from/date_to are local days ("2026-09-28", date_to included) or
        local times ("2026-09-28T06:30") - use times to look at a workout,
        a meal or a night. Without date_from: the last 24 hours. sensor: a
        sensor's number from list_sensors, its serial number or its start day
        - only that sensor's readings, without dates its whole run. Short
        periods come back as raw readings [time, mg_dl]; long ones are
        condensed into time buckets [start, mean, min, max] so that at most
        max_points rows (capped at 1000) are returned. `sensors` says which
        sensor measured which part. For a whole period's summary prefer
        get_glucose_stats."""
        uid = _uid()
        start, end = _window(uid, date_from, date_to, sensor, default_hours=24)
        pts = _points(uid, start, end)
        bucket, rows = stats.downsample(pts, max(10, min(int(max_points), MAX_POINTS)))
        for row in rows:
            row[0] = times.fmt(row[0])
        return {"unit": "mg/dL", "from": times.fmt(start), "to": times.fmt(end),
                "sensors": _measured_by(uid, pts),
                "columns": ["time", "mg_dl"] if not bucket
                else ["bucket_start", "mean", "min", "max"],
                "bucket_minutes": bucket // 60 or None, "rows": rows}

    @server.tool()
    @_guard
    async def get_glucose_stats(date_from: str | None = None,
                                date_to: str | None = None,
                                sensor: int | str | None = None) -> dict:
        """Summary of a period in the standard CGM metrics: mean, median, SD,
        CV (glycaemic variability, stable below 36 %), GMI (estimated HbA1c in
        %), and time in ranges: very_low <54, low 54-69, in_range 70-180,
        high 181-250, very_high >250 mg/dL - plus the share within the
        person's own target range from the Libre app. coverage_pct below 70
        means too many gaps for the numbers to be representative.
        gmi_setting_days is the period the person chose for "their" GMI (the
        one on the account page and in other apps); gmi_pct here covers the
        requested period. `sensors` names the sensors behind the figures.

        date_from/date_to and sensor as in get_glucose_readings; without
        either the last 14 days. To compare sensors, ask once per sensor or
        see list_sensors."""
        uid = _uid()
        start, end = _window(uid, date_from, date_to, sensor, default_hours=14 * 24)
        pts = _points(uid, start, end)
        return {"unit": "mg/dL", "from": times.fmt(start), "to": times.fmt(end),
                **stats.summarize(pts, end - start, _target(libre.account(uid))),
                "gmi_setting_days": users.gmi_days(uid), "sensors": _measured_by(uid, pts)}

    @server.tool()
    @_guard
    async def get_daily_pattern(date_from: str | None = None,
                                date_to: str | None = None,
                                sensor: int | str | None = None) -> dict:
        """The typical day (ambulatory glucose profile): per local hour the
        5th, 25th, 50th, 75th and 95th percentile of all readings in the
        period. Shows recurring patterns - dawn rise, post-meal peaks, night
        lows - that single days hide. Without date_from the last 14 days;
        sensor as in get_glucose_readings."""
        uid = _uid()
        start, end = _window(uid, date_from, date_to, sensor, default_hours=14 * 24)
        pts = _points(uid, start, end)
        by_hour: dict[int, list[int]] = {}
        for ts, mg in pts:
            by_hour.setdefault(times.local_hour(ts), []).append(mg)
        return {"unit": "mg/dL", "from": times.fmt(start), "to": times.fmt(end),
                "sensors": _measured_by(uid, pts), "hours": stats.daily_pattern(by_hour)}

    @server.tool()
    @_guard
    async def list_glucose_episodes(date_from: str | None = None,
                                    date_to: str | None = None,
                                    low: int = 70, high: int = 180,
                                    min_minutes: int = 15,
                                    sensor: int | str | None = None) -> dict:
        """Lows (below `low`) and highs (above `high`) lasting at least
        min_minutes, each with start, end, duration, the lowest or highest
        value and the sensor that measured it. Default thresholds are the
        consensus limits 70 and 180 mg/dL; use low=54 for clinically
        significant lows, high=250 for very high. Without date_from the last
        14 days; sensor as in get_glucose_readings. At most 50 of each, newest
        last."""
        uid = _uid()
        start, end = _window(uid, date_from, date_to, sensor, default_hours=14 * 24)
        pts = _points(uid, start, end)
        worn = sensors.all_of(uid)
        out: dict = {"unit": "mg/dL", "from": times.fmt(start), "to": times.fmt(end),
                     "sensors": _measured_by(uid, pts)}
        for name, kw in (("lows", {"below": int(low)}), ("highs", {"above": int(high)})):
            found = stats.episodes(pts, min_minutes=max(1, int(min_minutes)), **kw)
            out[name] = {"count": len(found), "episodes": [
                {**e, "start": times.fmt(e["start"]), "end": times.fmt(e["end"]),
                 "sensor": (sensors.of(worn, e["start"]) or {}).get("number")}
                for e in found[-50:]]}
        return out

    @server.tool()
    @_guard
    async def get_sensor() -> dict:
        """The sensor worn now (the newest one): number, serial number, start,
        days worn, status (warming_up, running, ended) and when it ends - a
        FreeStyle Libre 3 lasts 14 days, a Libre 3 Plus 15. Values from the
        first hours after the start and from the last day are the least
        accurate. All earlier sensors: list_sensors."""
        worn = sensors.history(_uid())
        if not worn:
            raise ValueError("No sensor known yet - the collector has not read one.")
        s = worn[-1]
        return {**_sensor_entry(s), "of": len(worn),
                "ends_at": {k: times.fmt(s["started_s"] + d * 86400)
                            for k, d in LIFETIME_DAYS.items()}}

    @server.tool()
    @_guard
    async def list_sensors() -> dict:
        """Every sensor in the stored history, oldest first and numbered:
        serial number, start, last reading, days worn, status, and its
        readings - count, coverage_pct (gaps during its run), mean, % in range
        70-180 and % below 70 mg/dL. Use it for "how many sensors", "how long
        did they last", or to compare sensors; the readings, stats, pattern
        and episodes tools take the number as `sensor`. A Libre 3 lasts 14
        days, a Libre 3 Plus 15 - one that ended clearly earlier failed or
        came off.

        start: "reported" = LibreLinkUp named the sensor and its activation.
        "estimated" = worn before this server knew it (LibreView CSV, older
        history): the change was recognised by the gap of at least an hour a
        new sensor leaves, the start is the first reading minus the warm-up
        hour, and there is no serial number. "history_start" = it was running
        when the stored history begins and may have started earlier."""
        uid = _uid()
        worn = sensors.history(uid)
        return {"unit": "mg/dL", "count": len(worn), "sensors": [_sensor_entry(s) for s in worn]}

    return server
