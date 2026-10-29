"""The account pages, with MyAuth deciding who is signed in."""
from conftest import SAME_ORIGIN
from test_csv import GERMAN
from test_oauth import _params, _register

from libre_mcp import libre, oauth, readings, users
from libre_mcp.access import myauth


def test_account_without_a_session_goes_to_the_myauth_login(client, user_id):
    r = client.get("/account", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == ("https://libre.example:1000/login"
                                     "?next=https%3A%2F%2Flibre.example%2Faccount")


def test_the_footer_shows_the_version_and_an_optional_note(anja, monkeypatch):
    from libre_mcp import __version__
    assert f"v{__version__}</footer>" in anja.get("/account").text
    monkeypatch.setenv("FOOTER_NOTE", "Private service <test>")
    assert f"v{__version__} · Private service &lt;test&gt;</footer>" in anja.get("/account").text


def test_a_pending_password_change_goes_to_myauth_first(client, user_id, monkeypatch):
    async def must_change(cookie):
        return 401, {"detail": "password_change_required"}
    monkeypatch.setattr(myauth, "_fetch_session", must_change)
    client.cookies.set("sso_session", "anja-cookie")
    r = client.get("/account", follow_redirects=False)
    assert r.headers["location"].startswith("https://libre.example:1000/password?next=")


def test_account_page(anja):
    page = anja.get("/account").text
    assert "Anja" in page and "not connected" in page
    assert 'href="https://libre.example:1000/connections#pf-libre">Connect LibreLinkUp in MyAuth' in page
    assert ">https://libre.example/mcp</textarea>" in page
    assert '<option value="24" selected>2 years</option>' in page
    assert "/static/ui/tokens.css?v=" in page


def test_msk_ui_is_served(client):
    r = client.get("/static/ui/tokens.css")
    assert r.status_code == 200 and "msk-ui v" in r.text


def test_a_user_without_the_app_gets_no_access(client, fake_myauth):
    fake_myauth.add(8, "bob", cookie="bob-cookie", allowed=False)
    client.cookies.set("sso_session", "bob-cookie")
    r = client.get("/account")
    assert r.status_code == 403 and "not enabled for Libre MCP" in r.text
    assert users.get_user(8) is None


def test_myauth_down_is_a_503(anja, fake_myauth):
    fake_myauth.down = True
    assert anja.get("/account").status_code == 503


def test_connected_shows_whose_glucose_and_links_to_myauth(anja, connected):
    page = anja.get("/account").text
    assert "connected</span> in MyAuth" in page and "Reads the glucose of <b>Max Muster</b>" in page
    assert "48</b> readings" in page and "Manage in MyAuth" in page
    assert 'name="password"' not in page                              # no login form here
    assert anja.get("/account/libre").status_code == 404


def test_a_refusal_in_myauth_shows_on_the_page(anja, connected, fake_myauth):
    fake_myauth.libre_refused = "LibreLinkUp rejected the password."
    myauth.clear_cache()
    assert "MyAuth: LibreLinkUp rejected the password." in anja.get("/account").text


def test_disconnected_in_myauth_keeps_the_history(anja, connected, fake_myauth):
    fake_myauth.libre.clear()
    myauth.clear_cache()
    page = anja.get("/account").text
    assert "not connected" in page and "48</b> readings" in page


def test_a_form_from_another_site_is_refused(anja, connected):
    r = anja.post("/account/delete", headers={"Origin": "https://libre.example:1010"})
    assert r.status_code == 403
    assert readings.summary(connected)["count"] == 48


def test_history_setting(anja, connected):
    r = anja.post("/account/history", headers=SAME_ORIGIN, data={"months": "0"})
    assert '<option value="0" selected>no limit</option>' in r.text
    assert anja.post("/account/history", headers=SAME_ORIGIN,
                     data={"months": "7"}).status_code == 400


def test_csv_import(anja, user_id):
    users.set_history_months(user_id, 0)
    r = anja.post("/account/import", headers=SAME_ORIGIN,
                  files={"file": ("export.csv", GERMAN.encode(), "text/csv")})
    assert r.status_code == 200 and "3 readings in the file, 3 of them new" in r.text
    assert "The history now holds 2 sensors." in r.text           # a month's gap: a change
    r = anja.post("/account/import", headers=SAME_ORIGIN,
                  files={"file": ("export.csv", GERMAN.encode(), "text/csv")})
    assert "3 of them new" not in r.text and "0 of them new" in r.text


def test_csv_import_refuses_other_files(anja):
    r = anja.post("/account/import", headers=SAME_ORIGIN,
                  files={"file": ("steps.csv", b"date,steps\n", "text/csv")})
    assert r.status_code == 400 and "LibreView" in r.text


def test_delete_my_data(anja, connected):
    token = oauth._mint_pair("client-1", connected, "mcp")["access_token"]
    r = anja.post("/account/delete", headers=SAME_ORIGIN)
    assert r.status_code == 200 and "No readings stored yet" in r.text   # still signed in
    assert "connected</span> in MyAuth" in r.text                  # MyAuth's connection stays
    assert libre.account(connected) is None
    assert readings.summary(connected)["count"] == 0
    assert oauth.access_token_user(token) is None


def test_the_ui_uses_the_host_the_browser_came_through(client, user_id, monkeypatch):
    monkeypatch.setenv("MCP_URL", "https://mcp.example/freestylelibre")
    ui = {"host": "home.example:1031"}
    r = client.get("/account", headers=ui, follow_redirects=False)
    assert r.headers["location"] == ("https://home.example:1000/login"
                                     "?next=https%3A%2F%2Fhome.example%3A1031%2Faccount")
    client.cookies.set("sso_session", "anja-cookie")
    assert ">https://mcp.example/freestylelibre</textarea>" in client.get(
        "/account", headers=ui).text
    ok = client.post("/account/history", data={"months": "12"}, follow_redirects=False,
                     headers={**ui, "origin": "https://home.example:1031"})
    assert ok.status_code == 303


def test_the_csv_is_read_in_the_users_zone(anja, user_id, fake_myauth):
    users.set_history_months(user_id, 0)
    fake_myauth.users[user_id]["timezone"] = "America/New_York"
    myauth.clear_cache()
    anja.post("/account/import", headers=SAME_ORIGIN,
              files={"file": ("export.csv", GERMAN.encode(), "text/csv")})
    first = readings.summary(user_id)["first"]
    assert first == 1790506920             # 27-09-2026 07:02 in New York = 11:02 UTC


def test_banner_when_an_admin_is_signed_in_as_the_user(anja, user_id, fake_myauth):
    assert "imp-banner" not in anja.get("/account").text
    fake_myauth.users[user_id]["impersonated_by"] = {"username": "max",
                                                     "display_name": "Max"}
    myauth.clear_cache()
    page = anja.get("/account").text
    assert "imp-banner" in page and "You are signed in as Anja - on behalf of Max." in page
    assert ('href="https://libre.example:1000/impersonate/stop?next='
            'https%3A%2F%2Flibre.example%2Faccount">Sign out as Anja') in page
    consent = anja.get("/oauth/authorize", params=_params(_register(anja))).text
    assert "imp-banner" in consent and "on behalf of Max" in consent


def test_account_shows_the_day_the_sensor_and_the_interval(anja, connected):
    page = anja.get("/account").text
    assert "Statistics" in page and '<option value="24h" selected>Last 24 hours' in page
    assert '<svg class="g24"' in page and "In range 70-180" in page
    assert "Sensor" in page and "0M00ABCDEF" in page and "version 3.6.7" in page
    assert 'name="minutes"' in page and '<option value="1" selected>' in page
    r = anja.post("/account/poll", data={"minutes": "15"}, headers=SAME_ORIGIN,
                  follow_redirects=False)
    assert r.status_code == 303
    assert users.poll_minutes(connected) == 15
    assert anja.post("/account/poll", data={"minutes": "7"},
                     headers=SAME_ORIGIN).status_code == 400


def test_statistics_period_filter(anja, connected):
    from libre_mcp import web
    now = int(libre.now_ms() / 1000)
    old = [(now - 20 * 86400 + i * 300, 100 + i % 50) for i in range(2000)]   # ~3 weeks ago
    readings.add(connected, old, "csv")
    page = anja.get("/account?period=7d").text
    assert '<option value="7d" selected>Last 7 days' in page
    assert "GMI (14 days)" in page                                      # independent of the period
    page = anja.get("/account?period=4w").text
    assert '<option value="4w" selected>' in page
    assert 'class="g-range"' in page and "averaged" in page             # averaged, with range
    assert '<option value="24h" selected>' in anja.get("/account?period=x").text   # unknown -> 24 h
    assert "No readings in the last 1 hour" in web._stats_card(999, "1h")


def test_live_cards_refresh_without_reload(anja, connected, client):
    page = anja.get("/account").text
    assert 'id="stats-body"' in page and "refreshes every minute" in page and "/account/live" in page
    newest = max(readings.latest(connected)[0], (readings.realtime_latest(connected) or (0,))[0])
    readings.add_realtime(connected, newest + 600, 150)                 # newer than anything so far
    readings.add_realtime(connected, newest + 660, 152)
    r = anja.get("/account/live?period=24h")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert set(d) == {"libre", "stats", "sensor", "updated"}
    assert 'class="g-live' in d["stats"] and "minute values up to" in d["stats"]
    assert "<b>152 mg/dL</b>" in d["libre"] and "minute value" in d["libre"]
    assert 'class="g-live' not in anja.get("/account/live?period=7d").json()["stats"]
    client.cookies.clear()
    assert client.get("/account/live").status_code == 401


def test_display_unit_mg_or_mmol(anja, connected):
    import json
    page = anja.get("/account").text
    assert "Like the Libre app (mg/dL)" in page and "In range 70-180" in page   # fake app: uom 1
    r = anja.post("/account/unit", data={"unit": "mmol", "period": "7d"}, headers=SAME_ORIGIN,
                  follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account?period=7d#stats"
    d = anja.get("/account/live?period=24h").json()
    assert "In range 3.9-10.0" in d["stats"] and "Below 3.9" in d["stats"] and "mmol/L" in d["stats"]
    assert "mmol/L" in d["libre"] and "mmol/L" in d["sensor"] and "mg/dL" not in d["sensor"]
    users.set_unit(connected, "auto")                                     # like the app ...
    info = json.loads(libre.account(connected)["sensor_info"])
    libre._update(connected, sensor_info=json.dumps({**info, "uom": 0}))  # ... that shows mmol/L
    assert "Like the Libre app (mmol/L)" in anja.get("/account").text
    assert anja.post("/account/unit", data={"unit": "kg"}, headers=SAME_ORIGIN).status_code == 400


def test_gmi_period_setting(anja, connected):
    from libre_mcp import stats
    now = int(libre.now_ms() / 1000)
    readings.add(connected, [(now - 20 * 86400 + i * 300, 200) for i in range(2000)], "csv")
    page = anja.get("/account?period=24h").text
    assert '<option value="14" selected>14 days' in page and "GMI (14 days)" in page
    assert "always covers the last 14 days" in page
    r = anja.post("/account/gmi", data={"days": "30", "period": "7d"}, headers=SAME_ORIGIN,
                  follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account?period=7d#stats"
    assert users.gmi_days(connected) == 30
    want = stats.summarize(readings.between(connected, now - 30 * 86400, now + 1), 30 * 86400)
    assert f'GMI (30 days)</span><b>{want["gmi_pct"]} %' in anja.get("/account").text
    assert anja.post("/account/gmi", data={"days": "7"}, headers=SAME_ORIGIN).status_code == 400
    assert users.gmi_days(connected) == 30


def test_account_lists_every_sensor_and_marks_the_changes(anja, connected, fake_llu):
    from libre_mcp import sensors
    users.set_history_months(connected, 0)
    now = fake_llu.now
    readings.add(connected, [(t, 100) for t in range(now - 20 * 86400, now - 8 * 86400, 300)],
                 "csv")
    sensors.rebuild(connected)
    page = anja.get("/account?period=4w").text
    assert "All sensors (2)" in page and "may have started earlier" in page
    assert '<span class="badge ok">running</span>' in page and "<code>0M00ABCDEF</code>" in page
    assert "sensor 2 · day 4" in page and "measured by <b>2</b> sensors" in page
    assert page.count('class="g-sensor"') == 2 and ">#2</text>" in page
    assert "Dotted vertical lines mark a new sensor" in page
    assert 'class="g-sensor"' not in anja.get("/account?period=24h").text   # no change


def test_an_unexpected_myauth_answer_fails_closed(client, user_id, monkeypatch):
    """Since access.web: anything but 200/401/403 from MyAuth is "sign-in
    unavailable", not a redirect to the sign-in page."""
    async def broken(cookie):
        return 500, {}
    monkeypatch.setattr(myauth, "_fetch_session", broken)
    myauth.clear_cache()
    client.cookies.set("sso_session", "anja-cookie")
    r = client.get("/account", follow_redirects=False)
    assert r.status_code == 503 and "Sign-in unavailable" in r.text
