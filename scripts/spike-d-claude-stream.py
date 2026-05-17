#!/usr/bin/env python3
"""Spike D: persistent `claude --print` over stream-json.

Goal: validate that a single long-running

    claude --print \
        --input-format=stream-json \
        --output-format=stream-json \
        --verbose \
        --session-id <uuid> \
        --replay-user-messages

subprocess can take multiple user messages across its lifetime while preserving
context — avoiding the ~5s CLI cold-start tax measured in Spike C.

Run from a separate terminal in the NixOS VM (not inside another Claude Code
session). Pass an output directory as the first arg or default to /tmp/spike-d-*.

The script writes the full bidirectional stream to <outdir>/stream.jsonl
(>>> = stdin / <<< = stdout) and prints per-turn wall times to the console.

If the input envelope is wrong, claude will likely exit with a parse error on
stderr — the script prints stderr and quits, so we can iterate on the shape.
"""
from __future__ import annotations

import json
import select
import subprocess
import sys
import time
import uuid
from pathlib import Path

OUTDIR = Path(sys.argv[1] if len(sys.argv) > 1 else f"/tmp/spike-d-{int(time.time())}")
OUTDIR.mkdir(parents=True, exist_ok=True)
print(f"=== Spike D output: {OUTDIR} ===")

session_id = str(uuid.uuid4())
print(f"Session ID: {session_id}")

cmd = [
    "claude", "--print",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--verbose",
    "--session-id", session_id,
    "--replay-user-messages",
]
print("Cmd: " + " ".join(cmd) + "\n")

proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
log = (OUTDIR / "stream.jsonl").open("w")


def send(prompt: str) -> None:
    """Best-guess envelope — adjust if claude rejects it."""
    msg = {
        "type": "user",
        "message": {"role": "user", "content": prompt},
    }
    line = json.dumps(msg) + "\n"
    log.write(">>> " + line)
    log.flush()
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()


def drain_stderr_nonblocking() -> str:
    assert proc.stderr is not None
    out = []
    while True:
        r, _, _ = select.select([proc.stderr], [], [], 0)
        if not r:
            break
        chunk = proc.stderr.readline()
        if not chunk:
            break
        out.append(chunk)
    return "".join(out)


def read_turn(timeout: float = 60.0) -> list[dict]:
    """Read JSON lines from stdout until we see a `type=result` line or timeout/EOF."""
    assert proc.stdout is not None
    deadline = time.time() + timeout
    chunks: list[dict] = []
    while time.time() < deadline:
        rem = max(0.05, deadline - time.time())
        r, _, _ = select.select([proc.stdout], [], [], rem)
        if not r:
            continue
        line = proc.stdout.readline()
        if not line:
            return chunks  # EOF
        log.write("<<< " + line)
        log.flush()
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            chunks.append({"_raw": line.rstrip()})
            continue
        chunks.append(obj)
        if obj.get("type") == "result":
            return chunks
    return chunks


def turn(label: str, prompt: str) -> None:
    print(f"--- {label} ---")
    t0 = time.time()
    send(prompt)
    chunks = read_turn()
    t1 = time.time()
    types = [c.get("type") for c in chunks]
    result_obj = next((c for c in chunks if c.get("type") == "result"), None)
    result_text = result_obj.get("result") if result_obj else None
    api_ms = result_obj.get("duration_api_ms") if result_obj else None
    print(
        f"wall={t1 - t0:.2f}s api_ms={api_ms} chunks={len(chunks)} types={types}"
    )
    print(f"result: {result_text!r}")
    if proc.poll() is not None:
        err = drain_stderr_nonblocking() + (proc.stderr.read() or "")
        print(f"!! process exited rc={proc.poll()}")
        print("stderr:")
        print(err)
        log.close()
        sys.exit(1)
    err = drain_stderr_nonblocking()
    if err.strip():
        print(f"(stderr while alive): {err.strip()}")
    print()


try:
    turn("Turn 1: prime",  "Remember the number 42 for me. Acknowledge briefly.")
    turn("Turn 2: recall", "What number did I just ask you to remember? Just the digits.")
    turn("Turn 3: short reply", "Reply with just OK.")
    turn("Turn 4: short reply", "Reply with just OK.")
    turn("Turn 5: short reply", "Reply with just OK.")
finally:
    try:
        proc.stdin.close()  # type: ignore[union-attr]
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait()
    err = drain_stderr_nonblocking() + (proc.stderr.read() or "")
    if err.strip():
        print("-- final stderr --")
        print(err)
    log.close()
    print(f"=== Done. See {OUTDIR}/stream.jsonl ===")
