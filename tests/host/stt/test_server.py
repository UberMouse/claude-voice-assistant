from pathlib import Path
from fastapi.testclient import TestClient
from host.stt.server import build_app

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "hello.wav"


def test_transcribe_returns_text():
    app = build_app(model_name="tiny.en")  # smallest, fastest model for tests
    client = TestClient(app)
    with FIXTURE.open("rb") as f:
        resp = client.post("/transcribe", files={"audio": ("hello.wav", f, "audio/wav")})
    assert resp.status_code == 200
    body = resp.json()
    assert "text" in body
    assert "hello" in body["text"].lower()


def test_health():
    app = build_app(model_name="tiny.en")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
