# Spike D: persistent `claude --print` over stream-json — findings

**Date:** 2026-05-17
**Result:** ✅ Pass — clear winner. Use a long-lived `claude` subprocess with stream-json I/O for the daemon. Drops per-turn wall time from ~6.5–8 s to **~1.5–2.8 s**, kills the 5 s CLI cold-start tax, and surfaces subscription rate-limit state inline.

## What we ran

```
claude --print \
       --input-format=stream-json \
       --output-format=stream-json \
       --verbose \
       --session-id <uuid> \
       --replay-user-messages
```

Driver: `scripts/spike-d-claude-stream.py`. Spawns one subprocess, feeds 5 sequential user messages over stdin, reads JSON lines back from stdout until each `type=result` line. Output captured at `/tmp/spike-d/stream.jsonl` (lines tagged `>>>` for stdin, `<<<` for stdout).

## Input envelope

Worked first try:

```jsonl
{"type":"user","message":{"role":"user","content":"Remember the number 42 for me. Acknowledge briefly."}}
```

One JSON object per line, terminated by `\n`. No batching, no closing sentinel — the process generates a response and then waits for the next line.

## Latency: the headline

| Turn | Prompt | `duration_ms` (per-turn) | Result |
|------|--------|--------------------------|--------|
| 1 | "Remember 42" | **14 295** | "Saved — 42." |
| 2 | "What number?" | **2 793** | "42" |
| 3 | "Reply with just OK." | **2 709** | "OK" |
| 4 | "Reply with just OK." | **1 546** | "OK" |
| 5 | "Reply with just OK." | **1 696** | "OK" |

Compared to Spike C (`--print`-per-call):

| Approach | Cold turn | Warm turn |
|----------|-----------|-----------|
| `--print` per call (Spike C) | 19.7 s wall | 6.5–7.9 s wall |
| stream-json long-lived (this spike) | 14.3 s | **1.5–2.8 s** |

**Net win on warm turns: ~4–5 s per request, every request.** Turn 1's 14 s cold start is *paid once per daemon lifetime*, not per user prompt. With STT (~290 ms) + TTS (~1.4 s for a short reply) + audio overhead, warm round-trip from button-release to first audio drops from ~9 s to **~4 s**. That's the difference between "noticeably slow" and "feels conversational."

(`duration_api_ms` in the result events is cumulative since process start, not per-turn — confirmed by the strictly-increasing series 15695, 18477, 21182, 22724, 24416. Use `duration_ms` for per-turn timing.)

## Stream shape (each turn)

Cold turn 1:
```
system/init  → rate_limit_event  → user (echo, from --replay-user-messages)
             → assistant ↻ user ↻ assistant ↻ user ↻ assistant ↻ user ↻ assistant
             → result   (num_turns=4, with cumulative metrics)
```

Subsequent warm turns:
```
system/init  → user (echo)  → assistant  → result   (num_turns=1)
```

- `system/init` fires at the start of every turn, not just the first. The `session_id` is identical across all inits within one process lifetime — safe for the daemon to consume only the first and discard the rest, **or** to use any init as the source of truth for the canonical session id.
- `assistant` event(s) carry the model's textual output as it streams; the terminal `result` event has the metrics and the consolidated `result` text.
- The terminal `result.type == "result"` line is the unambiguous end-of-turn delimiter the daemon should block on.

## Bonus finding: rate-limit state is in the stream 🎯

On turn 1 the process emits a `rate_limit_event` with the **full Pro/Max subscription rate-limit state**:

```json
{
  "type": "rate_limit_event",
  "rate_limit_info": {
    "status": "allowed",
    "resetsAt": 1778993400,
    "rateLimitType": "five_hour",
    "overageStatus": "allowed",
    "overageResetsAt": 1780272000,
    "isUsingOverage": false
  },
  ...
}
```

This is the risk we documented as "if rate limits bite, swap to Agent SDK" — we can now **monitor it inline**. The daemon can:

- Surface "approaching limit" or "switched to overage" as a `speak` warning to the user.
- Decide proactively whether to fall back to the Agent SDK path before hitting a hard 429.

