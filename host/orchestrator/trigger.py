"""HTTP trigger server: lets a VM-side keybinding (or any HTTP client on the
LAN) fire a hotkey press just like a real pynput keypress would.

Why: VMware grabs the keyboard when the VM window has focus, so the host
pynput listener never sees the press. Binding a key in i3 (or any VM-side
WM) to `curl -X POST http://<host>:8004/hotkey` is the simplest way to
plumb the event back to the orchestrator without running a second
keyboard listener inside the VM.
"""
from __future__ import annotations

import logging
from typing import Callable, Literal, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .hotkey import PressKind

log = logging.getLogger(__name__)

# DEBUG-TAG: hotkey-http
# Grep: grep -E "hotkey(-http)?"


class HotkeyRequest(BaseModel):
    kind: Literal["short", "long"] = "short"


def build_trigger_app(on_kind: Callable[[PressKind], None]) -> FastAPI:
    """Build a FastAPI app exposing POST /hotkey that calls `on_kind` with
    the requested press kind. `on_kind` is the same callback the pynput
    dispatcher invokes for real keypresses, so the press cycle is identical
    regardless of which input path fires it."""
    app = FastAPI(title="voice-orchestrator-trigger")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/hotkey")
    def hotkey(req: Optional[HotkeyRequest] = None):
        # Empty-body POSTs are convenient from `curl -X POST <url>` with no
        # flags. Default to short press in that case.
        kind = PressKind((req.kind if req else "short"))
        log.info("hotkey-http: received %s from HTTP trigger", kind)
        on_kind(kind)
        return {"ok": True, "kind": kind.value}

    return app
