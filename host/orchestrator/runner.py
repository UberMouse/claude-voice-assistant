"""Run the orchestrator: hotkey -> state machine."""
from __future__ import annotations
import asyncio
import logging
import os
import threading

import uvicorn

from .state import OneShotMachine
from .hotkey import HotkeyDispatcher, run_pynput
from .clients import SttHttpClient, ClaudeHttpClient, TtsHttpClient
from .trigger import build_trigger_app
from host.audio.capture import Recorder
from host.audio.vad import Endpointer, SileroVadModel

log = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _build_recorder(mic_name: str | None) -> Recorder:
    """VAD endpointing is on by default; set VOICE_VAD=0 to fall back to
    fixed-window capture."""
    if not _bool_env("VOICE_VAD", True):
        log.info("orchestrator: VAD disabled, using fixed-window capture")
        return Recorder(device_name=mic_name)
    endpointer = Endpointer(
        speech_threshold=float(os.environ.get("VOICE_VAD_THRESHOLD", "0.5")),
        silence_ms=int(os.environ.get("VOICE_SILENCE_MS", "800")),
        max_ms=int(os.environ.get("VOICE_MAX_SECS", "30")) * 1000,
        no_speech_ms=int(os.environ.get("VOICE_NO_SPEECH_SECS", "3")) * 1000,
    )
    return Recorder(device_name=mic_name, endpointer=endpointer, vad_scorer=SileroVadModel())


async def amain():
    stt_url    = os.environ.get("VOICE_STT_URL",    "http://127.0.0.1:8001")
    tts_url    = os.environ.get("VOICE_TTS_URL",    "http://127.0.0.1:8002")
    claude_url = os.environ.get("VOICE_CLAUDE_URL", "http://127.0.0.1:8003")
    hotkey     = os.environ.get("VOICE_HOTKEY",     "lshift+f3")
    mic_name   = os.environ.get("VOICE_MIC_NAME")  # substring match, Spike A
    trigger_host = os.environ.get("VOICE_TRIGGER_HOST", "0.0.0.0")
    trigger_port = int(os.environ.get("VOICE_TRIGGER_PORT", "8004"))

    rec = _build_recorder(mic_name)
    stt = SttHttpClient(stt_url)
    tts = TtsHttpClient(tts_url)
    claude = ClaudeHttpClient(claude_url)
    machine = OneShotMachine(recorder=rec, stt=stt, claude=claude, tts=tts)

    loop = asyncio.get_event_loop()

    # The press cycle is split across two callbacks (press, release) so we need
    # to bridge them. release_event is cleared on press and set on release; the
    # cycle coroutine awaits it before activating VAD. threading.Event is used
    # (rather than asyncio.Event) because the callbacks fire from pynput's
    # thread; bridging happens via run_in_executor.
    release_event = threading.Event()

    def _log_future_exc(fut) -> None:
        # Without this, exceptions in the fire-and-forget press cycle land on
        # an unawaited future and only surface as a GC-time warning.
        exc = fut.exception()
        if exc is not None:
            log.error("orchestrator: press cycle failed", exc_info=exc)

    def on_press_cb():
        # Clear before scheduling so the cycle's wait genuinely blocks until
        # the matching release fires. on_press_cb and on_release_cb are
        # always paired sequentially per the dispatcher's lock.
        release_event.clear()
        fut = asyncio.run_coroutine_threadsafe(_press_release_cycle(machine, rec, release_event), loop)
        fut.add_done_callback(_log_future_exc)

    def on_release_cb():
        release_event.set()

    dispatcher = HotkeyDispatcher(on_press=on_press_cb, on_release=on_release_cb)
    log.info("orchestrator: listening on key %s", hotkey)

    trigger_app = build_trigger_app(on_press=on_press_cb, on_release=on_release_cb)
    trigger_config = uvicorn.Config(
        trigger_app, host=trigger_host, port=trigger_port,
        log_level="info", access_log=False,
    )
    trigger_server = uvicorn.Server(trigger_config)
    log.info("orchestrator: HTTP trigger listening on %s:%d", trigger_host, trigger_port)

    # Pynput blocks forever in a thread; uvicorn runs in the event loop.
    # Either exiting takes the whole orchestrator down.
    await asyncio.gather(
        trigger_server.serve(),
        asyncio.to_thread(run_pynput, hotkey, dispatcher),
    )


async def _press_release_cycle(machine: OneShotMachine, rec: Recorder, release_event: threading.Event):
    """Push-to-talk cycle.

    Press starts the recorder; we then wait for release before activating
    VAD endpointing so the user can hold the button as long as they like
    without the silence timer firing on a pause. After release, VAD trails
    the user's speech and fires once they stop. The max-duration cap
    (VOICE_MAX_SECS, default 30s) is measured from release time and enforced
    by the endpointer, with the executor wait timeout below as
    belt-and-suspenders. Falls back to a fixed window (VOICE_CAPTURE_SECS)
    when VAD is disabled."""
    if not await machine.on_press():
        # Recorder didn't start (no mic, or we weren't idle). Abort the cycle
        # so we don't wait forever on a release that won't translate to
        # anything useful.
        return
    loop = asyncio.get_event_loop()
    if rec.has_endpointer:
        max_secs = int(os.environ.get("VOICE_MAX_SECS", "30"))
        hold_timeout = float(os.environ.get("VOICE_MAX_HOLD_SECS", "300"))
        # Wait for the user to release the hotkey. Bounded by hold_timeout as
        # a safety net so a stuck/forgotten release doesn't pin the stream.
        released = await loop.run_in_executor(None, release_event.wait, hold_timeout)
        if not released:
            log.warning("orchestrator: hold timeout (%ss) without release, forcing endpointing", hold_timeout)
        rec.begin_endpointing()
        result = await rec.wait_for_end(timeout=max_secs + 2)
        log.info("orchestrator: end-of-utterance result=%s", result)
    else:
        # Fixed-window mode is one-shot: release is ignored, we just sleep.
        await asyncio.sleep(float(os.environ.get("VOICE_CAPTURE_SECS", "5")))
    await machine.on_release()
