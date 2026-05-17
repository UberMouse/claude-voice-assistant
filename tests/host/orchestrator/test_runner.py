"""Tests for the press-release cycle. The recorder is faked here so we can
prove the wait/release path is skipped when on_press fails — important so a
missing mic doesn't make every press hang for ~30s on the VAD timeout."""
from __future__ import annotations

import asyncio
import pytest

from host.orchestrator.runner import _press_release_cycle
from host.orchestrator.state import OneShotMachine


class FakeRecorder:
    has_endpointer = False  # take the asyncio.sleep branch, not VAD wait

    def __init__(self, raise_on_start: Exception | None = None):
        self.start_calls = 0
        self.stop_calls = 0
        self.samples = b"audio"
        self._raise_on_start = raise_on_start

    def start(self) -> None:
        self.start_calls += 1
        if self._raise_on_start is not None:
            raise self._raise_on_start

    def stop(self) -> bytes:
        self.stop_calls += 1
        return self.samples


class _Stt:
    async def transcribe(self, audio): return "hello"


class _Claude:
    async def ask(self, text, mode): self.last = (text, mode)


class _Tts:
    async def health(self): return True


@pytest.mark.asyncio
async def test_press_release_cycle_skips_release_when_start_fails(monkeypatch):
    """A press with no mic should return promptly — no 30s wait, no stop()."""
    monkeypatch.setenv("VOICE_CAPTURE_SECS", "60")  # would be a long wait if reached
    rec = FakeRecorder(raise_on_start=RuntimeError("no input device"))
    m = OneShotMachine(recorder=rec, stt=_Stt(), claude=_Claude(), tts=_Tts())
    await asyncio.wait_for(_press_release_cycle(m, rec), timeout=1.0)
    assert rec.start_calls == 1
    assert rec.stop_calls == 0  # release path was skipped
    assert m.state == "idle"
