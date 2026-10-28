"""Readings for MyAuth (internal.py): Docker network only, this app's own key."""

from libre_mcp import readings, users

KEY = {"authorization": "Bearer test-app-key"}
T0 = 1_790_000_000


def _get(client, headers=KEY, **params):
    q = {"uid": 7, "from": T0, "to": T0 + 3600, **params}
    return client.get("/internal/readings", params=q, headers=headers)


def test_readings_of_a_window(client, user_id):
    readings.add(user_id, [(T0 - 300, 90), (T0, 100), (T0 + 300, 110), (T0 + 3600, 120)], "graph")
    r = _get(client)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"unit": "mg/dL", "display_unit": "mgdl",
                        "readings": [[T0, 100], [T0 + 300, 110]], "realtime": []}


def test_only_myauth_from_the_docker_network(client, user_id):
    assert _get(client, headers={}).status_code == 401
    assert _get(client, headers={"authorization": "Bearer other"}).status_code == 401
    assert _get(client, headers={**KEY, "x-forwarded-for": "100.64.0.1"}).status_code == 403


def test_bad_requests(client, user_id):
    assert _get(client, to=T0).status_code == 400                      # empty window
    assert _get(client, to=T0 + 40 * 3600).status_code == 400          # too long
    assert _get(client, uid="x").status_code == 400
    assert _get(client, uid=99).status_code == 404                     # unknown here


def test_current_values_are_kept_apart_from_the_history(client, user_id):
    assert readings.add_realtime(user_id, T0 + 60, 101)
    assert not readings.add_realtime(user_id, T0 + 60, 101)             # second poll, same value
    readings.add_realtime(user_id, T0 + 120, 103)
    body = _get(client).json()
    assert body["realtime"] == [[T0 + 60, 101], [T0 + 120, 103]] and body["readings"] == []
    assert readings.summary(user_id)["count"] == 0                      # statistics untouched


def _gmi(client, headers=KEY, **params):
    return client.get("/internal/gmi", params={"uid": 7, "at": T0, **params}, headers=headers)


def test_gmi_over_the_users_period(client, user_id):
    # 14 days every 5 minutes, mean 150; before that (outside) a day at 300
    readings.add(user_id, [(T0 - 14 * 86400 + i * 300, 140 + 20 * (i % 2))
                           for i in range(14 * 288)], "graph")
    readings.add(user_id, [(T0 - 16 * 86400 + i * 300, 300) for i in range(288)], "graph")
    r = _gmi(client)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    b = r.json()
    assert b["period_days"] == 14 and b["from"] == T0 - 14 * 86400 and b["to"] == T0
    assert b["mean"] == 150 and b["gmi_pct"] == round(3.31 + 0.02392 * 150, 1)
    assert b["coverage_pct"] == 100.0 and b["display_unit"] == "mgdl"
    users.set_gmi_days(user_id, 30)                                     # the setting here, not the caller's
    b = _gmi(client).json()
    assert b["period_days"] == 30 and b["mean"] > 150 and b["coverage_pct"] < 70
    users.set_unit(user_id, "mmol")
    assert _gmi(client).json()["display_unit"] == "mmol"


def test_gmi_without_readings_and_bad_requests(client, user_id):
    b = _gmi(client).json()
    assert b["readings"] == 0 and "gmi_pct" not in b
    assert _gmi(client, headers={}).status_code == 401
    assert _gmi(client, headers={**KEY, "x-forwarded-for": "100.64.0.1"}).status_code == 403
    assert _gmi(client, at="x").status_code == 400
    assert _gmi(client, uid=99).status_code == 404
