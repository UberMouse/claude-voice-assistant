from fastapi.testclient import TestClient

from vm.claude_daemon.server import build_app
from vm.claude_daemon.session import SessionStore


class FakeProcess:
    def __init__(self):
        self.session_id = None
        self.last_rate_limit = None
        self.asks = []
        self._resume = None

    async def ensure_alive(self, resume_id):
        self._resume = resume_id

    async def ask(self, text, persist_session_id):
        self.asks.append(text)
        if self.session_id is None:
            self.session_id = "p-sid"
            persist_session_id("p-sid")
        return "answered:" + text

    async def stop(self):
        pass


def test_ask_persists_and_returns(tmp_path):
    proc = FakeProcess()
    store = SessionStore(tmp_path / "sid")
    app = build_app(process=proc, store=store)
    with TestClient(app) as client:
        r = client.post("/ask", json={"text": "hi", "mode": "oneshot"})
        assert r.status_code == 200
        assert r.json()["result_text"] == "answered:hi"
        assert store.read() == "p-sid"
        client.post("/ask", json={"text": "again", "mode": "oneshot"})
        assert proc.asks == ["hi", "again"]
        # ensure_alive was called once via lifespan, with whatever was in the store at startup
        assert proc._resume is None  # tmp store was empty


def test_status_exposes_rate_limit(tmp_path):
    proc = FakeProcess()
    proc.session_id = "abc"
    proc.last_rate_limit = {"status": "allowed", "rateLimitType": "five_hour"}
    store = SessionStore(tmp_path / "sid")
    app = build_app(process=proc, store=store)
    with TestClient(app) as client:
        s = client.get("/status").json()
        assert s["session_id"] == "abc"
        assert s["rate_limit"]["status"] == "allowed"
