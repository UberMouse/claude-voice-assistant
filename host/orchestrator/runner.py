"""Run the orchestrator: hotkey -> state machine."""
from __future__ import annotations
import asyncio
import logging
import os
from .state import OneShotMachine
from .hotkey import HotkeyDispatcher, PressKind, run_pynput
from .clients import SttHttpClient, ClaudeHttpClient, TtsHttpClient
from host.audio.capture import Recorder

log = logging.getLogger(__name__)

async def amain():
    stt_url    = os.environ.get("VOICE_STT_URL",    "http://127.0.0.1:8001")
    tts_url    = os.environ.get("VOICE_TTS_URL",    "http://127.0.0.1:8002")
    claude_url = os.environ.get("VOICE_CLAUDE_URL", "http://127.0.0.1:8003")
    hotkey     = os.environ.get("VOICE_HOTKEY",     "f8")
    mic_name   = os.environ.get("VOICE_MIC_NAME")  # substring match, Spike A

    rec = Recorder(device_name=mic_name)
    stt = SttHttpClient(stt_url)
    tts = TtsHttpClient(tts_url)
    claude = ClaudeHttpClient(claude_url)
    machine = OneShotMachine(recorder=rec, stt=stt, claude=claude, tts=tts)

    loop = asyncio.get_event_loop()
    def on_kind(kind: PressKind):
        if kind == PressKind.SHORT:
            asyncio.run_coroutine_threadsafe(_press_release_cycle(machine), loop)
        else:
            log.info("orchestrator: long-press received (conversational mode not implemented yet)")
            asyncio.run_coroutine_threadsafe(_press_release_cycle(machine), loop)

    # TODO(phase-1.5): MVP captures for a fixed VOICE_CAPTURE_SECS window after the keypress; true hold-to-talk semantics come later.
    dispatcher = HotkeyDispatcher(on_event=on_kind)
    log.info("orchestrator: listening on key %s", hotkey)
    await asyncio.to_thread(run_pynput, hotkey, dispatcher)

async def _press_release_cycle(machine: OneShotMachine):
    """For MVP we simulate a press/release tied to the HotkeyDispatcher firing post-release.
    Capture for a default window of N seconds. A future iteration moves this to true press/release semantics."""
    await machine.on_press()
    await asyncio.sleep(float(os.environ.get("VOICE_CAPTURE_SECS", "5")))
    await machine.on_release()
