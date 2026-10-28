"""The local side of a MyAuth user.

Who may use this server is decided in MyAuth, not here. A row in `users` only
exists to hang the LibreLinkUp account, the readings and the OAuth grants on,
and its id IS the MyAuth uid.

The username is kept for the one case where that could go wrong: a MyAuth
database rebuilt from scratch hands the same uid to somebody else. When the
name behind a uid changes, everything stored for the previous owner is dropped
before the new one can see it - these are other people's health data.
"""

from __future__ import annotations

import contextvars

from .db import conn, now_ms

HISTORY_CHOICES = (3, 6, 12, 24, 36, 60, 120, 0)     # months, 0 = no limit
DEFAULT_HISTORY_MONTHS = 24
POLL_CHOICES = (1, 3, 5, 15, 30, 60)                # minutes between LibreLinkUp polls
UNIT_CHOICES = ("auto", "mgdl", "mmol")             # auto = like the Libre app
DEFAULT_POLL_MINUTES = 1
GMI_CHOICES = (14, 30, 90)                          # days the GMI is computed over
DEFAULT_GMI_DAYS = 14                               # the consensus period

# Set by the HTTP endpoint before it hands the request to the MCP session
# manager; the tools read it. That is the whole tenancy mechanism.
CURRENT_USER: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "libre_mcp_user", default=None)


def current_user_id() -> int:
    uid = CURRENT_USER.get()
    if uid is None:
        raise RuntimeError("no user bound to this request")
    return uid


def ensure_user(uid: int, username: str, display_name: str = "") -> bool:
    """Create or refresh the row for this MyAuth user. True when the uid used
    to belong to a different username and its data was just dropped."""
    with conn() as c:
        row = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    reset = row is not None and row["username"] != username
    if reset:
        delete_user(uid)
    with conn() as c:
        if row is None or reset:
            c.execute("INSERT INTO users (id, username, display_name, created_at_ms,"
                      " last_seen_ms) VALUES (?,?,?,?,?)",
                      (uid, username, display_name or "", now_ms(), now_ms()))
        else:
            c.execute("UPDATE users SET display_name=?, last_seen_ms=? WHERE id=?",
                      (display_name or "", now_ms(), uid))
    return reset


def get_user(user_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def delete_user(user_id: int) -> None:
    """Everything this server holds for that person: the LibreLinkUp account,
    all readings and sensors, connector grants, the row itself. The MyAuth account is not
    touched."""
    from .libre import forget
    forget(user_id)
    with conn() as c:
        for table in ("libre_accounts", "readings", "realtime", "sensors",
                      "oauth_tokens"):
            c.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


def history_months(user_id: int) -> int:
    user = get_user(user_id)
    return int(user["history_months"]) if user else DEFAULT_HISTORY_MONTHS


def set_history_months(user_id: int, months: int) -> None:
    if months not in HISTORY_CHOICES:
        raise ValueError(f"history must be one of {HISTORY_CHOICES} months")
    with conn() as c:
        c.execute("UPDATE users SET history_months=? WHERE id=?", (months, user_id))


def poll_minutes(user_id: int) -> int:
    user = get_user(user_id)
    return int(user["poll_minutes"]) if user and user["poll_minutes"] else DEFAULT_POLL_MINUTES


def set_poll_minutes(user_id: int, minutes: int) -> None:
    if minutes not in POLL_CHOICES:
        raise ValueError(f"interval must be one of {POLL_CHOICES} minutes")
    with conn() as c:
        c.execute("UPDATE users SET poll_minutes=? WHERE id=?", (minutes, user_id))


def unit(user_id: int) -> str:
    user = get_user(user_id)
    return user["unit"] if user and user["unit"] in UNIT_CHOICES else "auto"


def set_unit(user_id: int, value: str) -> None:
    if value not in UNIT_CHOICES:
        raise ValueError(f"unit must be one of {UNIT_CHOICES}")
    with conn() as c:
        c.execute("UPDATE users SET unit=? WHERE id=?", (value, user_id))


def display_unit(user_id: int) -> str:
    """The unit glucose is shown in: the user's choice, "auto" = what the Libre
    app shows (LibreLinkUp `uom`: 0 = mmol/L). Stored values stay mg/dL."""
    import json
    from .libre import account
    chosen = unit(user_id)
    if chosen != "auto":
        return chosen
    try:
        info = json.loads((account(user_id) or {}).get("sensor_info") or "{}")
    except ValueError:
        info = {}
    return "mmol" if info.get("uom") == 0 else "mgdl"


def gmi_days(user_id: int) -> int:
    user = get_user(user_id)
    days = int(user["gmi_days"]) if user and user["gmi_days"] else DEFAULT_GMI_DAYS
    return days if days in GMI_CHOICES else DEFAULT_GMI_DAYS


def set_gmi_days(user_id: int, days: int) -> None:
    if days not in GMI_CHOICES:
        raise ValueError(f"GMI period must be one of {GMI_CHOICES} days")
    with conn() as c:
        c.execute("UPDATE users SET gmi_days=? WHERE id=?", (days, user_id))
