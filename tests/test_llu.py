"""The LibreLinkUp client against a fake of Abbott's API (sign-in is MyAuth's)."""
import asyncio

import pytest

from conftest import PATIENT
from libre_mcp.librelinkup import client as llu


def run(coro):
    return asyncio.run(coro)


def test_graph_sends_the_account_id_and_parses(fake_llu):
    g = run(llu.graph(fake_llu.session(), PATIENT))   # FakeLLU asserts the account-id header
    assert g.patient_name == "Max Muster"
    assert (g.target_low, g.target_high) == (70, 160)
    assert g.sensor_serial == "0M00ABCDEF" and g.sensor_started_s == fake_llu.now - 3 * 86400
    assert g.points == sorted(fake_llu.points)
    assert (g.current.ts, g.current.mg_dl, g.current.trend) == fake_llu.current


def test_an_active_alarm_and_odd_items_do_not_break_the_read(fake_llu):
    fake_llu.extra_connection = {"glucoseAlarm": {"unexpected": [1, 2]}, "newField": 1}
    body = {"status": 0, "data": fake_llu.graph(PATIENT)}
    body["data"]["graphData"] += [{"FactoryTimestamp": "garbage", "ValueInMgPerDl": 100},
                                  {"FactoryTimestamp": "9/28/2026 5:02:31 AM"}, "nonsense"]
    g = llu.parse_graph(body)
    assert len(g.points) == len(fake_llu.points)
    assert run(llu.graph(fake_llu.session(), PATIENT)).current is not None


def test_timestamps_are_utc():
    assert llu.parse_time("9/28/2026 5:02:31 PM") == 1790614951


def test_version_can_be_raised_without_a_release(fake_llu, monkeypatch):
    monkeypatch.setenv("LLU_VERSION", "4.99.0")
    run(llu.graph(fake_llu.session(), PATIENT))
    assert fake_llu.requests[0].headers["version"] == "4.99.0"


def test_lockout_inside_a_200(fake_llu):
    fake_llu.locked_s = 300
    with pytest.raises(llu.RateLimited) as exc:
        run(llu.graph(fake_llu.session(), PATIENT))
    assert exc.value.wait_s == 300


def test_http_429(fake_llu):
    fake_llu.http_status = 429
    with pytest.raises(llu.RateLimited):
        run(llu.graph(fake_llu.session(), PATIENT))


def test_expired_token(fake_llu):
    auth = fake_llu.session()
    fake_llu.expire_tokens()
    with pytest.raises(llu.AuthExpired):
        run(llu.graph(auth, PATIENT))


def test_only_a_uuid_goes_into_the_path(fake_llu):
    with pytest.raises(ValueError):
        run(llu.graph(fake_llu.session(), "../../evil"))
