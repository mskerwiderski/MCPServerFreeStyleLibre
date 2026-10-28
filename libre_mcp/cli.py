"""libre-mcp: standalone on your own machine, without a server and MyAuth.

    libre-mcp login      sign in to a LibreLinkUp follower account once
    libre-mcp status     who is signed in, whose glucose, how much history
    libre-mcp logout     delete the session (the readings stay)
    libre-mcp stdio      MCP over stdio for Claude Code / Claude Desktop;
                         collects readings while it runs
    libre-mcp collect    one collection round, for a scheduled job

Hosted, the server runs as an HTTP app (`libre_mcp.server:build_http_app`)
and takes the LibreLinkUp session from MyAuth. See local.py for the files.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import os
import sys
from datetime import datetime


def _choose(people: list[tuple[str, str]]) -> tuple[str, str] | None:
    if len(people) == 1:
        return people[0]
    print("Several people share their glucose with this account:")
    for i, (pid, name) in enumerate(people, 1):
        print(f"  {i}. {name or pid}")
    answer = input("Whose glucose should be read? Number: ").strip()
    return people[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(people) else None


def _login() -> int:
    from . import local
    from .librelinkup import client as llu

    print("LibreLinkUp follower account (the one the person with the sensor shares with).")
    email = input("E-mail: ").strip()
    password = getpass.getpass("Password: ")
    try:
        auth = llu.login(email, password)
        people = llu.patients(auth)
    except llu.LibreError as exc:
        print(f"Sign-in failed: {exc}", file=sys.stderr)
        return 1
    if not people:
        print(llu.NO_PATIENTS, file=sys.stderr)
        return 1
    chosen = _choose(people)
    if chosen is None:
        print("No person chosen, nothing saved.", file=sys.stderr)
        return 1
    pid, name = chosen
    path = local.save(local.session_from(auth, pid, name, email))
    print(f"Signed in. Reading the glucose of {name or pid}. Session saved to {path} "
          "(readable only by you; your password is not stored).")
    print("Add `libre-mcp stdio` to Claude Code or Claude Desktop; see docs/standalone.md.")
    return 0


def _status() -> int:
    from . import local, readings

    try:
        s = local.load()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if s is None:
        print(f"Not signed in (no session at {local.session_path()}). Run `libre-mcp login`.")
        return 1
    local.prepare()
    summary = readings.summary(local.USER_ID)
    until = datetime.fromtimestamp(int(s["expires"]))
    print(f"Signed in as {s.get('account')}, reading the glucose of "
          f"{s.get('patient_name') or s['patient_id']}")
    print(f"Session: {local.session_path()} (valid until {until:%Y-%m-%d}, renewed while in use)")
    last = datetime.fromtimestamp(summary["last"]) if summary["last"] else None
    print(f"Readings: {summary['count']} in {os.environ['MCP_DB']}"
          + (f", newest {last:%Y-%m-%d %H:%M}" if last else ""))
    return 0


def _logout() -> int:
    from . import local

    if local.forget():
        print(f"Deleted {local.session_path()}. The readings stay in {local.data_path()}.")
    else:
        print("There was no session to delete.")
    return 0


async def _run_stdio() -> None:
    from . import libre, local
    from .server import build_server

    local.prepare()
    server = build_server()
    interval = libre.poll_interval_s()

    async def collect() -> None:
        await libre.poll_all()                     # fill the last 12 hours right away
        if interval > 0:
            await libre.run_collector(interval)

    collector = asyncio.create_task(collect())
    try:
        await server.run_stdio_async()
    finally:
        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector


def _collect() -> int:
    from . import libre, local, readings
    from .librelinkup import client as llu

    local.prepare()
    try:
        graph = asyncio.run(libre.refresh(local.USER_ID))
    except (libre.NotConnected, llu.LibreError) as exc:
        print(f"Not collected: {exc}", file=sys.stderr)
        return 1
    readings.prune_all()
    print(f"Collected {len(graph.points)} readings of the last 12 hours"
          f"{' and the current value' if graph.current else ''}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="libre-mcp", description="Read-only MCP server for FreeStyle Libre glucose "
                                      "(LibreLinkUp), standalone on your own machine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="sign in to a LibreLinkUp follower account")
    sub.add_parser("status", help="show who is signed in and how much history there is")
    sub.add_parser("logout", help="delete the session (the readings stay)")
    sub.add_parser("stdio", help="serve MCP over stdio (Claude Code / Claude Desktop)")
    sub.add_parser("collect", help="one collection round, for a scheduled job")
    args = parser.parse_args(argv)
    if args.cmd == "login":
        return _login()
    if args.cmd == "status":
        return _status()
    if args.cmd == "logout":
        return _logout()
    if args.cmd == "collect":
        return _collect()
    asyncio.run(_run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
