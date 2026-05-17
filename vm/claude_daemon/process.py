"""Long-lived `claude --print` stream-json subprocess.

Spike-D model: one subprocess for the daemon's lifetime, JSON lines in, JSON
lines out. Each turn is serialized through an asyncio.Lock — a single voice
user is the design assumption."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon


class ClaudeProcess:
    def __init__(self, workdir: Path, binary: str = "claude",
                 turn_timeout_s: float = 120.0):
        self.workdir = Path(workdir)
        self.binary = binary
        self.turn_timeout_s = turn_timeout_s
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self.session_id: Optional[str] = None
        self.last_rate_limit: Optional[dict] = None

    async def _spawn(self, resume_id: Optional[str]) -> None:
        cmd = [self.binary, "--print",
               "--input-format", "stream-json",
               "--output-format", "stream-json",
               "--verbose"]
        if resume_id:
            cmd += ["--resume", resume_id]
        log.info("claude-daemon: spawn %s (cwd=%s)", " ".join(cmd), self.workdir)
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(self.workdir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def ensure_alive(self, resume_id: Optional[str]) -> None:
        if self._proc and self._proc.returncode is None:
            return
        await self._spawn(resume_id)
        # Spike C: --resume on a stale id errors fast with "No conversation
        # found". Watch for early exit and retry without --resume.
        if resume_id is not None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                err = (await self._proc.stderr.read()).decode(errors="replace")
                if "No conversation found" in err:
                    log.warning("claude-daemon: stale resume id, starting fresh")
                    await self._spawn(resume_id=None)
                else:
                    raise RuntimeError(f"claude exited early: {err[:200]}")
            except asyncio.TimeoutError:
                pass  # still alive — good

    async def ask(self, text: str,
                  persist_session_id: Callable[[str], None]) -> str:
        async with self._lock:
            if not (self._proc and self._proc.returncode is None):
                raise RuntimeError("claude subprocess not alive")
            envelope = json.dumps({"type": "user",
                                   "message": {"role": "user", "content": text}}) + "\n"
            self._proc.stdin.write(envelope.encode())
            await self._proc.stdin.drain()
            log.info("claude-daemon: sent user message (%d chars)", len(text))

            loop = asyncio.get_event_loop()
            deadline = loop.time() + self.turn_timeout_s
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError("turn exceeded budget")
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=remaining)
                if not line:
                    raise RuntimeError("claude closed stdout mid-turn")
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("claude-daemon: non-JSON line: %r", line[:200])
                    continue
                t = obj.get("type")
                if t == "system" and obj.get("subtype") == "init":
                    sid = obj.get("session_id")
                    if sid and sid != self.session_id:
                        self.session_id = sid
                        persist_session_id(sid)
                elif t == "rate_limit_event":
                    self.last_rate_limit = obj.get("rate_limit_info")
                    log.info("claude-daemon: rate_limit %s", self.last_rate_limit)
                elif t == "result":
                    return obj.get("result", "")

    async def stop(self) -> None:
        if not self._proc:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._proc.terminate()
            await self._proc.wait()
