"""FastAPI surface over a long-lived ClaudeProcess."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from .process import ClaudeProcess
from .session import SessionStore

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon
# Grep all daemon debug logging with:
#     grep -E "claude-(daemon|stream)|pre-ack"


class AskRequest(BaseModel):
    text: str
    mode: str = "oneshot"  # "oneshot" | "conversational" — same wiring for MVP


async def _fire_pre_ack() -> None:
    """Speak a canned ack the moment /ask arrives, so the user hears something
    while Claude does its first reads. Claude's own ack (per runtime CLAUDE.md)
    still happens — this is a belt-and-braces fallback that fires unconditionally.

    Disable by setting VOICE_PRE_ACK_TEXT="" (empty). Override text via the same
    env var. Failures are logged and swallowed — pre-ack must never block /ask.
    """
    text = os.environ.get("VOICE_PRE_ACK_TEXT", "Processing")
    if not text:
        return
    url = os.environ.get("VOICE_TTS_URL", "http://127.0.0.1:8002")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"{url.rstrip('/')}/speak",
                              json={"text": text})
        log.info("pre-ack: queued %r", text)
    except Exception as e:
        log.warning("pre-ack: failed (%s) — continuing without ack", e)


def build_app(process: ClaudeProcess, store: SessionStore) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await process.ensure_alive(resume_id=store.read())
        yield
        await process.stop()

    app = FastAPI(title="voice-claude-daemon", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"ok": True, "session_id": process.session_id}

    @app.get("/status")
    def status():
        return {"session_id": process.session_id,
                "rate_limit": process.last_rate_limit}

    @app.post("/ask")
    async def ask(req: AskRequest):
        # Fire-and-forget: don't await — we want this happening on the wire
        # in parallel with claude's first tokens.
        asyncio.create_task(_fire_pre_ack())
        try:
            text = await process.ask(req.text, store.write)
            return {"ok": True, "result_text": text}
        except (asyncio.TimeoutError, RuntimeError) as e:
            # process.ask already killed the subprocess; respawn here so the
            # next /ask doesn't have to. Return 200 with degraded payload —
            # the orchestrator only cares about HTTP status, and a 5xx would
            # leak its state machine into a stuck "awaiting_claude" state.
            log.warning("claude-daemon: /ask aborted (%s) — respawning", e)
            await process.ensure_alive(resume_id=store.read())
            return {"ok": False, "error": "turn_aborted",
                    "detail": str(e), "result_text": ""}

    return app
