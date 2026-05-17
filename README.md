# Claude Voice Assistant

A push-to-talk voice interface to Claude Code.

## Status

- **Phase 1 MVP: working end-to-end (2026-05-17).** F3 → STT → Claude → `speak` → TTS round trip proven on Windows host + NixOS VM.
- Phase 0 spikes: see `docs/spikes/`
- Phase 1 MVP plan: see `docs/plans/2026-05-17-voice-assistant-plan.md`
- Design: see `docs/plans/2026-05-17-voice-assistant-design.md`

## Next

- **Phase 1.5 — true press-and-hold.** Replace the fixed `VOICE_CAPTURE_SECS=5` window with start-on-press / stop-on-release; today the hotkey only debounces.
- **Phase 1.5 — conversational long-press mode.** Long-press hands the mic over to a VAD-driven turn-taking loop sharing the same Claude session.
- **Daemon: stream `speak` calls live.** Right now the orchestrator waits for `/ask` to return before going idle; partial `speak` invocations during a long turn could play sooner if `/ask` streamed.
- **Permission-mode hardening.** `Bash(speak:*)` works because of the workspace allowlist; revisit whether we need `--permission-mode` for safety once we add more tools.
- **Rate-limit surfacing.** Daemon already parses `rate_limit_event`; expose via `speak "five-hour limit resets at HH:MM"` when the user asks.
- **Docs polish.** Make `VOICE_TTS_URL` on the VM side more prominent in `docs/windows-setup.md` — forgetting it is the most common cause of silent failures.

## Quick start (dev)

```bash
nix develop
uv venv && uv pip install -e '.[dev]'
. .venv/bin/activate
./scripts/dev.sh
tmux attach -t voice-dev
```

Press F3 and speak. See `docs/smoke-test.md`.

## Env vars

| Var | Default | Used by |
|---|---|---|
| `VOICE_STT_URL` | `http://127.0.0.1:8001` | orchestrator |
| `VOICE_TTS_URL` | `http://127.0.0.1:8002` | orchestrator, speak |
| `VOICE_CLAUDE_URL` | `http://127.0.0.1:8003` | orchestrator |
| `VOICE_STT_MODEL` | `distil-large-v3` | stt server |
| `VOICE_STT_DEVICE` | `auto` | stt server |
| `VOICE_TTS_VOICE` | `af_sarah` | tts server |
| `VOICE_WORKSPACE` | `~/voice-assistant` | claude daemon |
| `VOICE_CLAUDE_BIN` | `claude` | claude daemon (override path to the `claude` binary) |
| `VOICE_HOTKEY` | `f3` | orchestrator |
| `VOICE_CAPTURE_SECS` | `5` | orchestrator (MVP — fixed capture window) |
| `VOICE_MIC_NAME` | _unset_ | audio capture (substring match into device name; falls back to default input) |

## Daemon status

The Claude wrapper daemon exposes `GET /status` which returns the current session id and the last `rate_limit_event` snapshot (subscription window, reset timestamps, overage status). Useful when debugging "why did my prompt fail" or eyeballing how close you are to a five-hour cap.

## Debug logging

All components log with a tag in front. To follow a specific component:

```
grep -E "stt-server|tts-(server|queue)|audio-capture|hotkey|orchestrator|claude-daemon|speak-cli" voice-assistant.log
```

## Layout

- `host/` — services running on the Windows host (STT, TTS, orchestrator, audio capture)
- `vm/` — services running in the Linux VM (Claude daemon, `speak` CLI, workspace template)
- `tests/` — pytest
- `docs/plans/` — design + implementation plans
- `docs/spikes/` — Phase 0 spike findings
- `scripts/dev.sh` — start everything in tmux for local dev
