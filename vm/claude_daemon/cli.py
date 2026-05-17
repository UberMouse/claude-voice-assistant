import logging
import os
from pathlib import Path

import uvicorn

from .process import ClaudeProcess
from .server import build_app
from .session import SessionStore

# DEBUG-TAG: claude-daemon


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    workdir = Path(os.environ.get("VOICE_WORKSPACE", str(Path.home() / "voice-assistant")))
    workdir.mkdir(parents=True, exist_ok=True)
    state_dir = Path(os.environ.get("VOICE_STATE_DIR", str(Path.home() / ".local/state/voice-assistant")))
    process = ClaudeProcess(workdir=workdir, binary=os.environ.get("VOICE_CLAUDE_BIN", "claude"))
    store = SessionStore(state_dir / "session-id")
    host = os.environ.get("VOICE_CLAUDE_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_CLAUDE_PORT", "8003"))
    uvicorn.run(build_app(process, store), host=host, port=port)
