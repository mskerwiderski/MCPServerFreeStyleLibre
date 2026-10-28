"""The sensors a user wore, and which readings each one measured.

A reading carries no sensor: neither LibreLinkUp's graph nor the LibreView CSV
says which sensor measured it. Sensors are worn one after the other, though,
so time decides - a reading belongs to the sensor with the latest start at or
before it (`of`). Nothing is stored per reading, so a sensor found later (a
CSV import) sorts the history by itself.

Where a sensor comes from (`source`):
  llu    LibreLinkUp reported it: serial number and exact activation. Every
         poll records it (`last_seen_s`), so every sensor worn while the
         collector runs is known this way.
  gap    inferred from the stored history: a sensor change leaves a gap of at
         least the warm-up hour, so a gap of CHANGE_GAP_S or more that no
         reported sensor explains starts a new sensor, activated about one
         warm-up before the first reading after it. No serial number.
  first  inferred too: the sensor running when the stored history begins,
         if no reported sensor covers it. Its true start may be earlier.
Inferred sensors are recomputed (`rebuild`) after a CSV import, when a new
sensor is reported and at every start of the server; they never override a
reported one. A gap within a reported sensor's time - between its start and
the last poll that saw it - is data lost, not a change.

Numbers count the stored sensors, oldest first. A CSV import can add earlier
sensors and the history limit drops old ones, so a number may shift; the start
day and the serial number stay.
"""

from __future__ import annotations

import bisect
import statistics
import time
from datetime import date, datetime

from . import times
from .db import conn

WARMUP_S = 3600                     # Libre 2 and 3: one hour before the first value
CHANGE_GAP_S = 55 * 60              # a bit under the warm-up: the CSV has minutes only
RUNNING_S = 3 * 3600                # the newest sensor with a value this recent is running
SOURCES = ("llu", "gap", "first")


# --- writing -----------------------------------------------------------------


def seen(uid: int, serial: str, started_s: int, type_code=None, warmup_min=None,
         now: int | None = None) -> bool:
    """LibreLinkUp reports this sensor right now. True when it is new here."""
    now = int(now or time.time())
    with conn() as c:
        new = c.execute(
            "INSERT OR IGNORE INTO sensors (user_id, started_s, serial, source, type_code,"
            " warmup_min, last_seen_s) VALUES (?,?,?,'llu',?,?,?)",
            (uid, int(started_s), serial or "", type_code, warmup_min, now)).rowcount == 1
        if not new:
            c.execute("UPDATE sensors SET source='llu', last_seen_s=?,"
                      " serial=COALESCE(NULLIF(?, ''), serial),"
                      " type_code=COALESCE(?, type_code), warmup_min=COALESCE(?, warmup_min)"
                      " WHERE user_id=? AND started_s=?",
                      (now, serial or "", type_code, warmup_min, uid, int(started_s)))
    return new


def rebuild(uid: int) -> None:
    """Infer the sensors LibreLinkUp never reported from the gaps in the
    stored history (see the module docstring)."""
    with conn() as c:
        known = [(r["started_s"], max(r["started_s"], r["last_seen_s"] or 0)) for r in c.execute(
            "SELECT started_s, last_seen_s FROM sensors WHERE user_id=? AND source='llu'",
            (uid,))]
        first = c.execute("SELECT MIN(ts) AS t FROM readings WHERE user_id=?",
                          (uid,)).fetchone()["t"]
        gaps = c.execute(
            "SELECT prev, ts FROM (SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev"
            " FROM readings WHERE user_id=?) WHERE ts - prev >= ?",
            (uid, CHANGE_GAP_S)).fetchall()
        inferred = []
        if first is not None and not any(s <= first for s, _ in known):
            inferred.append((first - WARMUP_S, "first"))
        for prev, cur in gaps:
            if any(prev < s <= cur or s <= prev and cur <= last for s, last in known):
                continue                      # a reported change, or data lost under one
            inferred.append((max(cur - WARMUP_S, prev + 1), "gap"))
        c.execute("DELETE FROM sensors WHERE user_id=? AND source<>'llu'", (uid,))
        c.executemany("INSERT OR IGNORE INTO sensors (user_id, started_s, source)"
                      " VALUES (?,?,?)", [(uid, s, src) for s, src in inferred])


def rebuild_all() -> None:
    with conn() as c:
        ids = [r["user_id"] for r in c.execute("SELECT DISTINCT user_id FROM readings")]
    for uid in ids:
        rebuild(uid)


# --- reading -----------------------------------------------------------------


