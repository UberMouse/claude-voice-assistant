"""One-shot orchestrator state machine."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Protocol, Literal

log = logging.getLogger(__name__)

# DEBUG-TAG: orchestrator
# Grep: grep -E "orchestrator"

class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> "object": ...  # returns raw samples or bytes

class SttClient(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> str: ...

class ClaudeClient(Protocol):
    async def ask(self, text: str, mode: Literal["oneshot", "conversational"]) -> None: ...

class TtsClient(Protocol):
    async def health(self) -> bool: ...

State = Literal["idle", "recording", "transcribing", "awaiting_claude"]

@dataclass
class OneShotMachine:
    recorder: Recorder
    stt: SttClient
    claude: ClaudeClient
    tts: TtsClient
    state: State = "idle"

    async def on_press(self) -> bool:
        """Start recording. Returns True if recording is now in progress; False
        if the press was ignored (not idle) or the recorder failed to start
        (e.g. no mic plugged in). On failure we stay in ``idle`` so the next
        press isn't dropped by the guard above."""
        if self.state != "idle":
            log.warning("orchestrator: press ignored in state %s", self.state)
            return False
        try:
            self.recorder.start()
        except Exception:
            log.exception("orchestrator: recorder failed to start; staying idle")
            return False
        self.state = "recording"
        log.info("orchestrator: -> recording")
        return True

    async def on_release(self) -> None:
        if self.state != "recording":
            return
        audio = self.recorder.stop()
        # FakeRecorder may return bytes; real recorder returns numpy. Caller adapts.
        audio_bytes = audio if isinstance(audio, (bytes, bytearray)) else _encode(audio)
        self.state = "transcribing"
        log.info("orchestrator: -> transcribing (%d bytes)", len(audio_bytes))
        text = await self.stt.transcribe(audio_bytes)
        log.info("orchestrator: transcript=%r", text[:80])
        if not text.strip():
            log.info("orchestrator: empty transcript, back to idle")
            self.state = "idle"
            return
        self.state = "awaiting_claude"
        await self.claude.ask(text, mode="oneshot")
        self.state = "idle"
        log.info("orchestrator: -> idle")

def _encode(samples) -> bytes:
    # Lazy import to keep state.py thin
    from host.audio.capture import encode_wav
    return encode_wav(samples)
