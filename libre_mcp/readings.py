"""Stored sensor glucose, per user: writing, reading, pruning.

The same reading can arrive twice - every poll sees the last 12 hours
again, and a LibreView CSV overlaps with what the collector already has.
LibreLinkUp stamps readings with seconds, the CSV only with minutes, so a
reading counts as known when one exists within DEDUPE_S of it.
"""

from __future__ import annotations

import bisect
import time

from . import users
from .db import conn

DEDUPE_S = 120                      # readings are 5 or 15 minutes apart
DAYS_PER_MONTH = 30.4375


def add(user_id: int, points: list[tuple[int, int]], source: str) -> int:
    """Store (ts, mg/dL) pairs that are not known yet and not older than the
    user's history setting. Returns how many were new."""
    cutoff = retention_cutoff(user_id)
    points = sorted(p for p in points if cutoff is None or p[0] >= cutoff)
    if not points:
        return 0
    lo, hi = points[0][0] - DEDUPE_S, points[-1][0] + DEDUPE_S
    with conn() as c:
        known = [r["ts"] for r in c.execute(
            "SELECT ts FROM readings WHERE user_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
            (user_id, lo, hi))]
        new = []
        for ts, mg in points:
            i = bisect.bisect_left(known, ts - DEDUPE_S)
            if i < len(known) and known[i] <= ts + DEDUPE_S:
                continue
            bisect.insort(known, ts)
            new.append((user_id, ts, int(mg), source))
        c.executemany("INSERT OR IGNORE INTO readings (user_id, ts, mg_dl, source)"
                      " VALUES (?,?,?,?)", new)
    return len(new)


def between(user_id: int, start: int, end: int) -> list[tuple[int, int]]:
    """(ts, mg/dL) with start <= ts < end, oldest first."""
    with conn() as c:
        rows = c.execute("SELECT ts, mg_dl FROM readings WHERE user_id=? AND ts>=? AND ts<?"
                         " ORDER BY ts", (user_id, start, end)).fetchall()
    return [(r["ts"], r["mg_dl"]) for r in rows]


def latest(user_id: int) -> tuple[int, int] | None:
    with conn() as c:
        row = c.execute("SELECT ts, mg_dl FROM readings WHERE user_id=?"
                        " ORDER BY ts DESC LIMIT 1", (user_id,)).fetchone()
    return (row["ts"], row["mg_dl"]) if row else None


def summary(user_id: int) -> dict:
    with conn() as c:
        row = c.execute("SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last"
                        " FROM readings WHERE user_id=?", (user_id,)).fetchone()
    return {"count": row["n"], "first": row["first"], "last": row["last"]}


def delete_all(user_id: int) -> None:
    with conn() as c:
        for table in ("readings", "realtime", "sensors"):
            c.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))


# --- real-time values (minute values, table `realtime`) ------------------------

def add_realtime(user_id: int, ts: int, mg_dl: int) -> bool:
    """Store LibreLinkUp's current value; the same measurement seen by two polls
    has the same timestamp and is kept once. True when it was new."""
    cutoff = retention_cutoff(user_id)
    if cutoff is not None and ts < cutoff:
        return False
    with conn() as c:
        return c.execute("INSERT OR IGNORE INTO realtime (user_id, ts, mg_dl) VALUES (?,?,?)",
                         (user_id, int(ts), int(mg_dl))).rowcount == 1


def realtime_latest(user_id: int) -> tuple[int, int] | None:
    with conn() as c:
        row = c.execute("SELECT ts, mg_dl FROM realtime WHERE user_id=?"
                        " ORDER BY ts DESC LIMIT 1", (user_id,)).fetchone()
    return (row["ts"], row["mg_dl"]) if row else None


def realtime_between(user_id: int, start: int, end: int) -> list[tuple[int, int]]:
    """(ts, mg/dL) of the stored current values with start <= ts < end."""
    with conn() as c:
        rows = c.execute("SELECT ts, mg_dl FROM realtime WHERE user_id=? AND ts>=? AND ts<?"
                         " ORDER BY ts", (user_id, start, end)).fetchall()
    return [(r["ts"], r["mg_dl"]) for r in rows]


# --- history limit -----------------------------------------------------------


def retention_cutoff(user_id: int, now: float | None = None) -> int | None:
    months = users.history_months(user_id)
    if months <= 0:
        return None
    return int((now or time.time()) - months * DAYS_PER_MONTH * 86400)


def prune(user_id: int) -> int:
    cutoff = retention_cutoff(user_id)
    if cutoff is None:
        return 0
    with conn() as c:
        c.execute("DELETE FROM realtime WHERE user_id=? AND ts<?", (user_id, cutoff))
        # sensors whose readings are all gone: the next one started before the cutoff
        c.execute("DELETE FROM sensors WHERE user_id=? AND EXISTS (SELECT 1 FROM sensors n"
                  " WHERE n.user_id=sensors.user_id AND n.started_s>sensors.started_s"
                  " AND n.started_s<=?)", (user_id, cutoff))
        return c.execute("DELETE FROM readings WHERE user_id=? AND ts<?",
                         (user_id, cutoff)).rowcount


def prune_all() -> int:
    with conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users")]
    return sum(prune(uid) for uid in ids)