def all_of(uid: int) -> list[dict]:
    """Every stored sensor, oldest first, with its `number` and `until_s` (the
    next sensor's start; None for the newest)."""
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT started_s, serial, source, type_code, warmup_min, last_seen_s"
            " FROM sensors WHERE user_id=? ORDER BY started_s", (uid,))]
    for i, row in enumerate(rows):
        row["number"] = i + 1
        row["until_s"] = rows[i + 1]["started_s"] if i + 1 < len(rows) else None
    return rows


def of(sensors: list[dict], ts: int) -> dict | None:
    """The sensor that measured at ts: the latest started at or before it."""
    i = bisect.bisect_right([s["started_s"] for s in sensors], ts)
    return sensors[i - 1] if i else None


def spans(sensors: list[dict], points: list[tuple[int, int]]) -> list[dict]:
    """Which sensors measured these points: number, serial, first and last
    point, count - one entry per sensor, in time order."""
    starts = [s["started_s"] for s in sensors]
    out: list[dict] = []
    for ts, _ in points:
        i = bisect.bisect_right(starts, ts)
        s = sensors[i - 1] if i else None
        number = s["number"] if s else None
        if out and out[-1]["number"] == number:
            out[-1]["last"] = ts
            out[-1]["readings"] += 1
        else:
            out.append({"number": number, "serial": (s or {}).get("serial") or None,
                        "first": ts, "last": ts, "readings": 1})
    return out


def find(uid: int, key) -> dict:
    """A sensor by its number, serial number or start day (YYYY-MM-DD)."""
    sensors = all_of(uid)
    if not sensors:
        raise ValueError("No sensors are known yet.")
    text = str(key).strip()
    hint = (f"list_sensors shows the {len(sensors)} stored sensors "
            f"(numbers 1 to {len(sensors)}).")
    if text.isdigit():
        n = int(text)
        if 1 <= n <= len(sensors):
            return sensors[n - 1]
        raise ValueError(f"There is no sensor {n}; {hint}")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        day = None
    if day is not None:
        found = [s for s in sensors
                 if datetime.fromtimestamp(s["started_s"], times.zone()).date() == day]
        if len(found) == 1:
            return found[0]
        raise ValueError(f"{'No' if not found else 'More than one'} sensor started on "
                         f"{text}; {hint}")
    found = [s for s in sensors if s["serial"] and s["serial"].lower() == text.lower()]
    if found:
        return found[0]
    raise ValueError(f"No sensor with the serial number {text}; {hint}")


def history(uid: int, now: int | None = None) -> list[dict]:
    """Every sensor, oldest first, with what its readings say: first and last
    reading, count, coverage, mean and the shares in range and below 70 mg/dL,
    days worn and status (warming_up, running, ended)."""
    now = int(now or time.time())
    sensors = all_of(uid)
    with conn() as c:
        for s in sensors:
            until = s["until_s"] or now + 1
            row = c.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last, AVG(mg_dl) AS mean,"
                " SUM(mg_dl BETWEEN 70 AND 180) AS in_range, SUM(mg_dl < 70) AS below"
                " FROM readings WHERE user_id=? AND ts>=? AND ts<?",
                (uid, s["started_s"], until)).fetchone()
            n = row["n"]
            s.update(readings=n, first_s=row["first"], last_s=row["last"])
            if n:
                head = [r["ts"] for r in c.execute(
                    "SELECT ts FROM readings WHERE user_id=? AND ts>=? AND ts<? ORDER BY ts"
                    " LIMIT 50", (uid, s["started_s"], until))]
                step = (int(statistics.median(b - a for a, b in zip(head, head[1:])))
                        if len(head) > 1 else 300)
                s.update(coverage_pct=min(100.0, round(100 * n * step
                                                       / (row["last"] - row["first"] + step), 1)),
                         mean=round(row["mean"]),
                         in_range_pct=round(100 * row["in_range"] / n, 1),
                         below_70_pct=round(100 * row["below"] / n, 1))
            last = row["last"]
            if s["until_s"] is None:              # the newest: minute values count as a sign of life
                live = c.execute("SELECT MAX(ts) AS t FROM realtime WHERE user_id=? AND ts>=?",
                                 (uid, s["started_s"])).fetchone()["t"]
                last = max(last or 0, live or 0) or None
                if last is not None and now - last < RUNNING_S:
                    s["status"] = "running"
                elif last is None and now - s["started_s"] < WARMUP_S + RUNNING_S:
                    s["status"] = "warming_up"
                else:
                    s["status"] = "ended"
            else:
                s["status"] = "ended"
            end = now if s["status"] != "ended" else (last or s["started_s"])
            s["days"] = round((end - s["started_s"]) / 86400, 1)
    return sensors
