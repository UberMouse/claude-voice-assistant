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
#     grep -E "claude-(daemon|stream)|pre-ack|post-ack|speak-fallback"


class AskRequest(BaseModel):
    text: str
    mode: str = "oneshot"  # "oneshot" | "conversational" — same wiring for MVP


async def _fire_speak(text: str, tag: str) -> None:
    """Post `text` to the host TTS server as a fire-and-forget speak. Used
    for the pre-ack ("Processing") that fires the moment /ask arrives and
    the post-ack ("Processed") that fires when the turn completes. Both are
    belt-and-braces fallbacks: Claude is also expected to `speak` per the
    runtime CLAUDE.md, but these guarantee the user always hears *something*
    at turn start and turn end.

    `tag` is a short string used in log lines (e.g. "pre-ack", "post-ack")
    so the two are easy to distinguish in `grep -E "pre-ack|post-ack"`.
    """
    if not text:
        return
    url = os.environ.get("VOICE_TTS_URL", "http://127.0.0.1:8002")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"{url.rstrip('/')}/speak",
                              json={"text": text})
        log.info("%s: queued %r", tag, text)
    except Exception as e:
        log.warning("%s: failed (%s) — continuing without ack", tag, e)


def _pre_ack_text() -> str:
    return os.environ.get("VOICE_PRE_ACK_TEXT", "Processing")


def _post_ack_text() -> str:
    return os.environ.get("VOICE_POST_ACK_TEXT", "Processed")


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
        asyncio.create_task(_fire_speak(_pre_ack_text(), "pre-ack"))
        try:
            result = await process.ask(req.text, store.write)
            if result.needs_fallback_speak and result.text.strip():
                # Claude's final assistant message contained text that was
                # never followed by a `speak` (a Haiku failure mode: it acks,
                # does some work, then writes the answer as plain text and
                # ends the turn). The orchestrator doesn't speak result_text,
                # so without this fallback the user hears only acks. Skip the
                # post-ack on this path — speaking the answer is its own
                # end-of-turn cue, and "Processed" on top would be noise.
                asyncio.create_task(_fire_speak(result.text, "speak-fallback"))
            else:
                # Turn completed cleanly and the final user-facing content
                # went through speak — fire a post-ack as a "this turn is
                # done" cue. Skipped on the abort path below: that already
                # produces a degraded experience and a chirp would be noise.
                asyncio.create_task(_fire_speak(_post_ack_text(), "post-ack"))
            return {"ok": True, "result_text": result.text}
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
