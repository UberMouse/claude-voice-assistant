# Voice-Activated Claude Code Assistant — Design

> **Update 2026-05-17 (a):** After investigating GPU access from the dev VM, we chose **Option C: run the host stack as native Python on Windows** rather than NixOS-WSL. The hardware/hypervisor combo (single-GPU desktop, VMWare Workstation, no ESXi) makes both GPU passthrough to the existing VM and the Hyper-V-based WSL2 path unattractive: passthrough is unsupported on Workstation and would steal the host display; enabling WSL2 forces VMWare into WHP slow-mode for the existing dev VM. Option C trades the clean Nix packaging story for an unaffected dev VM and the simplest possible deployment surface. The HTTP wires and the VM side are unchanged.
>
> **Update 2026-05-17 (b):** Phase 0 spikes complete (A, B, C, D — see `docs/spikes/`). Spike D supersedes the original "shell out to `claude --print --resume` per request" daemon: the VM-side daemon now keeps **one long-lived `claude` subprocess** with stream-json I/O on stdin/stdout, dropping warm-turn latency from 6.5–8 s to 1.5–2.8 s and exposing the Pro/Max `rate_limit_event` inline. STT confirmed at 288 ms on the RTX 4090 (Spike B); TTS pinned to CPU (Spike B). Other findings folded into the implementation plan.

## Overview

A push-to-talk personal voice assistant backed by Claude Code. The user presses a button on their Windows host, speaks, and Claude (running in a Linux VM) responds via speech.

**Use cases** (in priority order):
- Voice notes / second brain — capture and retrieve thoughts hands-free
- On-demand research and Q&A — ask things, get spoken answers
- Agent ops — voice-triggered longer tasks (PRs, email, calendar)

Explicitly **not** a hands-free coding companion. Claude is not pinned to a repo; it has a dedicated workspace.

**Interaction modes** (both available):
- **One-shot**: short button tap → single utterance → single response.
- **Conversational**: long-press to enter → back-and-forth with VAD-driven turn-taking → second tap (or sustained silence) to exit.

## Topology

