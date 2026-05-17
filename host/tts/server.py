"""TTS HTTP server: Kokoro synthesis with a serialized playback queue."""
from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Awaitable, Callable
from fastapi import FastAPI
from pydantic import BaseModel
import numpy as np
import sounddevice as sd
from .queue import PlaybackQueue

log = logging.getLogger(__name__)

# DEBUG-TAG: tts-server
# Grep: grep -E "tts-(queue|server)"

class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None

def _default_kokoro_play(voice_default: str):
    from kokoro_onnx import Kokoro  # local import: heavy

    model_path = os.environ.get("VOICE_TTS_MODEL", "kokoro-v1.0.onnx")
    voices_path = os.environ.get("VOICE_TTS_VOICES", "voices-v1.0.bin")
    k = Kokoro(model_path, voices_path)

    async def play(text: str) -> None:
        log.info("tts-server: synth %r", text[:60])
        samples, sr = k.create(text, voice=voice_default, speed=1.0)
        # sounddevice's play is blocking when wait=True; run in thread
        await asyncio.to_thread(sd.play, samples, sr, blocking=True)

    return play

def build_app(play_fn: Callable[[str], Awaitable[None]] | None = None,
              voice_default: str = "af_sarah") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        fn = play_fn or _default_kokoro_play(voice_default)
        q = PlaybackQueue(fn)
        await q.start()
        app.state.q = q
        try:
            yield
        finally:
            await q.drain()
            await q.stop()

    app = FastAPI(title="voice-tts", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/speak", status_code=202)
    async def speak(req: SpeakRequest):
        await app.state.q.enqueue(req.text)
        return {"queued": True}

    return app
