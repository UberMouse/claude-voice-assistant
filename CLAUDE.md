# Claude Voice Assistant

A push-to-talk voice interface to Claude Code. User presses a button on a Windows host, speaks, and Claude (running in a Linux VM) responds via TTS.

## Read first

Full design: [docs/plans/2026-05-17-voice-assistant-design.md](docs/plans/2026-05-17-voice-assistant-design.md)

## Architecture at a glance

- **Windows 11 host (RTX 4090)**: button + mic capture + STT + TTS + speaker playback. Entire stack packaged as a **NixOS-WSL distro** built from a Nix flake in the dev VM. One small AutoHotkey (or Rust) shim handles the global hotkey on the Windows side.
- **Linux VM (existing NixOS dev env)**: Claude Code wrapper daemon + `speak` CLI tool + notes workspace.
- **Two HTTP wires**: host orchestrator → VM (`POST /ask`), VM → host (`POST /speak`).

## Key decisions

- **WSL2 + WSLg, not VMWare GPU passthrough.** CUDA-on-WSL is mature; WSLg bridges audio. The host stack lives in WSL so the whole thing is one Nix-built tarball.
- **Claude has an explicit `speak` CLI tool**, not auto-speak-the-final-response. Lets Claude give progress updates, stay silent, or split a long response.
- **Two interaction modes**: short tap = one-shot, long-press = conversational (VAD-driven turn-taking). Both share the same Claude session for context continuity.
- **Use cases**: voice notes / second brain, on-demand research / Q&A, agent ops. *Not* a coding companion — Claude is not pinned to a repo.
- **Updates are atomic**: `nix build` in the VM → `wsl --import` on the host. NixOS generations for rollback.

## Repo layout (planned, fill in as built)

- `host/` — Nix flake for the WSL distro (orchestrator, STT server, TTS server) + AHK shim
- `vm/` — Claude wrapper daemon, `speak` CLI, runtime workspace template (including its own runtime `CLAUDE.md` distinct from this one)
- `docs/plans/` — design docs and implementation plans

## Two CLAUDE.md files (don't confuse them)

- **This file** (`/CLAUDE.md`): context for Claude Code sessions *building* the assistant.
- **Runtime workspace `CLAUDE.md`** (`vm/.../CLAUDE.md`, lives in the deployed VM workspace): instructions for the voice-assistant Claude itself — be terse, use `speak`, defaults for when to stay silent, etc.

## Risks tracked

- `claude --print` may hit subscription-plan rate limits. Fallback: swap wrapper daemon to Agent SDK + Anthropic API. HTTP boundary unchanged.
- WSLg mic latency under load needs a spike before full commit.
- CUDA libs in `nixpkgs` vs Windows NVIDIA driver: pin `cudaPackages_12_x`.

## Status

- 2026-05-17: Design complete, no implementation yet, repo not yet under git. Next: detailed implementation plan.
