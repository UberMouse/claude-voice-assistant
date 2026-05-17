# Claude Voice Assistant

A push-to-talk voice interface to Claude Code. User presses a button on a Windows host, speaks, and Claude (running in a Linux VM) responds via TTS.

## Read first

Full design: [docs/plans/2026-05-17-voice-assistant-design.md](docs/plans/2026-05-17-voice-assistant-design.md)

## Architecture at a glance

- **Windows 11 host (RTX 4090)**: button + mic capture + STT + TTS + speaker playback. Runs as **native Python on Windows**, not WSL. Code is developed in the NixOS dev VM and deployed to Windows via `git pull` + a small bootstrap script.
- **Linux VM (existing NixOS dev env)**: Claude Code wrapper daemon + `speak` CLI tool + notes workspace.
- **Two HTTP wires**: host orchestrator → VM (`POST /ask`), VM → host (`POST /speak`).

## Key decisions

- **Native Python on Windows, not WSL.** GPU passthrough to the existing VM is unsupported on VMWare Workstation; enabling WSL2 forces VMWare into WHP slow-mode for the existing dev VM. Native Windows Python keeps the dev VM unaffected and uses the host GPU directly. We lose Nix packaging for the host side; we keep it for the VM side.
- **Claude has an explicit `speak` CLI tool**, not auto-speak-the-final-response. Lets Claude give progress updates, stay silent, or split a long response.
- **Two interaction modes**: short tap = one-shot, long-press = conversational (VAD-driven turn-taking). Both share the same Claude session for context continuity.
- **Use cases**: voice notes / second brain, on-demand research / Q&A, agent ops. *Not* a coding companion — Claude is not pinned to a repo.
- **Updates on the host = `git pull` + idempotent install script.** Not as clean as `nix build && wsl --import`, but the smallest possible deployment surface.

## Repo layout (planned, fill in as built)

- `host/` — Python services that run on the Windows host (orchestrator, STT server, TTS server, audio capture)
- `vm/` — Claude wrapper daemon, `speak` CLI, runtime workspace template (including its own runtime `CLAUDE.md` distinct from this one)
- `scripts/` — `install-host.ps1`, `dev.sh`, etc.
- `docs/plans/` — design docs and implementation plans
- `docs/spikes/` — spike findings

## Two CLAUDE.md files (don't confuse them)

- **This file** (`/CLAUDE.md`): context for Claude Code sessions *building* the assistant.
- **Runtime workspace `CLAUDE.md`** (`vm/.../CLAUDE.md`, lives in the deployed VM workspace): instructions for the voice-assistant Claude itself — be terse, use `speak`, defaults for when to stay silent, etc.

## Risks tracked

- `claude --print` may hit subscription-plan rate limits. Fallback: swap wrapper daemon to Agent SDK + Anthropic API. HTTP boundary unchanged.
- Windows Python venv can drift without Nix-style pinning — mitigated by strict `pyproject.toml` pins, a `verify-host.ps1` script, and documented Python+CUDA versions.
- VMWare network setup must allow VM↔host HTTP — confirm before deep work.

## Status

- 2026-05-17: Design complete (Option C — native Windows Python). No implementation yet. Next: detailed implementation plan execution starting at Phase 0 spikes (CUDA-on-Windows, `claude --print` behavior).
