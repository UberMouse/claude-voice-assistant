# Voice-Activated Claude Code Assistant — Design

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
┌─ Windows 11 Host (RTX 4090) ──────────────────────────────────────┐
│                                                                   │
│   [Push-to-talk hotkey] ──▶ Orchestrator (in WSL)                 │
│        (AHK shim)                │                                 │
│                              ┌───┴────┐                            │
│                              ▼        ▼                            │
│                       Mic capture    STT (faster-whisper, GPU)     │
│                                       │                            │
│                              ┌────────┘                            │
│                              ▼                                     │
│                       Send text ─────────────────────┐             │
│                                                      │             │
│                       TTS (Kokoro/XTTS, GPU)         │             │
│                              ▲                       │             │
│                              │                       │             │
│                       Speaker playback (WSLg → Win)  │             │
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

Audio I/O, STT, TTS, and the push-to-talk hotkey all run on the Windows host (RTX 4090). Claude runs in the existing Linux VM. Two HTTP boundaries:
- Host orchestrator → VM: sends transcript
- VM `speak` tool → host TTS server: sends text to play

## Host side (Windows + NixOS-WSL)

The entire host-side stack is packaged as a **NixOS-WSL distro**, built from a Nix flake in the user's existing NixOS VM and shipped to the Windows host as a tarball.

**Deploy / update flow:**
1. `nix build .#nixosConfigurations.voice-host.config.system.build.tarballBuilder` in the VM
2. `scp` the resulting tarball to the host
3. `wsl --import voice-host C:\wsl\voice-host nixos.wsl` (first time) or `wsl --import --override` (subsequent)
4. Atomic rollback by selecting an older NixOS generation

**Components inside the WSL distro** (all as systemd units):
- **Orchestrator** — owns the state machine. Python or Rust.
- **Mic capture** — PortAudio/`sounddevice` + Silero VAD for end-of-utterance detection in conversational mode.
- **STT server** — [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (CTranslate2-backed Whisper) on GPU. HTTP endpoint. `large-v3` transcribes a 10s clip in sub-second on a 4090.
- **TTS server** — Kokoro (recommended: fast, low VRAM, good quality) or XTTS-v2 / Coqui (more natural, voice cloning). HTTP endpoint. Plays through host default output via WSLg. Internal queue prevents overlapping playback from rapid `speak` calls.

**Why WSL2, not VMWare passthrough:**
- CUDA-on-WSL is mature (NVIDIA's WSL driver + `nvidia-smi` works inside WSL with stock Windows NVIDIA drivers)
- WSLg bridges PulseAudio to Windows audio devices automatically — no audio plumbing
- The whole environment is one Nix-built tarball; reproducible, atomic updates

**The one Windows-native piece:** a global push-to-talk hotkey shim. Win32 global hotkeys can't be registered from inside WSL. Options:
1. **AutoHotkey one-liner** (recommended) — `.ahk` script in Startup, ~5 lines. On hotkey down/up, fires `curl http://<wsl-ip>:8000/ptt-down`/`/ptt-up`. Effectively never needs updating.
2. **Tiny Rust binary** cross-compiled from the VM via `pkgsCross.mingwW64`. Single `.exe`, no runtime.
3. **Hardware trigger** (foot pedal, Stream Deck) — possible via `usbipd-win`, more setup. Only if a physical button is wanted.

**Spikes to do before committing:**
- WSLg microphone latency and stability under load
- CUDA driver-version compatibility between the host's Windows NVIDIA driver and the CUDA libs `nixpkgs` ships (may need a `cudaPackages_12_x` pin)

## VM side

- **Claude wrapper daemon** — small HTTP service (e.g. FastAPI). One endpoint: `POST /ask {"text": "...", "mode": "oneshot"|"conversational"}`. Internally shells out to `claude --print --output-format=stream-json --resume <session-id> "<text>"`. Returns when Claude exits. One systemd unit. Session ID persisted to disk.
- **`speak` CLI tool** — on `$PATH`. Bash one-liner or ~20 line Python script. `POST`s to host TTS server. Returns immediately; the host queues and plays asynchronously so Claude isn't blocked on TTS finishing.
- **Workspace** at `~/voice-assistant/`:
  - `notes/` — plain markdown (future: maybe Obsidian, not designed for now)
  - `CLAUDE.md` — runtime voice-mode instructions for Claude (be terse, use `speak`, when to stay silent and play a confirmation tone, etc.). Distinct from the project-level `CLAUDE.md` at the repo root.
  - MCP config for web search and any future integrations
  - Settings tuned to skip permission prompts for `speak`, `WebFetch`, edits within the workspace

Claude is invoked from this directory so it picks up `CLAUDE.md` and settings automatically.

**Session continuity:** `--resume <session-id>` is shared across one-shot presses (so "what did I just say about X" works across presses); same session is reused within a conversational burst. Exact session-rotation policy is deferred.

## Communication wire

Two HTTP boundaries, both unauthenticated on the local network (single-user setup):

| From | To | Endpoint | Semantics |
|------|----|----|-----------|
| Host orchestrator | VM Claude daemon | `POST /ask {text, mode}` | Returns when Claude finishes |
| VM `speak` CLI | Host TTS server | `POST /speak {text}` | Returns immediately, host plays async |

The host orchestrator only consumes Claude's process exit signal — not Claude's stdout. Any `speak` calls during the run already delivered audio. The orchestrator's only post-`/ask` job is transitioning the state machine.

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
- **WSLg audio**: needs a spike. If mic latency is unacceptable, fall back to a Windows-native audio-capture process forwarding samples to WSL over a TCP socket.
- **CUDA version drift**: pin `cudaPackages_12_x` in the Nix flake; bump deliberately.

## Open questions (deferred to implementation planning)

1. **Session rotation policy** — daily? idle timeout? per topic? Affects how often Claude has to rehydrate context from `notes/`.
2. **Barge-in** — can pressing the button mid-TTS interrupt and start a new utterance? Adds duck/cancel logic.
3. **Confirmation tones** — short audio cues for "listening" / "thinking" / "saved silently".
4. **Wake-word as a future option** — openWakeWord on CPU, easy bolt-on later.
5. **Voice selection** — Kokoro preset vs XTTS clone of the user's voice.
6. **Auth on local HTTP endpoints** — shared secret if anything ever leaves loopback.
7. **`claude --print` vs Agent SDK from day one** — depends on (1).
