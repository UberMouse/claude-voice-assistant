"""FastAPI surface over a long-lived ClaudeProcess."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from .process import ClaudeProcess
from .session import SessionStore

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon


class AskRequest(BaseModel):
    text: str
    mode: str = "oneshot"  # "oneshot" | "conversational" — same wiring for MVP


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
        text = await process.ask(req.text, store.write)
        return {"ok": True, "result_text": text}

    return app