Worth a Phase-2 ticket: parse `rate_limit_event`, expose it on a `/status` endpoint, and have the runtime `CLAUDE.md` mention it so voice-Claude can communicate limit state if asked.

## Session-id caveat

The driver passed `--session-id <python-generated-uuid>`, but the `system/init` event reported `62f504ae-...`, which is also where the persisted session JSONL ended up on disk. The script's printed UUID wasn't captured to file so we can't 100% confirm whether `--session-id` was honored-and-coincidentally-matched or silently replaced. Either way, **the daemon must treat `system/init.session_id` as authoritative** — read it from the stream, don't trust what was passed in.

## Implications for design

This supersedes the Spike C "use `--session-id` on first turn, `--resume` thereafter" pattern. New daemon model:

1. **One long-lived `claude` subprocess per workspace**, spawned at daemon startup.
2. Subprocess is launched with `claude --print --input-format=stream-json --output-format=stream-json --verbose`. `--session-id` can still be passed (cheap insurance), but the daemon **reads the canonical id from the first `system/init` event** and persists that to disk for cross-restart resumption.
3. Each `/ask` handler:
   - Writes one `{"type":"user","message":{"role":"user","content":<text>}}\n` to subprocess stdin.
   - Reads stdout lines until it sees `{"type":"result", ...}`.
   - Returns the `result` text (and metadata) to the host orchestrator.
4. **Crash / restart recovery**: if the subprocess dies (or daemon restarts), the daemon respawns it with `--resume <persisted-session-id>` to restore context. If `--resume` fails (Spike C: "No conversation found"), drop the persisted id, respawn fresh, persist the new id from the new init event. *Tested-and-known behavior, not speculation.*
5. **Rate-limit awareness**: drain and inspect `rate_limit_event` lines; expose them via a `/status` HTTP endpoint or push them into the orchestrator's log.
6. **CWD discipline still applies** (Spike C finding). The daemon must `chdir` into the workspace before spawning the subprocess so the session JSONL lands in a stable `~/.claude/projects/<cwd-encoded>/` path that survives daemon restarts.

## Comparison to the tmux alternative

The user's tmux send-keys idea would deliver the same latency win (a single persistent claude session, no cold-start per call). The reason we go with stream-json instead:

| | tmux send-keys | stream-json long-lived |
|--|---|---|
| Cold-start tax eliminated | yes | yes |
| Output format | TUI (ANSI, cursor positioning, redraws) — must scrape | structured JSON lines |
| End-of-turn detection | heuristic (idle-prompt detection, content stability) | explicit `type=result` line |
| Tool/permission errors | render in the TUI; need to detect & parse | structured events (`permission_denials` field already in result) |
| Rate-limit visibility | hidden in TUI text | first-class `rate_limit_event` |
| External dep | requires tmux on the path | none |
| Brittleness across claude versions | high (TUI is not a stable API) | low (stream-json is the documented machine interface) |

`tmux send-keys` is the right Plan B if the stream-json interface regresses in a future release; for now we don't pay for it.

## Open follow-ups

- **Confirm `--session-id` precedence in stream-json mode.** Run the driver once more with the generated UUID printed *into* the stream log so we can tell whether `--session-id` was honored or overridden. Affects exactly one line of daemon code (do we still bother passing it?).
- **Crash recovery test.** Kill the subprocess mid-conversation, respawn with `--resume`, verify context is back. Pencilled into Phase 1 integration tests.
- **Permission-denials path.** A real voice-assistant turn will sometimes invoke a tool that's not on the allowlist. Inspect the `result.permission_denials` field shape so the daemon can either retry with permissions or surface the denial to the user.
- **Cost accounting.** `total_cost_usd` is cumulative across the session — turn 5 reported $0.212. The daemon may want to expose per-turn cost (delta) and total since session start to the voice user.

## Driver

`scripts/spike-d-claude-stream.py` — keep this around as the reference shape for the Phase-1 daemon. Its `send()` / `read_turn()` functions are essentially what `vm/claude_daemon/server.py` will do, minus the FastAPI wrapper.
