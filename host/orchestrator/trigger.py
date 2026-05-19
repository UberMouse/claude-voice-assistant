"""HTTP trigger server: lets a VM-side keybinding (or any HTTP client on the
LAN) fire hotkey press/release events just like a real pynput keypress would.

Why: VMware grabs the keyboard when the VM window has focus, so the host
pynput listener never sees the press. Binding a key in i3 (or any VM-side
WM) to `curl -X POST http://<host>:8004/hotkey` is the simplest way to
plumb the event back to the orchestrator without running a second
keyboard listener inside the VM.

Three endpoints:

- POST /hotkey         — fires press + release back-to-back, equivalent to a
                         tap of the physical key (the trailing VAD decides
                         when capture ends).
- POST /hotkey/press   — fires only the press; useful from a WM that can also
                         emit /hotkey/release on key release for true PTT.
- POST /hotkey/release — fires only the release.
"""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import FastAPI

log = logging.getLogger(__name__)

# DEBUG-TAG: hotkey-http
# Grep: grep -E "hotkey(-http)?"


def build_trigger_app(on_press: Callable[[], None], on_release: Callable[[], None]) -> FastAPI:
    """Build a FastAPI app exposing /hotkey endpoints that drive the press
    and release callbacks. The callbacks are the same ones the pynput
    dispatcher invokes for real keypresses, so the press cycle is identical
    regardless of which input path fires it."""
    app = FastAPI(title="voice-orchestrator-trigger")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/hotkey")
    def hotkey():
        log.info("hotkey-http: tap (press+release)")
        on_press()
        on_release()
        return {"ok": True}

    @app.post("/hotkey/press")
    def hotkey_press():
        log.info("hotkey-http: press")
        on_press()
        return {"ok": True}

    @app.post("/hotkey/release")
    def hotkey_release():
        log.info("hotkey-http: release")
        on_release()
        return {"ok": True}

    return app
