"""STT HTTP server wrapping faster-whisper."""
from __future__ import annotations
import io
import logging
from functools import lru_cache
from fastapi import FastAPI, UploadFile, File, HTTPException
from faster_whisper import WhisperModel
import soundfile as sf
import numpy as np

log = logging.getLogger(__name__)

# DEBUG-TAG: stt-server
# To grep all STT debug logs: grep -E "stt-server" voice-assistant.log

def build_app(model_name: str = "distil-large-v3", device: str = "auto") -> FastAPI:
    app = FastAPI(title="voice-stt")

    @lru_cache(maxsize=1)
    def get_model() -> WhisperModel:
        log.info("stt-server: loading model %s on %s", model_name, device)
        # compute_type: float16 on GPU, int8 on CPU. "auto" lets ctranslate2
        # pick per the resolved device — important for the Linux dev VM where
        # device="auto" resolves to CPU but float16 would error.
        compute_type = {"cpu": "int8", "cuda": "float16", "auto": "auto"}.get(device, "float16")
        return WhisperModel(model_name, device=device, compute_type=compute_type)

    @app.get("/health")
    def health():
        return {"ok": True, "model": model_name}

    @app.post("/transcribe")
    async def transcribe(audio: UploadFile = File(...)):
        if not audio.filename.lower().endswith((".wav", ".flac", ".mp3", ".ogg")):
            raise HTTPException(400, "unsupported audio format")
        raw = await audio.read()
        try:
            data, sr = sf.read(io.BytesIO(raw))
        except Exception as e:
            raise HTTPException(400, f"could not decode audio: {e}")
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        log.debug("stt-server: transcribing %d samples @ %dHz", len(data), sr)
        segments, _ = get_model().transcribe(data, language=None, beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("stt-server: transcribed %d chars", len(text))
        return {"text": text}

    return app
