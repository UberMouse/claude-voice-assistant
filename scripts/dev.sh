#!/usr/bin/env bash
set -euo pipefail
SESSION=voice-dev
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v tmux >/dev/null; then
  echo "tmux required" >&2; exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session $SESSION exists; attach with: tmux attach -t $SESSION"; exit 0
fi

tmux new-session -d -s "$SESSION" -n stt "nix develop --command sh -c '. .venv/bin/activate && voice-stt'"
tmux new-window  -t "$SESSION"   -n tts "nix develop --command sh -c '. .venv/bin/activate && voice-tts'"
tmux new-window  -t "$SESSION"   -n claude "nix develop --command sh -c '. .venv/bin/activate && voice-claude-daemon'"
tmux new-window  -t "$SESSION"   -n orch "nix develop --command sh -c '. .venv/bin/activate && voice-orchestrator'"

echo "Started. Attach with: tmux attach -t $SESSION"
