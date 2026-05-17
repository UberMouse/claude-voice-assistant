# Spike C: `claude --print --resume` for the VM-side daemon — findings

**Date:** 2026-05-17
**Result:** ✅ Pass with one important constraint. Session continuity works; `--session-id` pre-seeding works; the daemon design needs a small tweak (use `--session-id` on first turn, `--resume` thereafter).

## What we tested

Driver script: `scripts/spike-c-claude-print.sh`. Run from a fresh terminal in the NixOS VM (not from inside an existing Claude Code session, to avoid cache / auth interference). Output captured under `/tmp/spike-c/`.

`claude` version under test: `2.1.141`.

## Findings

### 1. Session pinning via `--session-id` works ✅

`claude --print --session-id <our-uuid> --output-format json '...'` accepts the caller-supplied UUID and the same UUID comes back in the result JSON's `session_id` field. **We do not need to scrape the UUID from output** — the daemon picks its own UUID on first turn and is then free to `--resume` it.

```
$ uuid=13f2d6b0-203c-4dd5-8bb4-555dbd411f45
$ claude --print --session-id $uuid --output-format json 'Remember 42'
{... "session_id":"13f2d6b0-203c-4dd5-8bb4-555dbd411f45", "result":"Got it — 42 saved to memory." ...}
```

### 2. `--resume` round-trips context cleanly ✅

```
$ claude --print --resume $uuid --output-format json 'What number?'
{... "result":"42", "duration_api_ms":1606, "session_id":"13f2d6b0-..." ...}
```

### 3. 10-call burst: no rate-limit pain, no latency drift ✅

| Iter | Wall (s) | API (ms) | Cost (USD) |
|------|----------|----------|------------|
| 1 | 6.76 | 1539 | 0.0152 |
| 2 | 7.04 | 1443 | 0.0152 |
| 3 | 6.64 | 1517 | 0.0152 |
| 4 | 7.23 | 1938 | 0.0153 |
| 5 | 7.86 | 2198 | 0.0153 |
| 6 | 7.12 | 1571 | 0.0161 |
| 7 | 6.79 | 1666 | 0.0154 |
| 8 | 6.56 | 1386 | 0.0154 |
| 9 | 7.15 | 1627 | 0.0154 |
| 10 | 6.63 | 1544 | 0.0154 |

All replied "OK" as instructed, rc=0 across the board. No 429s, no warnings. **No subscription-plan ceiling visible at this depth** — but ten short prompts is a small test; the documented risk (`claude --print` rate limits under heavier load) is not retired by this spike, just not seen.

### 4. Wall-vs-API gap: ~5s CLI startup tax 🔴

Every call shows roughly **5 seconds of wall time on top of `duration_api_ms`** — Node.js startup, auth check, plugin sync, settings load, auto-memory bootstrap, etc. This is the latency floor for the voice assistant *per Claude invocation*.

End-to-end latency budget estimate (one-shot mode):

| Stage | Time |
|-------|------|
| Mic capture + endpointing | 0–200 ms after release |
| STT (Spike B) | ~290 ms |
| HTTP host→VM | <50 ms (loopback-equivalent across vmnet) |
| `claude --print` cold start | ~5 s |
| Claude API turn | 1.4–2.2 s |
| `speak` HTTP back to host | <50 ms |
| TTS synth (Spike B, CPU) | ~1.4 s for a 5-s reply (first chunk earlier in principle but kokoro_onnx 0.5 is non-streaming) |
| Audio playback start | immediate |

Round-trip ~7.5–9 s from button release to first sound. **Acceptable for one-shot. Sluggish for conversational mode.**

**Mitigation options (deferred to Phase 2):**
1. `--bare` flag — explicitly skips hooks, LSP, plugin sync, auto-memory, keychain, CLAUDE.md auto-discovery, etc. Likely shaves a noticeable chunk off the 5 s. Not validated in this spike; worth a follow-up A/B run before Phase 1 freezes the daemon.
2. Long-lived Python daemon using the **Claude Agent SDK** instead of shelling out to `claude --print`. Eliminates CLI startup entirely; turns become pure API latency (1.5–2 s). This was already the documented fallback for the rate-limit risk and is now the documented fallback for latency too.
3. Pre-warm a backgrounded `claude --print` waiting on stdin (interactive mode style). Doable but more complex than (2).

