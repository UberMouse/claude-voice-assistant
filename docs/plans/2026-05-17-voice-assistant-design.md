# Voice-Activated Claude Code Assistant — Design

> **Update 2026-05-17:** After investigating GPU access from the dev VM, we chose **Option C: run the host stack as native Python on Windows** rather than NixOS-WSL. The hardware/hypervisor combo (single-GPU desktop, VMWare Workstation, no ESXi) makes both GPU passthrough to the existing VM and the Hyper-V-based WSL2 path unattractive: passthrough is unsupported on Workstation and would steal the host display; enabling WSL2 forces VMWare into WHP slow-mode for the existing dev VM. Option C trades the clean Nix packaging story for an unaffected dev VM and the simplest possible deployment surface. The HTTP wires and the VM side are unchanged.

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
│   Claude wrapper daemon ──▶ claude --print --resume <session-id>   │
│              ▲                                                     │
│              │  HTTP POST /speak                                   │
│              │                                                     │
│           `speak` CLI tool  ──── (hits host TTS server) ───────────┼─▶
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

**Spikes to do before committing:**
- CUDA-on-Windows: confirm `faster-whisper` and Kokoro run on the 4090 from a stock Python install, measure latency.
- Mic capture from Python on Windows: confirm `sounddevice` enumerates the user's mic and captures with acceptable latency.

## VM side

Unchanged from the prior design:

- **Claude wrapper daemon** — small HTTP service (FastAPI). `POST /ask {"text": "...", "mode": "oneshot"|"conversational"}`. Shells out to `claude --print --output-format=json --resume <session-id> "<text>"`. One systemd unit. Session ID persisted to disk.
- **`speak` CLI tool** — on `$PATH`. POSTs to host TTS server. Returns immediately; host queues and plays asynchronously.
- **Workspace** at `~/voice-assistant/`:
  - `notes/` — plain markdown
  - `CLAUDE.md` — runtime voice-mode instructions for Claude (be terse, use `speak`, when to stay silent)
  - MCP config for web search and future integrations
  - Settings tuned to skip permission prompts for `speak`, `WebFetch`, edits within the workspace

Claude is invoked from this directory so it picks up `CLAUDE.md` and settings automatically.

**Session continuity:** `--resume <session-id>` is shared across one-shot presses (so "what did I just say about X" works across presses); same session reused within a conversational burst. Exact rotation policy deferred.

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

- **`claude --print` subscription-plan limits**: if Claude Pro/Max rate limits bite, swap the VM wrapper daemon to call the Anthropic API directly via the Agent SDK. HTTP boundary `POST /ask` stays unchanged.
- **Windows Python deployment drift**: without Nix, the host venv can drift over time (Python version mismatches, native deps recompiled, missing CUDA runtime DLLs). Mitigation: pin everything in `pyproject.toml`, document the exact Python and CUDA versions in `docs/host-setup.md`, and ship a `verify-host.ps1` script that checks the install is sane.
- **CUDA / driver mismatch**: PyTorch wheels are pinned to a specific CUDA version. If the Windows NVIDIA driver is older than the CUDA runtime PyTorch wants, things fail quietly. Mitigation: pin a known-good combo, document it.
- **Code dev/deploy split**: developing in NixOS and deploying to Windows means platform-specific bugs (path separators, line endings, audio device enumeration differences) can sneak in. Mitigation: a small set of integration tests runnable on Windows after install.

## Open questions (deferred to implementation planning)

1. **Session rotation policy** — daily? idle timeout? per topic? Affects how often Claude has to rehydrate context from `notes/`.
2. **Barge-in** — can pressing the button mid-TTS interrupt and start a new utterance? Adds duck/cancel logic.
3. **Confirmation tones** — short audio cues for "listening" / "thinking" / "saved silently".
4. **Wake-word as a future option** — openWakeWord on CPU, easy bolt-on later.
5. **Voice selection** — Kokoro preset vs XTTS clone of the user's voice.
6. **Auth on local HTTP endpoints** — shared secret if anything ever leaves loopback.
7. **`claude --print` vs Agent SDK from day one** — depends on (1).
8. **Windows service management** — Task Scheduler vs NSSM vs Startup shortcut. Decide during Phase 2.
9. **Network reachability** — confirm VM↔host HTTP works on the user's VMWare network setup before assuming it does.
