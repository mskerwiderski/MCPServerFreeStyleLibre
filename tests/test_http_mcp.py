"""The MCP endpoint as a remote client sees it: auth, tenancy, and the tools."""
import json
from datetime import datetime
import time
from zoneinfo import ZoneInfo

from libre_mcp import oauth, readings, users
from libre_mcp.access import myauth

HEADERS = {"Accept": "application/json, text/event-stream",
           "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18"}

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}}}


def _auth(token):
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def _token_for(user_id: int) -> str:
    return oauth._mint_pair("client-1", user_id, "mcp")["access_token"]


def call(client, user_id, name, **args) -> tuple[bool, object]:
    """(is_error, result) of one tool call."""
    r = client.post("/mcp", headers=_auth(_token_for(user_id)), json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": args}})
    result = r.json()["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        return True, text
    return False, json.loads(text)


def test_health(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_no_token_is_401(client):
    r = client.post("/mcp", json=INIT, headers=HEADERS)
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


def test_oauth_token_initializes_and_lists_the_tools(client, user_id):
    token = _token_for(user_id)
    r = client.post("/mcp", json=INIT, headers=_auth(token))
    assert r.json()["result"]["serverInfo"]["name"] == "freestyle-libre"
    r = client.post("/mcp", headers=_auth(token),
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {t["name"] for t in r.json()["result"]["tools"]} == {
        "whoami", "get_current_glucose", "get_glucose_readings", "get_glucose_stats",
        "get_daily_pattern", "list_glucose_episodes", "get_sensor", "list_sensors"}


def test_revoking_the_app_in_myauth_cuts_the_connector_off(client, user_id, fake_myauth):
    token = _token_for(user_id)
    assert client.post("/mcp", json=INIT, headers=_auth(token)).status_code == 200
    fake_myauth.allowed.discard(user_id)
    myauth.clear_cache()
    assert client.post("/mcp", json=INIT, headers=_auth(token)).status_code == 401


def test_myauth_down_is_a_503(client, user_id, fake_myauth):
    fake_myauth.down = True
    assert client.post("/mcp", json=INIT, headers=_auth(_token_for(user_id))).status_code == 503


def test_a_reassigned_uid_loses_everything(client, connected, fake_myauth):
    token = _token_for(connected)
    fake_myauth.users[connected]["username"] = "eve"
    assert client.post("/mcp", json=INIT, headers=_auth(token)).status_code == 401
    assert readings.summary(connected)["count"] == 0


def test_two_accounts_never_see_each_other(client, connected, fake_myauth):
    """The tenancy boundary: the same process, two bearers, two people."""
    bob = fake_myauth.add(8, "bob", "Bob")
    users.ensure_user(bob, "bob", "Bob")
    t = int(time.time()) - 3600
    readings.add(bob, [(t, 250), (t + 900, 260)], "csv")
    _, anja = call(client, connected, "get_glucose_stats")
    _, bobs = call(client, bob, "get_glucose_stats")
    assert anja["max"] < 200 and bobs["max"] == 260 and bobs["readings"] == 2


# --- tools -------------------------------------------------------------------


def test_not_connected_is_a_readable_error(client, user_id):
    err, text = call(client, user_id, "get_current_glucose")
    assert err and "not connected yet" in text
    _, who = call(client, user_id, "whoami")
    assert who["collector"] == {"connected": False}


def test_whoami(client, connected):
    _, who = call(client, connected, "whoami")
    assert who["glucose_of"] == "Max Muster" and who["collector"]["connected"]
    assert who["history"]["readings"] == 48 and who["history"]["kept_months"] == 24
    assert who["history"]["sensors"] == 1


def test_current_glucose_live_and_fallback(client, connected, fake_llu):
    _, cur = call(client, connected, "get_current_glucose")
    assert cur["mg_dl"] == 124 and cur["trend"] == "rising" and cur["live"] is True
    assert cur["target_range"] == "70-160"
    fake_llu.http_status = 502
    _, cur = call(client, connected, "get_current_glucose")
    assert cur["live"] is False and "HTTP 502" in cur["note"]
    assert cur["mg_dl"] == fake_llu.points[-1][1]


def test_readings_raw_and_condensed(client, connected):
    _, raw = call(client, connected, "get_glucose_readings")
    assert raw["columns"] == ["time", "mg_dl"] and len(raw["rows"]) == 48
    assert raw["rows"][0][0].count("T") == 1                    # local time strings
    _, few = call(client, connected, "get_glucose_readings", max_points=12)
    assert few["columns"] == ["bucket_start", "mean", "min", "max"]
    assert len(few["rows"]) <= 12 and few["bucket_minutes"] in (60, 120)   # clock-aligned


def test_stats_and_pattern(client, connected):
    _, s = call(client, connected, "get_glucose_stats")
    assert s["readings"] == 48 and s["time_in_ranges_pct"]["in_range"] == 100.0
    assert s["coverage_pct"] < 70                               # 12 of 14 days missing
    assert s["gmi_setting_days"] == 14
    _, p = call(client, connected, "get_daily_pattern")
    assert {"hour", "n", "p5", "p50", "p95"} <= set(p["hours"][0])


def test_episodes(client, connected):
    t = int(time.time()) - 2 * 86400
    readings.add(connected, [(t + i * 900, 60) for i in range(3)], "csv")
    _, e = call(client, connected, "list_glucose_episodes")
    assert e["lows"]["count"] == 1 and e["lows"]["episodes"][0]["minutes"] == 45
    assert e["highs"]["count"] == 0


def test_a_period_without_data_says_what_is_there(client, connected):
    err, text = call(client, connected, "get_glucose_stats",
                     date_from="2020-01-01", date_to="2020-01-31")
    assert err and "No readings between 2020-01-01T00:00" in text
    assert "stored readings cover" in text


def test_bad_dates_are_readable(client, connected):
    err, text = call(client, connected, "get_glucose_readings", date_from="yesterday")
    assert err and "Invalid isoformat" in text


def test_sensor(client, connected):
    _, s = call(client, connected, "get_sensor")
    assert s["serial"] == "0M00ABCDEF" and s["days"] == 3.0 and s["status"] == "running"
    assert (s["number"], s["of"], s["start"]) == (1, 1, "reported")
    assert s["ends_at"]["libre_3"] < s["ends_at"]["libre_3_plus"]


def test_the_tools_say_which_sensor_measured(client, connected, fake_llu):
    from libre_mcp import sensors
    users.set_history_months(connected, 0)
    now = fake_llu.now
    readings.add(connected, [(t, 60) for t in range(now - 20 * 86400, now - 8 * 86400, 300)],
                 "csv")
    sensors.rebuild(connected)
    _, worn = call(client, connected, "list_sensors")
    assert worn["count"] == 2
    one, two = worn["sensors"]
    assert (one["number"], one["start"], one["serial"], one["status"]) == (
        1, "history_start", None, "ended")
    assert one["days"] == 12.0 and one["below_70_pct"] == 100.0
    assert (two["serial"], two["start"], two["status"]) == ("0M00ABCDEF", "reported", "running")

    _, both = call(client, connected, "get_glucose_stats")
    assert [(m["sensor"], m["serial"]) for m in both["sensors"]] == [(1, None), (2, "0M00ABCDEF")]
    _, first = call(client, connected, "get_glucose_stats", sensor=1)
    assert first["readings"] == 12 * 288 and first["max"] == 60
    assert [m["sensor"] for m in first["sensors"]] == [1]
    _, second = call(client, connected, "get_glucose_readings", sensor="0M00ABCDEF")
    assert len(second["rows"]) == 48 and second["sensors"][0]["readings"] == 48
    _, lows = call(client, connected, "list_glucose_episodes", date_from="2000-01-01")
    assert lows["lows"]["count"] == 1 and lows["lows"]["episodes"][0]["sensor"] == 1
    _, day = call(client, connected, "get_daily_pattern", sensor=2)
    assert [m["sensor"] for m in day["sensors"]] == [2]

    err, text = call(client, connected, "get_glucose_stats", sensor=5)
    assert err and "no sensor 5" in text and "list_sensors" in text
    err, text = call(client, connected, "get_glucose_stats", sensor=2, date_to="2000-01-01")
    assert err and "Sensor 2 was not worn then" in text


def test_the_collector_starts_and_stops_with_the_app(monkeypatch):
    from starlette.testclient import TestClient

    from libre_mcp.server import build_http_app
    monkeypatch.setenv("POLL_INTERVAL_S", "900")
    with TestClient(build_http_app()) as c:
        assert c.get("/healthz").status_code == 200


def test_times_follow_the_users_zone_from_myauth(client, connected, fake_myauth):
    _, berlin = call(client, connected, "whoami")
    fake_myauth.users[connected]["timezone"] = "America/New_York"
    myauth.clear_cache()
    _, ny = call(client, connected, "whoami")
    assert ny["timezone"] == "America/New_York"
    b, n = (datetime.fromisoformat(x["history"]["last"]) for x in (berlin, ny))
    # Same instant in both zones; the offset is not fixed (5 h in the week
    # between the European and the US end of daylight saving time, else 6 h).
    ny_zone, berlin_zone = ZoneInfo("America/New_York"), ZoneInfo("Europe/Berlin")
    assert b != n
    assert b == n.replace(tzinfo=ny_zone).astimezone(berlin_zone).replace(tzinfo=None)
