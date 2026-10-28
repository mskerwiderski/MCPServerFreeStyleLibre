"""Standalone mode: one person, their own machine, no MyAuth.

`libre-mcp login` signs in to a LibreLinkUp follower account once (e-mail,
password, which person's glucose) and writes the session - region host,
token, account id, patient - to a file only that user can read. The password
is never stored. `libre-mcp stdio` then serves the MCP tools over stdio to
Claude Code or Claude Desktop and collects readings while it runs;
`libre-mcp collect` does one round for a scheduled job.

LibreLinkUp's token lasts about half a year, and every answer renews it, so
it is saved again whenever it changed (`LocalSource.keep`). When LibreLinkUp
stops accepting it, the tools say so: run `libre-mcp login` again.

Files:
  session   $LIBRE_MCP_SESSION, else $XDG_CONFIG_HOME/libre-mcp/session.json
            (default ~/.config/libre-mcp/), mode 0600 in a 0700 directory
  readings  $MCP_DB, else $XDG_DATA_HOME/libre-mcp/libre.db
            (default ~/.local/share/libre-mcp/), in a 0700 directory
The person is user 0 in the database; the time zone is $TZ, else the
machine's.
"""

from __future__ import annotations

import getpass
import json
import os
import time
from pathlib import Path

from . import db, times, users
from .librelinkup import client as llu

USER_ID = 0
RENEW_MARGIN_S = 10 * 60
NOT_SIGNED_IN = ("not signed in to LibreLinkUp yet - run `libre-mcp login` in a terminal "
                 "on this machine first")
EXPIRED = ("The LibreLinkUp sign-in has expired (it lasts about half a year). Run "
           "`libre-mcp login` in a terminal again.")


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default).expanduser()


def session_path() -> Path:
    env = os.environ.get("LIBRE_MCP_SESSION")
    if env:
        return Path(env).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "libre-mcp" / "session.json"


def data_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "libre-mcp" / "libre.db"


def load() -> dict | None:
    """The stored session, None when nobody signed in yet."""
    path = session_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text("utf-8"))
    if not data.get("token") or not data.get("patient_id"):
        raise ValueError(f"{path} holds no LibreLinkUp session - run `libre-mcp login` again")
    return data


def save(session: dict) -> Path:
    """Write atomically, readable by this user only."""
    path = session_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def forget() -> bool:
    """Delete the session; True if there was one. The readings stay."""
    path = session_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def session_from(auth: llu.Auth, patient_id: str, patient_name: str, account: str) -> dict:
    return {"base_url": auth.base_url, "token": auth.token, "expires": auth.expires_s,
            "account_id": auth.account_id, "patient_id": patient_id,
            "patient_name": patient_name, "account": account}


class LocalSource:
    """The session from the local file instead of MyAuth (libre.use_source)."""

    not_connected = NOT_SIGNED_IN

    async def session(self, uid: int, renew: bool) -> dict | None:
        s = load()
        if s is None:
            return None
        if renew or int(s.get("expires") or 0) - RENEW_MARGIN_S < time.time():
            raise llu.LibreError(EXPIRED)
        return s

    async def state(self, uid: int) -> dict:
        s = load()
        return {"connected": s is not None, "account": (s or {}).get("account"),
                "last_error": None}

    def keep(self, uid: int, auth: llu.Auth) -> None:
        s = load()
        if s and (s["token"] != auth.token or int(s.get("expires") or 0) != auth.expires_s):
            save({**s, "token": auth.token, "expires": auth.expires_s})


def system_zone() -> str | None:
    """The machine's time zone name (macOS and Linux link /etc/localtime)."""
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else None


def prepare() -> None:
    """Point the server at the local files and the local session, user 0."""
    from . import libre

    if not os.environ.get("MCP_DB"):
        path = data_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ["MCP_DB"] = str(path)
    db.init()
    libre.use_source(LocalSource())
    s = load()
    users.ensure_user(USER_ID, "local", (s or {}).get("account") or getpass.getuser())
    users.CURRENT_USER.set(USER_ID)
    times.use(os.environ.get("TZ") or system_zone())
