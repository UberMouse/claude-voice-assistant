import asyncio
import pytest
from host.orchestrator.state import OneShotMachine

class FakeRecorder:
    def __init__(self): self.started = False; self.samples = b"audio"
    def start(self): self.started = True
    def stop(self): return self.samples

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
    await m.on_press()
    assert rec.started
    await m.on_release()
    assert claude.last == ("what time is it", "oneshot")
    assert m.state == "idle"
