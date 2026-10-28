"""Test fixtures. No test talks to MyAuth or LibreLinkUp, and none touches a
real database: MyAuth is faked at `myauth._fetch_*` (including the LibreLinkUp
session it hands out), LibreLinkUp at the HTTP transport (`llu._client`). All
glucose values are made up."""
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import pytest

PATIENT = "11111111-2222-3333-4444-555555555555"
OTHER_PATIENT = "66666666-7777-8888-9999-000000000000"
LLU_USER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PUBLIC = "https://libre.example"
SAME_ORIGIN = {"Origin": PUBLIC}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_DB", str(tmp_path / "app.db"))
    # never the real ~/.config/libre-mcp of the standalone mode
    monkeypatch.setenv("LIBRE_MCP_SESSION", str(tmp_path / "config" / "session.json"))
    monkeypatch.setenv("MYAUTH_APP_KEY", "test-app-key")
    monkeypatch.setenv("POLL_INTERVAL_S", "0")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    for name in ("PUBLIC_URL", "MCP_URL", "UI_URL", "LLU_VERSION"):
        monkeypatch.delenv(name, raising=False)
    from libre_mcp import db, libre
    libre._locks.clear()
    db.init()
    yield tmp_path
    libre.use_source(libre.MyAuthSource())     # a standalone test must not leak into the next


# --- MyAuth ------------------------------------------------------------------


class FakeMyAuth:
    """Which browser cookie belongs to whom, who may use the app, and whose
    LibreLinkUp is connected. A session it hands out is a token FakeLLU
    accepts, as if MyAuth had just signed in."""

    def __init__(self, llu: "FakeLLU") -> None:
        self.llu = llu
        self.users: dict[int, dict] = {}
        self.cookies: dict[str, int] = {}
        self.allowed: set[int] = set()
        self.down = False
        self.libre: dict[int, dict] = {}
        self.libre_refused: str | None = None
        self.renewals = 0
        self.signins = 0

    def connect_libre(self, uid: int, patient: str = PATIENT, name: str = "Max Muster") -> None:
        self.libre[uid] = {"patient_id": patient, "patient_name": name}

    async def libre_token(self, uid, renew=False):
        from libre_mcp.access import myauth
        if self.down:
            raise myauth.Unavailable("down")
        if uid not in self.allowed:
            return 403, {"detail": "not_allowed"}
        if self.libre_refused:
            return 502, {"detail": "platform_refused", "message": self.libre_refused}
        entry = self.libre.get(uid)
        if entry is None:
            return 404, {"detail": "not_connected"}
        self.renewals += bool(renew)
        if renew or "token" not in entry:
            self.signins += 1
            entry["token"] = f"myauth-token-{self.signins}"
            self.llu.tokens.add(entry["token"])
        return 200, {"base_url": "https://api-de.libreview.io", "token": entry["token"],
                     "expires": self.llu.now + 180 * 86400, "account_id": LLU_USER,
                     "patient_id": entry["patient_id"], "patient_name": entry["patient_name"],
                     "account": entry["patient_name"]}

    async def connections(self, uid):
        from libre_mcp.access import myauth
        if self.down:
            raise myauth.Unavailable("down")
        entry = self.libre.get(uid)
        return 200, {"libre": {"connected": entry is not None,
                               "account": entry["patient_name"] if entry else None,
                               "last_error": self.libre_refused, "last_error_at": None}}

    def add(self, uid: int, username: str, display_name: str = "",
            cookie: str | None = None, allowed: bool = True) -> int:
        self.users[uid] = {"uid": uid, "username": username,
                           "display_name": display_name or username, "is_admin": False,
                           "timezone": "Europe/Berlin"}
        if cookie:
            self.cookies[cookie] = uid
        if allowed:
            self.allowed.add(uid)
        return uid

    async def session(self, cookie):
        from libre_mcp.access import myauth
        if self.down:
            raise myauth.Unavailable("down")
        uid = self.cookies.get(cookie)
        if uid is None:
            return 401, {"detail": "not_authenticated"}
        if uid not in self.allowed:
            return 403, {"detail": "app_not_allowed"}
        return 200, {**self.users[uid], "apps": ["mcp-libre"], "language": None}

    async def access(self, uid):
        from libre_mcp.access import myauth
        if self.down:
            raise myauth.Unavailable("down")
        if uid in self.users and uid in self.allowed:
            return 200, dict(self.users[uid])
        return 403, {"detail": "not_allowed"}


@pytest.fixture(autouse=True)
def fake_myauth(monkeypatch, fake_llu):
    from libre_mcp.access import myauth
    fake = FakeMyAuth(fake_llu)
    monkeypatch.setenv("MYAUTH_URL", "http://myauth.test")
    monkeypatch.setattr(myauth, "_fetch_session", fake.session)
    monkeypatch.setattr(myauth, "_fetch_access", fake.access)
    monkeypatch.setattr(myauth, "_fetch_libre", fake.libre_token)
    monkeypatch.setattr(myauth, "_fetch_connections", fake.connections)
    myauth.clear_cache()
    yield fake
    myauth.clear_cache()


# --- LibreLinkUp -------------------------------------------------------------


def llu_time(ts: int) -> str:
    """LibreLinkUp's FactoryTimestamp format, UTC."""
    d = datetime.fromtimestamp(ts, timezone.utc)
    hour = d.hour % 12 or 12
    return (f"{d.month}/{d.day}/{d.year} {hour}:{d.minute:02d}:{d.second:02d} "
            f"{'AM' if d.hour < 12 else 'PM'}")


