#!/usr/bin/env bash
# Dev convenience: run all four services in tmux windows against the local
# .venv. Production deployment is different -- host services run on Windows
# (see scripts/run-host.ps1); only the daemon runs in the VM (see
# scripts/install-vm.sh + the systemd user unit).
#
# Prereqs (one-time):
#   nix develop --command uv sync --extra host --extra dev
#
# Host scripts (stt/tts/orchestrator) are no longer exposed as console entry
# points -- only voice-claude-daemon and speak are (see pyproject.toml). So
# we invoke everything here via `python -m` for consistency.
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

run() { echo "nix develop --command sh -c '. .venv/bin/activate && python -m $1'"; }

tmux new-session -d -s "$SESSION" -n stt    "$(run host.stt.cli)"
tmux new-window  -t "$SESSION"   -n tts    "$(run host.tts.cli)"
tmux new-window  -t "$SESSION"   -n claude "$(run vm.claude_daemon.cli)"
tmux new-window  -t "$SESSION"   -n orch   "$(run host.orchestrator.cli)"

echo "Started. Attach with: tmux attach -t $SESSION"
