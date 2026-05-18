"""Long-lived `claude --print` stream-json subprocess.

Spike-D model: one subprocess for the daemon's lifetime, JSON lines in, JSON
lines out. Each turn is serialized through an asyncio.Lock — a single voice
user is the design assumption."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon


@dataclass
class AskResult:
    """Result of a single /ask turn.

    `needs_fallback_speak` is True when Claude likely never spoke the actual
    answer during the turn — meaning the user heard only the daemon acks and
    needs us to speak `text` directly. Heuristic: was there a "substantial"
    `speak` (command length above the ack threshold)? If yes, the user
    probably heard a real answer; if no, fall back.

    Two prior heuristics failed:
      - "any speak happened" → the opening ack-speak tripped it, so missing
        closing speaks slipped through and the user heard only the ack.
      - "text after the last speak" → fired whenever Claude double-emitted
        (speak the answer, then also write it as text), duplicating audio.

    Substantial-speak captures "did the user hear something longer than an
    ack?" which is the property we actually care about. False positives
    (Claude does only a substantial-but-not-the-answer speak) and false
    negatives (Claude speaks a one-word answer like "Yes." and writes more
    in text) both exist but are uncommon — the alternative of duplicating
    or missing the answer entirely is worse.
    """
    text: str
    needs_fallback_speak: bool


# A `speak ...` Bash command this long or longer is considered "substantial"
# — i.e. it's almost certainly the answer rather than an ack. Acks per the
# runtime CLAUDE.md are short ("On it.", "Looking that up.", "One sec.") and
# all land below ~35 chars including the `speak "` wrapping. The threshold
# is intentionally a bit higher than the longest common ack to leave room
# for variants without false-suppressing the fallback.
_SUBSTANTIAL_SPEAK_CMD_LEN = 50


class ClaudeProcess:
    def __init__(self, workdir: Path, binary: str = "claude",
                 turn_timeout_s: float = 120.0,
                 model: Optional[str] = None,
                 fallback_model: Optional[str] = None):
        self.workdir = Path(workdir)
        self.binary = binary
        self.turn_timeout_s = turn_timeout_s
        # Model for the main thread. Subagents pick their own via the Task
        # tool, guided by the runtime CLAUDE.md. Haiku is the right default
        # for the main thread: it mostly orchestrates `speak` + Task dispatch
        # and benefits from low latency far more than from raw capability.
        self.model = model
        self.fallback_model = fallback_model
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self.session_id: Optional[str] = None
        self.last_rate_limit: Optional[dict] = None

    async def _spawn(self, resume_id: Optional[str]) -> None:
        cmd = [self.binary, "--print",
               "--input-format", "stream-json",
               "--output-format", "stream-json",
               "--verbose"]
        if self.model:
            cmd += ["--model", self.model]
        if self.fallback_model:
            cmd += ["--fallback-model", self.fallback_model]
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
                  persist_session_id: Callable[[str], None]) -> AskResult:
        async with self._lock:
            if not (self._proc and self._proc.returncode is None):
                raise RuntimeError("claude subprocess not alive")
            envelope = json.dumps({"type": "user",
                                   "message": {"role": "user", "content": text}}) + "\n"
            self._proc.stdin.write(envelope.encode())
            await self._proc.stdin.drain()
            log.info("claude-daemon: sent user message (%d chars)", len(text))

            # Longest `speak ...` Bash command observed this turn. At
            # result-time, if no speak crossed the substantial threshold
            # (i.e. all speaks were ack-length), the user probably never
            # heard the answer and the daemon falls back to speaking
            # result.text directly.
            max_speak_cmd_len = 0
            loop = asyncio.get_event_loop()
            deadline = loop.time() + self.turn_timeout_s
            try:
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
                        log.info("claude-daemon: stream non-JSON: %r", line[:200])
                        continue
                    _log_stream_event(obj)
                    new_max = _max_speak_cmd_len_in(obj, max_speak_cmd_len)
                    if new_max > max_speak_cmd_len:
                        max_speak_cmd_len = new_max
                        log.info("claude-daemon: max_speak_cmd_len=%d (substantial=%s)",
                                 max_speak_cmd_len,
                                 max_speak_cmd_len >= _SUBSTANTIAL_SPEAK_CMD_LEN)
                    t = obj.get("type")
                    if t == "system" and obj.get("subtype") == "init":
                        sid = obj.get("session_id")
                        if sid and sid != self.session_id:
                            self.session_id = sid
                            persist_session_id(sid)
                    elif t == "rate_limit_event":
                        self.last_rate_limit = obj.get("rate_limit_info")
                    elif t == "result":
                        needs_fallback = (
                            max_speak_cmd_len < _SUBSTANTIAL_SPEAK_CMD_LEN)
                        return AskResult(
                            text=obj.get("result", "") or "",
                            needs_fallback_speak=needs_fallback)
            except (asyncio.TimeoutError, RuntimeError):
                # Subprocess is now in an indeterminate state: the model may
                # still be mid-tool-call with pending stdout. We can't safely
                # send another user message into the same stdin. Hard-kill so
                # the next ensure_alive() spawns a fresh process (which will
                # --resume the same session).
                log.warning("claude-daemon: turn aborted — killing subprocess for recycle")
                await self._kill()
                raise

    async def _kill(self) -> None:
        """Hard-kill the subprocess and forget it. Caller is responsible for
        spawning a replacement via ensure_alive()."""
        if not self._proc:
            return
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            log.warning("claude-daemon: subprocess didn't die within 2s of kill")
        self._proc = None

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


def _truncate(s: str, n: int = 240) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "..."


# Matches a Bash command that runs the `speak` CLI: leading whitespace, the
# word `speak`, then a word boundary (space, end of string, or quote). Avoids
# false positives like `speakers` or `speak-cli` (a logging tag, not a binary).
_SPEAK_CMD_RE = re.compile(r"^\s*speak(\s|$|[\"'])")


def _speak_cmd_len(block: dict) -> int:
    """Length of the Bash `speak ...` command in `block`, or 0 if `block`
    isn't a speak invocation. The runtime exposes `speak` as a shell command
    via Bash, so every speak shows up as
    `tool_use name="Bash" input.command="speak ..."`."""
    if not isinstance(block, dict):
        return 0
    if block.get("type") != "tool_use" or block.get("name") != "Bash":
        return 0
    cmd = (block.get("input") or {}).get("command", "")
    if not isinstance(cmd, str) or not _SPEAK_CMD_RE.match(cmd):
        return 0
    return len(cmd)


def _max_speak_cmd_len_in(obj: dict, current_max: int) -> int:
    """Returns max(current_max, longest speak command in this event).

    Non-assistant events pass through with current_max unchanged. An
    assistant event's content list is walked for `speak ...` Bash tool_uses.
    """
    if obj.get("type") != "assistant":
        return current_max
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return current_max
    best = current_max
    for block in content:
        n = _speak_cmd_len(block)
        if n > best:
            best = n
    return best


def _log_stream_event(obj: dict) -> None:
    """Dump each stream-json event from `claude --print` for debugging.

    DEBUG-TAG: claude-stream
    Grep all debug logging added by this function with:
        grep -E "claude-(daemon|stream)"
    """
    t = obj.get("type")
    if t == "system":
        sub = obj.get("subtype")
        if sub == "init":
            log.info("claude-stream: system/init session=%s tools=%s cwd=%s",
                     obj.get("session_id"), obj.get("tools"), obj.get("cwd"))
        else:
            log.info("claude-stream: system/%s %s", sub, _truncate(json.dumps(obj), 200))
    elif t == "assistant":
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str):
            log.info("claude-stream: assistant.text %s", _truncate(content))
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    log.info("claude-stream: assistant.text %s", _truncate(block.get("text", "")))
                elif bt == "tool_use":
                    log.info("claude-stream: assistant.tool_use name=%s id=%s input=%s",
                             block.get("name"), block.get("id"),
                             _truncate(json.dumps(block.get("input", {})), 200))
                else:
                    log.info("claude-stream: assistant.%s %s", bt, _truncate(json.dumps(block), 160))
    elif t == "user":
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    log.info("claude-stream: tool_result id=%s is_error=%s content=%s",
                             block.get("tool_use_id"), block.get("is_error"),
                             _truncate(json.dumps(block.get("content")), 240))
    elif t == "rate_limit_event":
        log.info("claude-stream: rate_limit %s", obj.get("rate_limit_info"))
    elif t == "result":
        log.info("claude-stream: result subtype=%s is_error=%s text=%s",
                 obj.get("subtype"), obj.get("is_error"),
                 _truncate(obj.get("result", "") or ""))
    else:
        log.info("claude-stream: unknown type=%s %s", t, _truncate(json.dumps(obj), 200))


