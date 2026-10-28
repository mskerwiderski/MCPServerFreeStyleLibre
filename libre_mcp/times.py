"""Local time. Readings are stored in UTC; people ask in their own time zone.

Each user picks their zone in MyAuth, which hands it over with every answer
(`timezone`); the MCP endpoint and the pages put it into a ContextVar with
`use`. It sets day boundaries, how the LibreView CSV (local times only) is
read, and every time a tool returns. Without one - the collector, tests - the
container's TZ applies, and Europe/Berlin without that.
"""

from __future__ import annotations

import contextvars
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_USER_ZONE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "libre_mcp_zone", default=None)


def use(name: str | None) -> None:
    """The zone of the user this request is for."""
    _USER_ZONE.set(name or None)


def _zone(name: str | None) -> ZoneInfo | None:
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def zone() -> ZoneInfo:
    return (_zone(_USER_ZONE.get()) or _zone(os.environ.get("TZ"))
            or ZoneInfo("Europe/Berlin"))


def fmt(ts: int | None) -> str | None:
    """"2026-09-28T07:15" in local time."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, zone()).strftime("%Y-%m-%dT%H:%M")


def local_hour(ts: int) -> int:
    return datetime.fromtimestamp(ts, zone()).hour


def from_local(naive: datetime) -> int:
    """A wall-clock time in the local zone to Unix seconds. In the autumn
    hour that exists twice, the first occurrence wins."""
    return int(naive.replace(tzinfo=zone()).timestamp())


def _point(value: str, end: bool) -> int:
    value = value.strip()
    if len(value) == 10:                                   # a day: whole days
        day = date.fromisoformat(value)
        if end:
            day += timedelta(days=1)
        return from_local(datetime(day.year, day.month, day.day))
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        return int(dt.astimezone(timezone.utc).timestamp())
    return from_local(dt)


def parse_range(date_from: str | None, date_to: str | None, default_hours: int,
                now: float | None = None) -> tuple[int, int]:
    """[start, end) in Unix seconds. Days ("2026-09-28") cover the whole day,
    date_to included; times ("2026-09-28T06:30") are exact. Without
    date_from the range is the last `default_hours` up to date_to (or now)."""
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    end = _point(date_to, end=True) if date_to else now
    start = _point(date_from, end=False) if date_from else end - default_hours * 3600
    if start >= end:
        raise ValueError("date_from must be before date_to")
    return start, end
