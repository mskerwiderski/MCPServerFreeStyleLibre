"""The sensor history: sensors LibreLinkUp reports, sensors inferred from gaps
in the stored history, and which readings belong to which sensor."""
import asyncio
import sqlite3
import time

import pytest

from conftest import PATIENT
from libre_mcp import db, libre, readings, sensors, times, users

DAY = 86400


def run(coro):
    return asyncio.run(coro)


def segment(start, end, step=300, mg=110):
    """A sensor's evenly spaced readings from start up to (not including) end."""
    return [(t, mg) for t in range(start, end, step)]


def test_a_reported_sensor_is_kept_and_a_new_one_follows(connected, fake_llu):
    first = sensors.all_of(connected)
    assert [(s["serial"], s["started_s"], s["source"]) for s in first] == [
        ("0M00ABCDEF", fake_llu.now - 3 * DAY, "llu")]
    assert first[0]["type_code"] == 4 and first[0]["last_seen_s"] >= fake_llu.now - 5
    new_start = fake_llu.now + 600                              # sensor change
    fake_llu.sensor = {"sn": "0M00GHIJKL", "a": new_start, "w": 60, "pt": 4}
    fake_llu.points.append((new_start + 3700, 140))
    run(libre.refresh(connected))
    worn = sensors.all_of(connected)
    assert [s["serial"] for s in worn] == ["0M00ABCDEF", "0M00GHIJKL"]
    assert worn[0]["until_s"] == new_start and worn[1]["until_s"] is None
    pts = readings.between(connected, 0, new_start + DAY)
    assert [(sp["number"], sp["readings"]) for sp in sensors.spans(worn, pts)] == [(1, 48), (2, 1)]


def test_sensors_before_the_server_are_inferred_from_gaps(connected, fake_llu):
    users.set_history_months(connected, 0)
    now = fake_llu.now
    a, b = now - 40 * DAY, now - 25 * DAY                  # first readings of A and B
    readings.add(connected, segment(a, b - 2 * 3600) + segment(b, now - 4 * DAY), "csv")
    sensors.rebuild(connected)
    worn = sensors.all_of(connected)
    assert [(s["source"], s["started_s"], s["serial"]) for s in worn] == [
        ("first", a - 3600, ""), ("gap", b - 3600, ""), ("llu", now - 3 * DAY, "0M00ABCDEF")]
    # the gap before the reported sensor is its change, not another sensor
    assert sensors.of(worn, b - 2 * 3600 - 300)["number"] == 1
    assert sensors.of(worn, b)["number"] == 2 and sensors.of(worn, now)["number"] == 3
    sensors.rebuild(connected)                                     # idempotent
    assert sensors.all_of(connected) == worn


def test_a_gap_while_a_reported_sensor_ran_is_lost_data(connected, fake_llu):
    now = fake_llu.now
    readings.add(connected, segment(now - 3 * DAY + 3600, now - 2 * DAY)
                 + segment(now - 2 * DAY + 3 * 3600, now - 13 * 3600), "csv")   # 3 h hole
    sensors.rebuild(connected)
    assert [s["source"] for s in sensors.all_of(connected)] == ["llu"]


def test_short_gaps_are_no_change_and_a_start_never_precedes_the_old_reading(user_id):
    t = int(time.time()) - 10 * DAY
    pts = segment(t, t + 3600) + segment(t + 5400, t + 7200)       # 35 min: no change
    last = pts[-1][0]
    pts += segment(last + 57 * 60, last + 57 * 60 + 3600)         # 57 min: change
    readings.add(user_id, pts, "csv")
    sensors.rebuild(user_id)
    worn = sensors.all_of(user_id)
    assert [(s["source"], s["started_s"]) for s in worn] == [("first", t - 3600),
                                                             ("gap", last + 1)]
    assert sensors.of(worn, last)["number"] == 1


def test_find_by_number_serial_or_start_day(connected, fake_llu):
    one = sensors.find(connected, 1)
    assert one["serial"] == "0M00ABCDEF"
    assert sensors.find(connected, "0m00abcdef") == one == sensors.find(connected, "1")
    assert sensors.find(connected, times.fmt(fake_llu.now - 3 * DAY)[:10]) == one
    for key, message in ((2, "no sensor 2"), ("2020-01-01", "No sensor started on 2020-01-01"),
                         ("XYZ", "serial number XYZ")):
        with pytest.raises(ValueError, match=message):
            sensors.find(connected, key)


