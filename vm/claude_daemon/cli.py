import logging
import os
import shutil
from importlib.resources import files
from pathlib import Path

import uvicorn

from .process import ClaudeProcess
from .server import build_app
from .session import SessionStore

# DEBUG-TAG: claude-daemon

log = logging.getLogger(__name__)


def _bootstrap_workspace(workdir: Path) -> None:
    """Copy the workspace template into `workdir` if it isn't already populated.

    Idempotent: only copies if `workdir / "CLAUDE.md"` doesn't exist."""
    if (workdir / "CLAUDE.md").exists():
        return
    template = files("vm.workspace_template")
    log.info("claude-daemon: bootstrapping workspace at %s", workdir)
    for src in template.iterdir():
        # Skip the package marker
        if src.name == "__init__.py":
            continue
        dst = workdir / src.name
        if src.is_dir():
            shutil.copytree(str(src), dst, dirs_exist_ok=True)
        else:
            shutil.copy(str(src), dst)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    workdir = Path(os.environ.get("VOICE_WORKSPACE", str(Path.home() / "voice-assistant")))
    workdir.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workdir)
    state_dir = Path(os.environ.get("VOICE_STATE_DIR", str(Path.home() / ".local/state/voice-assistant")))
    process = ClaudeProcess(
        workdir=workdir,
        binary=os.environ.get("VOICE_CLAUDE_BIN", "claude"),
        model=os.environ.get("VOICE_CLAUDE_MODEL", "haiku"),
        fallback_model=os.environ.get("VOICE_CLAUDE_FALLBACK_MODEL", "sonnet"),
    )
    store = SessionStore(state_dir / "session-id")
    host = os.environ.get("VOICE_CLAUDE_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_CLAUDE_PORT", "8003"))
    uvicorn.run(build_app(process, store), host=host, port=port)


if __name__ == "__main__":
    main()
