"""Persist Claude session IDs across daemon restarts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon


class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> Optional[str]:
        if not self.path.exists():
            return None
        txt = self.path.read_text().strip()
        return txt or None

    def write(self, session_id: str) -> None:
        self.path.write_text(session_id + "\n")
        log.info("claude-daemon: session id persisted: %s", session_id)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
        log.info("claude-daemon: session id cleared")