### 5. Unknown session ID with `--resume` is a hard error 🔴

```
$ claude --print --resume <never-used-uuid> --output-format json 'Hello'
[exit 1] stderr: No conversation found with session ID: 499747ea-5564-432d-baad-841cae23ba1d
```

**Implication for daemon design:** the daemon **cannot** lazily mint a UUID and pass `--resume` on every call. The protocol is:

- First turn ever (or after a session rotation) → `claude --print --session-id <new-uuid> ...`. Persist that UUID.
- Every subsequent turn → `claude --print --resume <persisted-uuid> ...`.
- Recovery: if `--resume` fails with "No conversation found" (e.g. session JSONL was deleted), the daemon should regenerate a UUID and use `--session-id` again.

### 6. Session JSONL location depends on CWD 🔴

After the run, the session lived at:

```
/home/taylorl/.claude/projects/-tmp-spike-c/13f2d6b0-...jsonl
/home/taylorl/.claude/session-env/13f2d6b0-...
```

The `projects/` subdir is the *current working directory at invocation time*, with `/` replaced by `-`. Because we ran the spike from `/tmp/spike-c/`, it landed under `projects/-tmp-spike-c/`. If you re-invoke from a different cwd, `--resume <same-uuid>` will fail because the loader searches `projects/<cwd-encoded>/`.

**Implication for daemon design:** the VM-side `claude_daemon` **must `chdir` into the workspace directory (`~/voice-assistant/`) before every `claude --print` exec**. Otherwise sessions written from one cwd won't be resumable from another. The workspace is also where `CLAUDE.md` and settings live, so this aligns naturally with the design — just needs to be a hard rule in the daemon code, not an accident of where the systemd unit happens to be launched.

### 7. `--output-format=stream-json` requires `--verbose` 📝

```
$ claude --print --output-format stream-json --include-partial-messages ...
Error: When using --print, --output-format=stream-json requires --verbose
```

Deferred. We only need streaming output if we add streaming TTS (kokoro_onnx 0.5 doesn't stream anyway, so this is a Phase-2+ concern).

### 8. Pricing data point (informational)

- First call (cold, loads auto-memory + project context): $0.24 (29 579 cache_creation tokens for memory/skills).
- Resumed call: $0.08 → drops to ~$0.015 once cache stabilizes.
- The 10-call burst totalled ~$0.155.

At ~$0.015 per short conversational turn, a 20-turn voice session costs $0.30 ish — fine. A heavy day of voice-noting could plausibly run into single-digit dollars. The `--bare` flag (which skips auto-memory load) would also drop the cold-start cost significantly.

## Implications for design / Phase 1

1. **Daemon "session lifecycle"** is now concrete:
   - On startup, load persisted `session_id` from disk (path TBD, e.g. `~/voice-assistant/.state/session.json`).
   - If no session exists, generate a fresh UUID; on first `/ask`, use `--session-id`.
   - Subsequent `/ask` calls use `--resume`.
   - On `--resume` failure, regenerate UUID and retry as a fresh session. Log the rotation.
2. **Daemon must `chdir`** to the voice-assistant workspace before every exec. Add a test that breaks if this is omitted.
3. **Session rotation policy** (deferred open question) — still deferred. A daily rotation or context-size-based rotation are both viable; spike doesn't force a decision.
4. **Latency mitigation work** (Phase 2): A/B `--bare` against the default invocation, and prototype the Agent-SDK daemon. Pick one before declaring conversational mode "good enough."
5. **`claude_daemon` HTTP timeout** must accommodate the 5–9 s wall — default FastAPI/uvicorn timeouts are fine but the *host orchestrator's* HTTP client must not time out at 5 s.

## Open items not closed by this spike

- Rate-limit behavior under sustained heavier load (the 10-call burst is too light to trigger Pro/Max throttling if it exists).
- `--bare` performance and feature parity for our use case (does it still load `CLAUDE.md` from the workspace, settings, allowed-tools?).
- Streaming output shape — re-run step 4 with `--verbose` when streaming TTS comes on the roadmap.

## Driver script

`scripts/spike-c-claude-print.sh` (run from any terminal where `claude` is on PATH and not already in use by an interactive session). Writes per-step JSON + stderr + timing to its output directory.
