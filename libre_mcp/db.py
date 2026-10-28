"""SQLite storage. One connection per operation, no ORM.

Starlette runs sync endpoints in a threadpool, so a shared connection would be
used across threads. Opening per call sidesteps that entirely and costs
nothing at this scale - this database serves a handful of MyAuth users.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
-- One row per MyAuth user who has used this server; id IS the MyAuth uid.
-- history_months: how long readings are kept, 0 = no limit.
-- poll_minutes: how often the collector asks LibreLinkUp (users.POLL_CHOICES).
-- unit: what the account page shows - auto (like the Libre app), mgdl, mmol.
-- gmi_days: the period "the" GMI is computed over (users.GMI_CHOICES).
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    username       TEXT NOT NULL,
    display_name   TEXT NOT NULL DEFAULT '',
    history_months INTEGER NOT NULL DEFAULT 24,
    poll_minutes   INTEGER NOT NULL DEFAULT 1,
    unit           TEXT NOT NULL DEFAULT 'auto',
    gmi_days       INTEGER NOT NULL DEFAULT 14,
    created_at_ms  INTEGER NOT NULL,
    last_seen_ms   INTEGER
);

-- What this server knows about a user's LibreLinkUp readings. The connection
-- itself (login, session) lives in MyAuth; patient_id says whose glucose the
-- stored readings are, so a connection to somebody else can drop them.
CREATE TABLE IF NOT EXISTS libre_accounts (
    user_id          INTEGER PRIMARY KEY,
    patient_id       TEXT,
    patient_name     TEXT NOT NULL DEFAULT '',
    target_low       INTEGER,
    target_high      INTEGER,
    sensor_info      TEXT,
    last_poll_ms     INTEGER,
    last_ok_ms       INTEGER,
    last_error       TEXT,
    blocked_until_ms INTEGER,
    updated_at_ms    INTEGER NOT NULL
);

-- Sensor glucose, one row per reading. ts is Unix seconds (UTC).
CREATE TABLE IF NOT EXISTS readings (
    user_id INTEGER NOT NULL,
    ts      INTEGER NOT NULL,
    mg_dl   INTEGER NOT NULL,
    source  TEXT NOT NULL,
    PRIMARY KEY (user_id, ts)
) WITHOUT ROWID;

-- The sensors a user wore (sensors.py). A reading belongs to the sensor with
-- the latest started_s at or before it; nothing is stored per reading.
-- source: llu = reported by LibreLinkUp (serial, exact activation); gap and
-- first = inferred from the stored history (no serial, start estimated).
-- last_seen_s: the last poll that reported the sensor.
CREATE TABLE IF NOT EXISTS sensors (
    user_id     INTEGER NOT NULL,
    started_s   INTEGER NOT NULL,
    serial      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,
    type_code   INTEGER,
    warmup_min  INTEGER,
    last_seen_s INTEGER,
    PRIMARY KEY (user_id, started_s)
) WITHOUT ROWID;

-- LibreLinkUp's current (real-time) value at every poll - the minute values
-- the sensor shows live. Kept apart from `readings` (the smoothed 5-minute
-- history) so statistics and coverage stay on one kind of value. Only other
-- apps use it (internal.py: e.g. to put it into activities).
CREATE TABLE IF NOT EXISTS realtime (
    user_id INTEGER NOT NULL,
    ts      INTEGER NOT NULL,
    mg_dl   INTEGER NOT NULL,
    PRIMARY KEY (user_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    access_token          TEXT PRIMARY KEY,
    refresh_token         TEXT NOT NULL,
    client_id             TEXT NOT NULL,
    user_id               INTEGER NOT NULL,
    scope                 TEXT,
    access_expires_at_ms  INTEGER NOT NULL,
    refresh_expires_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_oauth_tokens_refresh ON oauth_tokens(refresh_token);
CREATE INDEX IF NOT EXISTS ix_oauth_tokens_user ON oauth_tokens(user_id);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def db_path() -> Path:
    env = os.environ.get("MCP_DB")
    return Path(env).expanduser() if env else Path.home() / ".libre-mcp" / "app.db"


@contextmanager
def conn():
    """Careful: an exception passing through the `yield` skips the commit, so
    writes that must survive a failure have to be committed before the
    exception is raised.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


# The LibreLinkUp login moved to MyAuth; these columns held it (encrypted).
_LOGIN_COLUMNS = ("email", "password_enc", "base_url", "token_enc", "token_expires_s",
                  "account_id")


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(libre_accounts)")}
        if "sensor_info" not in have:
            c.execute("ALTER TABLE libre_accounts ADD COLUMN sensor_info TEXT")
        ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "poll_minutes" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN poll_minutes INTEGER NOT NULL DEFAULT 1")
        if "unit" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN unit TEXT NOT NULL DEFAULT 'auto'")
        if "gmi_days" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN gmi_days INTEGER NOT NULL DEFAULT 14")
        if "sensor_started_s" in have:          # only the current sensor, before `sensors`
            _move_sensor(c)
            for col in ("sensor_serial", "sensor_started_s"):
                c.execute(f"ALTER TABLE libre_accounts DROP COLUMN {col}")
        dropped = [col for col in _LOGIN_COLUMNS if col in have]
        for col in dropped:
            c.execute(f"ALTER TABLE libre_accounts DROP COLUMN {col}")
    if dropped:
        with conn() as c:
            c.execute("VACUUM")          # no copy of the old login left in free pages


def _move_sensor(c: sqlite3.Connection) -> None:
    """libre_accounts held the sensor LibreLinkUp reported last; it becomes
    the first reported sensor in `sensors`, seen at the last good poll."""
    for r in c.execute("SELECT user_id, sensor_serial, sensor_started_s, sensor_info, last_ok_ms"
                       " FROM libre_accounts WHERE sensor_started_s IS NOT NULL").fetchall():
        try:
            info = json.loads(r["sensor_info"] or "{}")
        except ValueError:
            info = {}
        c.execute("INSERT OR IGNORE INTO sensors (user_id, started_s, serial, source, type_code,"
                  " warmup_min, last_seen_s) VALUES (?,?,?,'llu',?,?,?)",
                  (r["user_id"], r["sensor_started_s"], r["sensor_serial"] or "",
                   info.get("type_code"), info.get("warmup_min"),
                   (r["last_ok_ms"] or 0) // 1000 or None))
