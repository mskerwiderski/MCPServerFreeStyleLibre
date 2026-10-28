"""Standalone mode: `libre-mcp login/status/logout/collect/stdio` and the
local session source - against FakeLLU, with the session file and the
database in the test's tmp dir (conftest)."""
import asyncio
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from conftest import OTHER_PATIENT, PATIENT
from libre_mcp import cli, libre, local, readings, times, users
from libre_mcp.librelinkup import client as llu


@pytest.fixture(autouse=True)
def fresh_context():
    yield
    users.CURRENT_USER.set(None)
    times.use(None)


@pytest.fixture
def signin(fake_llu, monkeypatch):
    """LibreLinkUp's sign-in, as the block does it (tested in MyCommons)."""
    attempts = []

    def login(email, password):
        attempts.append((email, password))
        if password != "follower-pw":
            raise llu.SignInRejected("LibreLinkUp did not accept this e-mail and password.")
        return fake_llu.session()

    def patients(auth):
        return [(p["patientId"], f"{p['firstName']} {p['lastName']}") for p in fake_llu.patients]

    monkeypatch.setattr(llu, "login", login)
    monkeypatch.setattr(llu, "patients", patients)
    return attempts


def _typing(monkeypatch, *answers, password="follower-pw"):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt="": password)


def _session(fake_llu, **over):
    return {**local.session_from(fake_llu.session(), PATIENT, "Max Muster", "max@example.com"),
            **over}


# ---------- login, status, logout ----------

def test_login_saves_a_private_session_without_the_password(signin, monkeypatch, capsys):
    _typing(monkeypatch, "max@example.com")
    assert cli.main(["login"]) == 0
    assert "glucose of Max Muster" in capsys.readouterr().out
    path = local.session_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    saved = json.loads(path.read_text())
    assert saved["patient_id"] == PATIENT and saved["account"] == "max@example.com"
    assert "follower-pw" not in path.read_text() and "password" not in saved


def test_several_people_ask_whose_glucose(signin, fake_llu, monkeypatch):
    fake_llu.patients.append({"patientId": OTHER_PATIENT, "firstName": "Erika",
                              "lastName": "Muster"})
    _typing(monkeypatch, "max@example.com", "2")
    assert cli.main(["login"]) == 0
    assert local.load()["patient_id"] == OTHER_PATIENT


def test_a_rejected_password_saves_nothing(signin, monkeypatch, capsys):
    _typing(monkeypatch, "max@example.com", password="wrong")
    assert cli.main(["login"]) == 1
    assert "did not accept" in capsys.readouterr().err
    assert not local.session_path().exists()


def test_nobody_sharing_saves_nothing(signin, fake_llu, monkeypatch, capsys):
    fake_llu.patients.clear()
    _typing(monkeypatch, "max@example.com")
    assert cli.main(["login"]) == 1
    assert "Add Connection" in capsys.readouterr().err


def test_status_and_logout(fake_llu, capsys):
    assert cli.main(["status"]) == 1
    assert "Not signed in" in capsys.readouterr().out
    local.save(_session(fake_llu))
    assert cli.main(["status"]) == 0
    assert "glucose of Max Muster" in capsys.readouterr().out
    assert cli.main(["logout"]) == 0
    assert not local.session_path().exists()


def test_the_default_places_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("LIBRE_MCP_SESSION")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert local.session_path() == tmp_path / "cfg" / "libre-mcp" / "session.json"
    assert local.data_path() == tmp_path / "data" / "libre-mcp" / "libre.db"


# ---------- the local session source ----------

def test_without_a_session_the_tools_say_how_to_sign_in():
    local.prepare()
    with pytest.raises(libre.NotConnected, match="libre-mcp login"):
        asyncio.run(libre.refresh(local.USER_ID))


def test_collect_stores_the_last_twelve_hours(fake_llu, capsys):
    local.save(_session(fake_llu))
    assert cli.main(["collect"]) == 0
    assert f"Collected {len(fake_llu.points)} readings" in capsys.readouterr().out
    assert readings.summary(local.USER_ID)["count"] == len(fake_llu.points)


def test_a_renewed_token_is_kept(fake_llu):
    local.save(_session(fake_llu, token="old-token"))
    fake_llu.tokens.add("old-token")
    local.prepare()
    # FakeLLU answers with a ticket for the token it was asked with; make it a new one
    auth_before = local.load()["token"]
    source = local.LocalSource()
    auth = llu.Auth(fake_llu.session().base_url, "renewed", int(time.time()) + 999, "x")
    source.keep(local.USER_ID, auth)
    assert auth_before == "old-token" and local.load()["token"] == "renewed"


def test_an_expired_session_asks_to_sign_in_again(fake_llu):
    local.save(_session(fake_llu, expires=int(time.time()) - 1))
    local.prepare()
    with pytest.raises(llu.LibreError, match="libre-mcp login"):
        asyncio.run(libre.refresh(local.USER_ID))


def test_a_rejected_token_asks_to_sign_in_again(fake_llu):
    local.save(_session(fake_llu))
    fake_llu.expire_tokens()
    local.prepare()
    with pytest.raises(llu.LibreError, match="libre-mcp login"):
        asyncio.run(libre.refresh(local.USER_ID))


# ---------- stdio, end to end ----------

def test_stdio_speaks_mcp(tmp_path):
    """The real `libre-mcp stdio` process: initialize, list the tools, and a
    tool call without a session answers with the sign-in hint."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_current_glucose", "arguments": {}}},
    ]
    env = {**os.environ, "LIBRE_MCP_SESSION": str(tmp_path / "none.json"),
           "MCP_DB": str(tmp_path / "solo.db"), "POLL_INTERVAL_S": "0"}
    env.pop("MYAUTH_URL", None)
    proc = subprocess.Popen([sys.executable, "-m", "libre_mcp.cli", "stdio"], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    answers = {}
    try:
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))
        proc.stdin.flush()
        deadline = time.time() + 60
        while 3 not in answers and time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if "id" in msg:
                answers[msg["id"]] = msg
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    assert 3 in answers, proc.stderr.read()
    assert answers[1]["result"]["serverInfo"]["name"] == "freestyle-libre"
    names = {t["name"] for t in answers[2]["result"]["tools"]}
    assert {"get_current_glucose", "whoami", "get_glucose_stats"} <= names
    call = answers[3]["result"]
    assert call["isError"] and "libre-mcp login" in call["content"][0]["text"]
