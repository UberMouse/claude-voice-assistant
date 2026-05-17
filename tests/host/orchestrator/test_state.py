import asyncio
import pytest
from host.orchestrator.state import OneShotMachine

class FakeRecorder:
    def __init__(self, raise_on_start: Exception | None = None):
        self.started = False
        self.start_calls = 0
        self.stop_calls = 0
        self.samples = b"audio"
        self._raise_on_start = raise_on_start
    def start(self):
        self.start_calls += 1
        if self._raise_on_start is not None:
            raise self._raise_on_start
        self.started = True
    def stop(self):
        self.stop_calls += 1
        return self.samples

class FakeSttClient:
    async def transcribe(self, audio_bytes): return "what time is it"

class FakeClaudeClient:
    async def ask(self, text, mode): self.last = (text, mode); return None

class FakeTtsClient:
    async def health(self): return True

@pytest.mark.asyncio
async def test_oneshot_happy_path():
    rec, stt, claude, tts = FakeRecorder(), FakeSttClient(), FakeClaudeClient(), FakeTtsClient()
    m = OneShotMachine(recorder=rec, stt=stt, claude=claude, tts=tts)
    ok = await m.on_press()
    assert ok is True
    assert rec.started
    await m.on_release()
    assert claude.last == ("what time is it", "oneshot")
    assert m.state == "idle"


@pytest.mark.asyncio
async def test_on_press_recorder_failure_stays_idle():
    """If the mic is missing, recorder.start() raises (PortAudioError). The
    state machine must NOT transition to 'recording' or it'll silently drop
    every subsequent press."""
    rec = FakeRecorder(raise_on_start=RuntimeError("no input device"))
    m = OneShotMachine(recorder=rec, stt=FakeSttClient(), claude=FakeClaudeClient(), tts=FakeTtsClient())
    ok = await m.on_press()
    assert ok is False
    assert m.state == "idle"


@pytest.mark.asyncio
async def test_press_after_failed_press_still_works():
    """Regression: a failed first press used to wedge the state machine in
    'recording', dropping every later press. After the fix, a press following
    a failure should still start the recorder when the mic comes back."""
    rec = FakeRecorder(raise_on_start=RuntimeError("no input device"))
    m = OneShotMachine(recorder=rec, stt=FakeSttClient(), claude=FakeClaudeClient(), tts=FakeTtsClient())
    assert await m.on_press() is False
    # Mic comes back online.
    rec._raise_on_start = None
    ok = await m.on_press()
    assert ok is True
    assert rec.started
    assert m.state == "recording"


@pytest.mark.asyncio
async def test_on_release_noop_when_not_recording():
    """on_release after a failed on_press must not touch the recorder."""
    rec = FakeRecorder(raise_on_start=RuntimeError("no input device"))
    m = OneShotMachine(recorder=rec, stt=FakeSttClient(), claude=FakeClaudeClient(), tts=FakeTtsClient())
    await m.on_press()
    await m.on_release()
    assert rec.stop_calls == 0
    assert m.state == "idle"