```
┌─ Windows 11 Host (RTX 4090, Python native) ───────────────────────┐
│                                                                   │
│   [Push-to-talk hotkey] ──▶ Orchestrator (Python on Windows)      │
│   (pynput global hook)            │                                │
│                              ┌────┴───┐                            │
│                              ▼        ▼                            │
│                       Mic capture    STT (faster-whisper, CUDA)    │
│                                       │                            │
│                              ┌────────┘                            │
│                              ▼                                     │
│                       Send text ─────────────────────┐             │
│                                                      │             │
│                       TTS (Kokoro, CUDA)             │             │
│                              ▲                       │             │
│                              │                       │             │
│                       Speaker playback (Windows audio)             │
│                                                      │             │
└──────────────────────────────────────────────────────┼─────────────┘
                                                       │ HTTP
┌─ Linux VM (existing NixOS dev env) ──────────────────┼─────────────┐
│                                                      ▼             │
│   Claude wrapper daemon                                            │
│      │   stdin  ──▶  one long-lived `claude --print                │
│      │                  --input-format=stream-json                 │
│      │                  --output-format=stream-json --verbose`     │
│      │   stdout ◀──  JSON line stream (system/user/assistant/      │
│      │                rate_limit_event/result events)              │
│      ▲                                                             │
│      │  HTTP POST /speak                                           │
│      │                                                             │
│   `speak` CLI tool  ──── (hits host TTS server) ───────────────────┼─▶
│                                                                    │
│   Workspace: ~/voice-assistant/{notes,CLAUDE.md,...}               │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

Audio I/O, STT, TTS, and the push-to-talk hotkey all run natively on the Windows host (no WSL, no extra hypervisor layer). Claude runs in the existing Linux VM. Two HTTP boundaries:
- Host orchestrator → VM: sends transcript
- VM `speak` tool → host TTS server: sends text to play

## Host side (Windows native Python)

The host stack is plain Python 3.12 running directly on Windows, with CUDA via the standard Windows NVIDIA driver and the official PyTorch / `faster-whisper` / Kokoro Windows builds. No WSL, no AutoHotkey shim — `pynput` registers a Win32 global hotkey directly.

**Deploy / update flow:**
1. Develop and test in the NixOS VM (this repo) using localhost services.
2. Push code to a Git remote (GitHub or self-hosted).
3. On the Windows host (one-time setup): install Python 3.12 + `uv` + Git + NVIDIA driver + CUDA runtime. Clone the repo. Run a small bootstrap script (`scripts/install-host.ps1`) that creates a venv, installs deps, downloads model weights, and registers the four services to start at logon.
4. Update flow: `git pull` on the host, re-run install script (idempotent), restart services.

This is less elegant than `nix build && wsl --import`, but the moving parts are minimal: one venv, one model cache, one set of services. The repo is the source of truth and updates ride normal Git.

**Components on Windows** (each is a Python process):
- **Orchestrator** — state machine. Owns the hotkey listener.
- **Mic capture** — `sounddevice` (PortAudio) calling Windows audio. Silero VAD for end-of-utterance in conversational mode.
- **STT server** — `faster-whisper` on CUDA. HTTP endpoint.
- **TTS server** — Kokoro (or XTTS) on CUDA. HTTP endpoint. Plays via `sounddevice` to the Windows default output. Internal queue prevents overlap.

**Service management** — open question. Three plausible options, decide during Phase 2:
1. **Windows Task Scheduler** at logon, restart on failure — built in, no extra software.
2. **NSSM** (Non-Sucking Service Manager) — wraps each Python process as a proper Windows service; cleaner logs, survives logoff.
3. **Plain Startup shortcut + tmux-style multiplexer** — simplest but no resilience.

**Why this is less bad than it sounds:**
- Windows CUDA + PyTorch + faster-whisper is the canonical setup; well-documented and stable.
- Audio on Windows via PortAudio "just works" — no PulseAudio bridge, no WSLg.
- `pynput` registers Win32 global hotkeys natively — the AHK shim from the previous design is gone.
- The VM side is unchanged, so the architectural seam (HTTP wires) is intact.

**Spikes done (2026-05-17):**
- **A — Windows audio:** `sounddevice`/PortAudio works; pin devices by name substring (Windows reorders indices when Bluetooth devices come/go). See `docs/spikes/spike-a-windows-audio.md`.
- **B — CUDA on Windows:** `faster-whisper` distil-large-v3 transcribes a 3-second clip in 288 ms on the 4090. Kokoro pinned to CPU (kokoro_onnx 0.5 doesn't expose ORT providers cleanly; CPU steady-state is 0.27× realtime which is fine). Critical: ctranslate2's native loader on Windows ignores `os.add_dll_directory()` — the launcher must prepend `site-packages/nvidia/*/bin` to `PATH` before Python starts. See `docs/spikes/spike-b-cuda-windows.md`.
- **C — `claude --print --resume`:** Session pinning works, but `--resume <unknown-uuid>` errors hard rather than creating; daemon must use `--session-id` on first launch and `--resume` thereafter. CLI cold-start tax is ~5 s per invocation. See `docs/spikes/spike-c-claude-print.md`.
- **D — Long-lived `claude` with stream-json:** Beats `--print`-per-call 3–5×. Warm turns 1.5–2.8 s. `rate_limit_event` surfaces inline. This is the daemon model we ship. See `docs/spikes/spike-d-claude-stream.md`.

## VM side

- **Claude wrapper daemon** — small HTTP service (FastAPI). `POST /ask {"text": "...", "mode": "oneshot"|"conversational"}`. Owns **one long-lived `claude` subprocess** spawned at daemon start with `--print --input-format=stream-json --output-format=stream-json --verbose`. Each `/ask` writes a single `{"type":"user","message":{"role":"user","content":<text>}}` JSON line to subprocess stdin and reads stdout JSON lines until a `{"type":"result", ...}` line arrives. **The 5-second CLI cold-start tax is paid once at daemon start, not per request** (Spike D: warm turns drop to 1.5–2.8 s). One systemd unit. Canonical session ID is read from the first `system/init` event in the stream and persisted to disk so the daemon can respawn the subprocess with `--resume <id>` after a crash or restart. `rate_limit_event` lines are drained into a `/status` endpoint so the orchestrator (and voice-Claude itself, via `CLAUDE.md` instructions) can surface "approaching limit" warnings to the user before a hard 429.
- **`speak` CLI tool** — on `$PATH`. POSTs to host TTS server. Returns immediately; host queues and plays asynchronously.
- **Workspace** at `~/voice-assistant/`:
  - `notes/` — plain markdown
  - `CLAUDE.md` — runtime voice-mode instructions for Claude (be terse, use `speak`, when to stay silent)
  - MCP config for web search and future integrations
  - Settings tuned to skip permission prompts for `speak`, `WebFetch`, edits within the workspace

The daemon **must `chdir` into the workspace before spawning the subprocess** so the session JSONL lands in `~/.claude/projects/<workspace-cwd-encoded>/<id>.jsonl` consistently across daemon restarts (Spike C). Claude also picks up the workspace's `CLAUDE.md` and settings automatically because of this cwd.

**Session continuity:** the same long-lived process serves all turns within a daemon lifetime, so context is shared across all one-shot presses *and* conversational bursts for free. On daemon restart the persisted session id is fed back via `--resume`; if that fails (`No conversation found with session ID …`), the daemon spawns a fresh session and persists the new id from the new `system/init`. Exact rotation policy still deferred.

## Communication wire

Two HTTP boundaries, both unauthenticated on the local network (single-user setup):

| From | To | Endpoint | Semantics |
|------|----|----|-----------|
| Host orchestrator | VM Claude daemon | `POST /ask {text, mode}` | Returns when Claude finishes |
| VM `speak` CLI | Host TTS server | `POST /speak {text}` | Returns immediately, host plays async |

Both endpoints reach each other across the VMWare virtual network (NAT or host-only — concrete IP depends on user's VM network config; the VM URL on the host is typically something like `http://<vm-ip>:8003`, the host URL from the VM is `http://<host-ip>:8002`).

The host orchestrator only consumes Claude's process exit signal — not Claude's stdout. Any `speak` calls during the run already delivered audio.

## Orchestrator state machine

```
        ┌─────────────┐
        │    IDLE     │◀──────────────────────────┐
        └──────┬──────┘                           │
               │ short tap (one-shot)             │
               │ or long-press (conversational)   │
               ▼                                  │
        ┌─────────────┐                           │
        │  RECORDING  │                           │
        └──────┬──────┘                           │
               │ button release (one-shot)        │
               │ or VAD silence (conversational)  │
               ▼                                  │
        ┌─────────────┐                           │
        │   ASR'ING   │ faster-whisper, ~200ms    │
        └──────┬──────┘                           │
               ▼                                  │
        ┌─────────────┐                           │
        │  AWAITING   │ POST /ask → VM            │
        │   CLAUDE    │ (TTS plays as Claude      │
        └──────┬──────┘  calls `speak` mid-run)   │
               ▼                                  │
        ┌─────────────┐                           │
        │  SPEAKING   │ drain TTS queue           │
        └──────┬──────┘                           │
               │                                  │
        one-shot ──────────────────────────────── ┘
        conversational ──▶ back to RECORDING (VAD-armed)
```

Conversational mode exits on: second button tap, N seconds of silence with no new utterance, or Claude signaling end of conversation.

## Risks and known fallbacks

- **Pro/Max subscription rate limits**: now *partially derisked* — Spike D shows the stream emits a `rate_limit_event` with status (`allowed`/overage/etc.), `rateLimitType`, and reset timestamps. The daemon exposes this on `/status`, and the runtime `CLAUDE.md` can instruct voice-Claude to warn the user when overage status changes. Hard-fallback if we hit ceilings anyway: swap the daemon's subprocess for the Anthropic API via the Agent SDK; the `POST /ask` HTTP boundary stays unchanged.
- **Long-lived subprocess dies mid-session**: the daemon detects EOF or non-zero exit on the `claude` subprocess, respawns it with `--resume <persisted-session-id>`, and (if `--resume` errors with "No conversation found") falls back to spawning fresh and persisting the new id from `system/init`. Tested behavior, not speculation.
- **CWD coupling**: the session JSONL is stored under `~/.claude/projects/<cwd-encoded>/`. If the daemon is launched from a different cwd between runs, `--resume` won't find the session. Mitigation: daemon `chdir`'s into the workspace dir before spawn. Integration test asserts the cwd of the spawned subprocess.
- **Windows Python deployment drift**: without Nix, the host venv can drift over time (Python version mismatches, native deps recompiled, missing CUDA runtime DLLs). Mitigation: pin everything in `pyproject.toml`, document the exact Python and CUDA versions in `docs/host-setup.md`, and ship a `verify-host.ps1` script that checks the install is sane.
- **Windows CUDA DLL discoverability**: `ctranslate2`'s native loader on Windows ignores `os.add_dll_directory()` — DLLs are resolved against `PATH` *as set before* Python starts. Mitigation: the host STT service is launched via a wrapper that prepends every `site-packages/nvidia/*/bin` directory to `PATH` before exec'ing Python. Baked into Phase 1.
- **Code dev/deploy split**: developing in NixOS and deploying to Windows means platform-specific bugs (path separators, line endings, audio device enumeration differences) can sneak in. Mitigation: a small set of integration tests runnable on Windows after install, and devices pinned by name substring rather than index (Spike A).

## Open questions (deferred to implementation planning)

1. **Session rotation policy** — daily? idle timeout? per topic? Affects how often Claude has to rehydrate context from `notes/`. Note: with the long-lived-subprocess daemon, "rotate" means kill the subprocess and respawn without `--resume` — context is otherwise shared for the daemon's full lifetime.
2. **Barge-in** — can pressing the button mid-TTS interrupt and start a new utterance? Adds duck/cancel logic.
3. **Confirmation tones** — short audio cues for "listening" / "thinking" / "saved silently".
4. **Wake-word as a future option** — openWakeWord on CPU, easy bolt-on later.
5. **Voice selection** — Kokoro preset vs XTTS clone of the user's voice.
6. **Auth on local HTTP endpoints** — shared secret if anything ever leaves loopback.
7. ~~**`claude --print` vs Agent SDK from day one**~~ — **closed by Spike D**: long-lived `claude --print` stream-json gives us subscription-plan auth, inline rate-limit visibility, and warm-turn latency in the 1.5–2.8 s range. Agent SDK stays in reserve as the fallback if rate limits actually bite.
8. **Windows service management** — Task Scheduler vs NSSM vs Startup shortcut. Decide during Phase 2.
9. **Network reachability** — confirm VM↔host HTTP works on the user's VMWare network setup before assuming it does.
10. **Permission-denial handling in the stream** — Spike D didn't exercise a tool denial; the `result.permission_denials` field exists but its shape isn't documented yet. Phase-1 integration tests should cover this when we wire up `Bash(speak:*)` allow-list interactions.
