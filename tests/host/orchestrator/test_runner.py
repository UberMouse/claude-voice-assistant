"""Tests for the press-release cycle. The recorder is faked here so we can
prove the wait/release path is skipped when on_press fails — important so a
missing mic doesn't make every press hang for ~30s on the VAD timeout."""
from __future__ import annotations

import asyncio
import threading

import pytest

from host.orchestrator.runner import _press_release_cycle
from host.orchestrator.state import OneShotMachine


class FakeRecorder:
    has_endpointer = False  # take the asyncio.sleep branch, not VAD wait

    def __init__(self, raise_on_start: Exception | None = None):
        self.start_calls = 0
        self.stop_calls = 0
        self.begin_endpointing_calls = 0
        self.samples = b"audio"
        self._raise_on_start = raise_on_start

    def start(self) -> None:
        self.start_calls += 1
        if self._raise_on_start is not None:
            raise self._raise_on_start

    def stop(self) -> bytes:
        self.stop_calls += 1
        return self.samples

    def begin_endpointing(self) -> None:
        self.begin_endpointing_calls += 1


class VadRecorder(FakeRecorder):
    """Recorder that pretends VAD is wired up; wait_for_end returns
    immediately so the test doesn't actually wait."""
    has_endpointer = True

    async def wait_for_end(self, *, timeout: float):
        return None


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
    release = threading.Event()
    release.set()  # not used in fixed-window mode but harmless
    await asyncio.wait_for(_press_release_cycle(m, rec, release), timeout=1.0)
    assert rec.start_calls == 1
    assert rec.stop_calls == 0  # release path was skipped
    assert m.state == "idle"


@pytest.mark.asyncio
async def test_press_release_cycle_waits_for_release_then_endpoints():
    """With VAD attached, the cycle must wait on release_event before calling
    begin_endpointing(). Pre-setting the event lets the cycle proceed
    deterministically in the test."""
    rec = VadRecorder()
    m = OneShotMachine(recorder=rec, stt=_Stt(), claude=_Claude(), tts=_Tts())
    release = threading.Event()
    release.set()  # simulate the user already released
    await asyncio.wait_for(_press_release_cycle(m, rec, release), timeout=1.0)
    assert rec.start_calls == 1
    assert rec.begin_endpointing_calls == 1
    assert rec.stop_calls == 1
    assert m.state == "idle"


@pytest.mark.asyncio
async def test_press_release_cycle_does_not_begin_endpointing_before_release(monkeypatch):
    """If release hasn't fired, the cycle should still be waiting on
    release_event — begin_endpointing() must not have been called yet."""
    monkeypatch.setenv("VOICE_MAX_HOLD_SECS", "10")
    rec = VadRecorder()
    m = OneShotMachine(recorder=rec, stt=_Stt(), claude=_Claude(), tts=_Tts())
    release = threading.Event()  # never set
    cycle = asyncio.create_task(_press_release_cycle(m, rec, release))
    # Give the cycle time to start the recorder and reach the wait.
    await asyncio.sleep(0.1)
    assert rec.start_calls == 1
    assert rec.begin_endpointing_calls == 0
    # Now release — cycle should complete.
    release.set()
    await asyncio.wait_for(cycle, timeout=1.0)
    assert rec.begin_endpointing_calls == 1
    assert m.state == "idle"
