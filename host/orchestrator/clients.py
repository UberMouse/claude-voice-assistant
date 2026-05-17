"""Thin HTTP clients for STT, TTS, and the Claude VM daemon."""
from __future__ import annotations
import logging
import httpx

log = logging.getLogger(__name__)

class SttHttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def transcribe(self, audio_bytes: bytes) -> str:
        r = await self._client.post(
            f"{self._base}/transcribe",
            files={"audio": ("clip.wav", audio_bytes, "audio/wav")},
        )
        r.raise_for_status()
        return r.json()["text"]

    async def aclose(self): await self._client.aclose()

class ClaudeHttpClient:
    def __init__(self, base_url: str, timeout: float = 300.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def ask(self, text: str, mode: str = "oneshot") -> None:
        r = await self._client.post(f"{self._base}/ask", json={"text": text, "mode": mode})
        r.raise_for_status()

    async def aclose(self): await self._client.aclose()

class TtsHttpClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10.0)

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{self._base}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self): await self._client.aclose()
