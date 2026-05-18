"""Run the orchestrator: hotkey -> state machine."""
from __future__ import annotations
import asyncio
import logging
import os
from .state import OneShotMachine
from .hotkey import HotkeyDispatcher, PressKind, run_pynput
from .clients import SttHttpClient, ClaudeHttpClient, TtsHttpClient
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

    rec = _build_recorder(mic_name)
    stt = SttHttpClient(stt_url)
    tts = TtsHttpClient(tts_url)
    claude = ClaudeHttpClient(claude_url)
    machine = OneShotMachine(recorder=rec, stt=stt, claude=claude, tts=tts)

    loop = asyncio.get_event_loop()
    def _log_future_exc(fut) -> None:
        # Without this, exceptions in the fire-and-forget press cycle land on
        # an unawaited future and only surface as a GC-time warning.
        exc = fut.exception()
        if exc is not None:
            log.error("orchestrator: press cycle failed", exc_info=exc)

    def on_kind(kind: PressKind):
        if kind != PressKind.SHORT:
            log.info("orchestrator: long-press received (conversational mode not implemented yet)")
        fut = asyncio.run_coroutine_threadsafe(_press_release_cycle(machine, rec), loop)
        fut.add_done_callback(_log_future_exc)

    dispatcher = HotkeyDispatcher(on_event=on_kind)
    log.info("orchestrator: listening on key %s", hotkey)
    await asyncio.to_thread(run_pynput, hotkey, dispatcher)


async def _press_release_cycle(machine: OneShotMachine, rec: Recorder):
    """Hotkey press starts recording. VAD ends it on trailing silence; the max-duration
    cap (VOICE_MAX_SECS, default 30s) is enforced by the endpointer, with the
    executor wait timeout below as a belt-and-suspenders. Falls back to a
    fixed window (VOICE_CAPTURE_SECS) when VAD is disabled."""
    if not await machine.on_press():
        # Recorder didn't start (no mic, or we weren't idle). Abort the cycle
        # so we don't sit through a 30s wait on a stream that doesn't exist.
        return
    if rec.has_endpointer:
        max_secs = int(os.environ.get("VOICE_MAX_SECS", "30"))
        result = await rec.wait_for_end(timeout=max_secs + 2)
        log.info("orchestrator: end-of-utterance result=%s", result)
    else:
        await asyncio.sleep(float(os.environ.get("VOICE_CAPTURE_SECS", "5")))
    await machine.on_release()
