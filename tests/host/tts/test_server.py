from fastapi.testclient import TestClient
from host.tts.server import build_app

class FakeSynth:
    def __init__(self):
        self.calls = []
    async def play(self, text: str):
        self.calls.append(text)

def test_speak_enqueues():
    # NOTE: spec originally used `client = TestClient(app)` without a context
    # manager for the first /speak call, but FastAPI's lifespan only runs
    # inside `with TestClient(app) as ...`, so `app.state.q` was unset and
    # the endpoint 500'd. Minimal fix: wrap the first call in a context
    # manager too. See task notes — spec bug flagged.
    synth = FakeSynth()
    app = build_app(play_fn=synth.play)
    with TestClient(app) as client:
        resp = client.post("/speak", json={"text": "hello"})
        assert resp.status_code == 202
    # The TestClient drives lifespan via context manager; use it for drain:
    with TestClient(app) as c:
        c.post("/speak", json={"text": "one"})
        c.post("/speak", json={"text": "two"})
    # By the time the context manager exits, the lifespan shuts the queue down,
    # which drains pending items.
    assert "one" in synth.calls and "two" in synth.calls

def test_health():
    app = build_app(play_fn=lambda t: None)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