class FakeLLU:
    """LibreLinkUp as seen through the HTTP transport: the graph, with the
    headers Abbott insists on (sign-in is MyAuth's - see FakeMyAuth)."""

    def __init__(self) -> None:
        self.patients = [{"patientId": PATIENT, "firstName": "Max", "lastName": "Muster"}]
        self.now = int(time.time())
        # 12 hours every 15 minutes, a gentle wave around 110
        self.points = [(self.now - i * 900, 110 + (i % 8) * 5) for i in range(48, 0, -1)]
        self.current = (self.now - 60, 124, 4)
        self.sensor = {"deviceId": "", "sn": "0M00ABCDEF", "a": self.now - 3 * 86400,
                       "w": 60, "pt": 4}
        self.tokens: set[str] = set()
        self.requests: list[httpx.Request] = []
        self.locked_s: int | None = None
        self.http_status: int | None = None
        self.extra_connection: dict = {}

    def expire_tokens(self) -> None:
        self.tokens.clear()

    def session(self):
        """A session as MyAuth hands it out, accepted by this fake."""
        from libre_mcp.librelinkup import client as llu
        self.tokens.add("direct-token")
        return llu.Auth("https://api-de.libreview.io", "direct-token",
                        self.now + 180 * 86400, LLU_USER)

    def paths(self) -> list[str]:
        return [f"{r.url.host}{r.url.path}" for r in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlparse(str(request.url))
        assert request.headers["product"] == "llu.android"
        assert request.headers["version"]
        if self.http_status:
            return httpx.Response(self.http_status, json={})
        if self.locked_s is not None:
            return httpx.Response(200, json={"status": 429, "data": {
                "code": 60, "data": {"failures": 3, "interval": 60,
                                     "lockout": self.locked_s}, "message": "locked"}})
        auth = request.headers.get("authorization", "")
        if auth.removeprefix("Bearer ") not in self.tokens:
            return httpx.Response(401, json={"message": "Unauthorized"})
        assert request.headers["account-id"] == hashlib.sha256(LLU_USER.encode()).hexdigest()
        assert url.hostname == "api-de.libreview.io"
        ticket = {"token": auth.removeprefix("Bearer "), "expires": self.now + 180 * 86400}
        if url.path.endswith("/graph"):
            pid = url.path.split("/")[3]
            if pid not in {p["patientId"] for p in self.patients}:
                return httpx.Response(200, json={"status": 4, "error": {
                    "message": "couldNotLoadPatient"}})
            return httpx.Response(200, json={"status": 0, "data": self.graph(pid),
                                             "ticket": ticket})
        return httpx.Response(404, json={})

    def graph(self, pid: str) -> dict:
        patient = next(p for p in self.patients if p["patientId"] == pid)
        ts, mg, trend = self.current
        return {
            "connection": {
                "id": "c1", "patientId": pid, "firstName": patient["firstName"],
                "lastName": patient["lastName"], "targetLow": 70, "targetHigh": 160,
                "uom": 1, "sensor": dict(self.sensor),
                "patientDevice": {"did": "x", "dtid": 40068, "v": "3.6.7", "ll": 70, "hl": 250,
                                  "alarms": True},
                "glucoseMeasurement": {"FactoryTimestamp": llu_time(ts), "Timestamp": "x",
                                       "ValueInMgPerDl": mg, "TrendArrow": trend,
                                       "isHigh": False, "isLow": False},
                # an active alarm - the case strict parsers trip over
                "glucoseAlarm": {"alarmType": 1, "value": 55},
                **self.extra_connection,
            },
            "activeSensors": [],
            "graphData": [{"FactoryTimestamp": llu_time(t), "Timestamp": "x",
                           "ValueInMgPerDl": v, "type": 0, "isHigh": False, "isLow": False}
                          for t, v in self.points],
        }


@pytest.fixture(autouse=True)
def fake_llu(monkeypatch):
    from libre_mcp.librelinkup import client as llu
    fake = FakeLLU()
    monkeypatch.setattr(llu, "_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handle), timeout=5))
    return fake


# --- app and users -----------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """The HTTP app as the browser sees it."""
    from starlette.testclient import TestClient

    from libre_mcp.server import build_http_app
    monkeypatch.setenv("PUBLIC_URL", PUBLIC)
    with TestClient(build_http_app(), base_url=PUBLIC) as c:
        yield c


@pytest.fixture
def user_id(fake_myauth):
    """Anja: a MyAuth user who may use the app, known to this server."""
    from libre_mcp import users
    fake_myauth.add(7, "anja", "Anja", cookie="anja-cookie")
    users.ensure_user(7, "anja", "Anja")
    return 7


@pytest.fixture
def anja(client, user_id):
    """A browser signed in to MyAuth as Anja."""
    client.cookies.set("sso_session", "anja-cookie")
    return client


@pytest.fixture
def connected(user_id, fake_myauth):
    """Anja with LibreLinkUp connected in MyAuth and the first 12 hours stored."""
    import asyncio

    from libre_mcp import libre
    fake_myauth.connect_libre(user_id)
    asyncio.run(libre.refresh(user_id))
    libre._locks.clear()                 # bound to the loop that just closed
    return user_id