def test_history_tells_how_long_each_sensor_ran_and_what_it_measured(connected, fake_llu):
    users.set_history_months(connected, 0)
    now = fake_llu.now
    readings.add(connected, segment(now - 20 * DAY, now - 8 * DAY, mg=60), "csv")  # lows only
    sensors.rebuild(connected)
    old, cur = sensors.history(connected, now=now)
    assert (old["number"], old["source"], old["status"], old["days"]) == (1, "first", "ended", 12.0)
    assert old["readings"] == 12 * 288 and old["coverage_pct"] == 100.0
    assert (old["mean"], old["in_range_pct"], old["below_70_pct"]) == (60, 0.0, 100.0)
    assert (cur["number"], cur["status"], cur["days"], cur["readings"]) == (2, "running", 3.0, 48)
    assert cur["last_s"] == fake_llu.points[-1][0]


def test_a_new_sensor_warms_up_and_the_one_before_has_ended(user_id):
    now = int(time.time())
    sensors.seen(user_id, "0M00ALT", now - 15 * DAY, now=now - DAY)
    readings.add(user_id, segment(now - 15 * DAY + 3600, now - DAY), "csv")
    assert [(s["status"], s["days"]) for s in sensors.history(user_id, now=now)] == [("ended", 14.0)]
    assert sensors.seen(user_id, "0M00NEU", now - 600, now=now)
    assert not sensors.seen(user_id, "0M00NEU", now - 600, now=now)            # already known
    assert [s["status"] for s in sensors.history(user_id, now=now)] == ["ended", "warming_up"]


def test_the_history_limit_takes_old_sensors_along(user_id):
    now = int(time.time())
    users.set_history_months(user_id, 0)
    readings.add(user_id, segment(now - 200 * DAY, now - 186 * DAY)
                 + segment(now - 100 * DAY, now - 86 * DAY)
                 + segment(now - 30 * DAY, now - 16 * DAY), "csv")
    sensors.rebuild(user_id)
    assert len(sensors.all_of(user_id)) == 3
    users.set_history_months(user_id, 3)          # up to a good 91 days ago: the second one stays
    readings.prune(user_id)
    assert [s["started_s"] for s in sensors.all_of(user_id)] == [now - 100 * DAY - 3600,
                                                                  now - 30 * DAY - 3600]
    readings.delete_all(user_id)
    assert sensors.all_of(user_id) == []


def test_somebody_else_in_myauth_brings_their_own_sensors(connected, fake_llu, fake_myauth):
    from conftest import OTHER_PATIENT
    from libre_mcp.access import myauth
    fake_llu.patients.append({"patientId": OTHER_PATIENT, "firstName": "Eva",
                              "lastName": "Muster"})
    fake_llu.sensor = {"sn": "0M00EVA123", "a": fake_llu.now - DAY, "w": 60, "pt": 4}
    fake_myauth.connect_libre(connected, OTHER_PATIENT, "Eva Muster")
    myauth.clear_cache()
    run(libre.refresh(connected))
    assert [s["serial"] for s in sensors.all_of(connected)] == ["0M00EVA123"]


def test_the_current_sensor_moves_out_of_libre_accounts(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("MCP_DB", str(path))
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE libre_accounts (user_id INTEGER PRIMARY KEY, patient_id TEXT, "
              "patient_name TEXT NOT NULL DEFAULT '', target_low INTEGER, target_high INTEGER, "
              "sensor_serial TEXT, sensor_started_s INTEGER, sensor_info TEXT, "
              "last_poll_ms INTEGER, last_ok_ms INTEGER, last_error TEXT, "
              "blocked_until_ms INTEGER, updated_at_ms INTEGER NOT NULL)")
    c.execute("INSERT INTO libre_accounts (user_id, patient_id, sensor_serial, sensor_started_s,"
              " sensor_info, last_ok_ms, updated_at_ms) VALUES (7, ?, '0M00ABCDEF', 1790000000,"
              " '{\"type_code\": 4, \"warmup_min\": 60}', 1790500000000, 1)", (PATIENT,))
    c.commit()
    c.close()
    db.init()
    db.init()                                                            # idempotent
    assert [(s["serial"], s["started_s"], s["source"], s["type_code"], s["warmup_min"],
             s["last_seen_s"]) for s in sensors.all_of(7)] == [
        ("0M00ABCDEF", 1790000000, "llu", 4, 60, 1790500000)]
    acc = libre.account(7)
    assert acc["patient_id"] == PATIENT and "sensor_serial" not in acc
