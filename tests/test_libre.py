"""Polling with the LibreLinkUp session from MyAuth, and what the collector
does when things go wrong."""
import asyncio
import sqlite3

import pytest

from conftest import OTHER_PATIENT, PATIENT
from libre_mcp import db, libre, readings, users
from libre_mcp.librelinkup import client as llu


def run(coro):
    return asyncio.run(coro)


def test_the_first_poll_stores_whose_glucose_and_12_hours(connected, fake_llu, fake_myauth):
    acc = libre.account(connected)
    assert acc["patient_id"] == PATIENT and acc["patient_name"] == "Max Muster"
    assert (acc["target_low"], acc["target_high"]) == (70, 160)
    assert readings.summary(connected)["count"] == len(fake_llu.points)
    assert fake_myauth.signins == 1


def test_nothing_about_the_login_is_stored(connected):
    with db.conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(libre_accounts)")}
    assert not cols & {"email", "password_enc", "token_enc", "account_id", "base_url"}


def test_polling_again_adds_only_new_readings(connected, fake_llu):
    fake_llu.points.append((fake_llu.now + 900, 130))
    run(libre.refresh(connected))
    assert readings.summary(connected)["count"] == len(fake_llu.points)


def test_each_poll_keeps_the_current_value_apart(connected, fake_llu):
    ts, mg, _ = fake_llu.current
    assert readings.realtime_between(connected, ts, ts + 1) == [(ts, mg)]   # from the first poll
    before = readings.summary(connected)["count"]
    fake_llu.current = (ts + 60, mg + 4, 3)
    run(libre.refresh(connected))
    run(libre.refresh(connected))                                       # same value, no duplicate
    assert readings.realtime_between(connected, ts, ts + 3600) == [(ts, mg), (ts + 60, mg + 4)]
    assert readings.summary(connected)["count"] == before               # history untouched


def test_an_expired_session_asks_myauth_to_sign_in_again(connected, fake_llu, fake_myauth):
    fake_llu.expire_tokens()
    run(libre.refresh(connected))
    assert libre.account(connected)["last_error"] is None
    assert fake_myauth.renewals == 1


def test_myauth_refusing_is_recorded_and_the_history_stays(connected, fake_llu, fake_myauth):
    fake_llu.expire_tokens()
    fake_myauth.libre_refused = "LibreLinkUp did not accept this e-mail and password."
    with pytest.raises(llu.LibreError, match="did not accept"):
        run(libre.refresh(connected))
    assert "MyAuth account page" in libre.account(connected)["last_error"]
    assert readings.summary(connected)["count"] > 0


def test_myauth_down(connected, fake_myauth):
    fake_myauth.down = True
    from libre_mcp.access import myauth
    myauth.clear_cache()
    with pytest.raises(llu.LibreError, match="not answering"):
        run(libre.refresh(connected))


def test_a_lockout_pauses_without_asking_again(connected, fake_llu):
    fake_llu.locked_s = 300
    with pytest.raises(llu.RateLimited):
        run(libre.refresh(connected))
    before = len(fake_llu.requests)
    with pytest.raises(llu.LibreError, match="pause"):
        run(libre.refresh(connected))
    assert len(fake_llu.requests) == before


def test_not_connected_in_myauth(user_id):
    with pytest.raises(libre.NotConnected, match="MyAuth"):
        run(libre.refresh(user_id))
    assert libre.account(user_id) is None


def test_somebody_else_in_myauth_drops_the_old_readings(connected, fake_llu, fake_myauth):
    month_ago = fake_llu.now - 30 * 86400
    readings.add(connected, [(month_ago, 99)], "csv")
    run(libre.refresh(connected))
    assert readings.between(connected, month_ago, month_ago + 1)       # same person: kept

    fake_llu.patients.append({"patientId": OTHER_PATIENT, "firstName": "Eva",
                              "lastName": "Muster"})
    fake_myauth.connect_libre(connected, OTHER_PATIENT, "Eva Muster")
    from libre_mcp.access import myauth
    myauth.clear_cache()
    run(libre.refresh(connected))
    assert not readings.between(connected, month_ago, month_ago + 1)
    assert libre.account(connected)["patient_name"] == "Eva Muster"


def test_poll_all_polls_everybody_connected_in_myauth(connected, fake_llu, fake_myauth):
    fake_myauth.add(8, "bob")
    users.ensure_user(8, "bob")
    fake_llu.points.append((fake_llu.now + 900, 130))
    run(libre.poll_all())
    assert readings.latest(connected) != (fake_llu.now + 900, 130)      # just polled
    libre._update(connected, last_poll_ms=libre.now_ms() - 60_000)
    run(libre.poll_all())
    assert readings.latest(connected) == (fake_llu.now + 900, 130)
    assert readings.latest(8) is None and libre.account(8) is None


def test_each_user_is_polled_as_often_as_they_chose(connected, fake_llu):
    assert users.poll_minutes(connected) == 1
    users.set_poll_minutes(connected, 15)
    libre._update(connected, last_poll_ms=libre.now_ms() - 10 * 60_000)
    assert not libre._due(connected)                                    # only after 15 min
    libre._update(connected, last_poll_ms=libre.now_ms() - 15 * 60_000)
    assert libre._due(connected)
    with pytest.raises(ValueError):
        users.set_poll_minutes(connected, 2)


def test_sensor_and_app_details_are_kept(connected):
    import json
    info = json.loads(libre.account(connected)["sensor_info"])
    assert info["warmup_min"] == 60 and info["type_code"] == 4
    assert "trend" in info and "current_mg" in info and info["app_version"] == "3.6.7"


def test_the_old_login_columns_are_dropped(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("MCP_DB", str(path))
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE libre_accounts (user_id INTEGER PRIMARY KEY, email TEXT, "
              "password_enc TEXT, base_url TEXT, token_enc TEXT, token_expires_s INTEGER, "
              "account_id TEXT, patient_id TEXT, patient_name TEXT NOT NULL DEFAULT '', "
              "updated_at_ms INTEGER NOT NULL)")
    c.execute("INSERT INTO libre_accounts VALUES (7, 'a@b', 'ENC', 'u', 'TOK', 1, 'x', ?, "
              "'Max', 1)", (PATIENT,))
    c.commit()
    c.close()
    db.init()
    db.init()                                                            # idempotent
    acc = libre.account(7)
    assert acc["patient_id"] == PATIENT and "password_enc" not in acc
