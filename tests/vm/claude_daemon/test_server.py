import pytest
from fastapi.testclient import TestClient

from vm.claude_daemon.process import AskResult
from vm.claude_daemon.server import build_app
from vm.claude_daemon.session import SessionStore


class FakeProcess:
    def __init__(self, speak_seen: bool = True):
        self.session_id = None
        self.last_rate_limit = None
        self.asks = []
        self._resume = None
        self._speak_seen = speak_seen

    async def ensure_alive(self, resume_id):
        self._resume = resume_id

    async def ask(self, text, persist_session_id):
        self.asks.append(text)
        if self.session_id is None:
            self.session_id = "p-sid"
            persist_session_id("p-sid")
        return AskResult(text="answered:" + text, speak_seen=self._speak_seen)

    async def stop(self):
        pass


@pytest.fixture
def captured_speaks(monkeypatch):
    """Capture _fire_speak calls so we can assert what the daemon would have
    spoken without actually hitting an HTTP endpoint."""
    calls: list[tuple[str, str]] = []

    async def fake_fire_speak(text: str, tag: str) -> None:
        calls.append((tag, text))

    monkeypatch.setattr("vm.claude_daemon.server._fire_speak", fake_fire_speak)
    return calls


def test_ask_persists_and_returns(tmp_path, captured_speaks):
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


def test_fallback_speaks_result_when_claude_skipped_speak(tmp_path, captured_speaks):
    """If process.ask returns speak_seen=False, the daemon must speak
    result_text directly so the user isn't left with just acks + silence.
    The post-ack must be suppressed on this path (it would land on top of
    the actual answer)."""
    proc = FakeProcess(speak_seen=False)
    store = SessionStore(tmp_path / "sid")
    app = build_app(process=proc, store=store)
    with TestClient(app) as client:
        r = client.post("/ask", json={"text": "hi", "mode": "oneshot"})
        assert r.status_code == 200
        tags = [tag for tag, _ in captured_speaks]
        assert "speak-fallback" in tags
        assert "post-ack" not in tags
        fallback_text = next(text for tag, text in captured_speaks if tag == "speak-fallback")
        assert fallback_text == "answered:hi"


def test_post_ack_fires_when_claude_did_speak(tmp_path, captured_speaks):
    """Happy path: Claude called `speak` during the turn, so we fire the
    post-ack ("Processed") and skip the fallback."""
    proc = FakeProcess(speak_seen=True)
    store = SessionStore(tmp_path / "sid")
    app = build_app(process=proc, store=store)
    with TestClient(app) as client:
        client.post("/ask", json={"text": "hi", "mode": "oneshot"})
        tags = [tag for tag, _ in captured_speaks]
        assert "post-ack" in tags
        assert "speak-fallback" not in tags


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
