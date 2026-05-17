#!/usr/bin/env bash
# Idempotent installer for the VM (Linux) side of claude-voice-assistant.
# Installs `voice-claude-daemon` and `speak` into ~/.local/bin/ as editable
# tools managed by uv, so source edits are picked up on next process start.
#
# Prereqs (one-time, not handled here):
#   - uv on PATH (`nix profile install nixpkgs#uv` or system package)
#   - Python 3.12 available to uv (uv will download one if not)
#   - The `claude` CLI on PATH (for the daemon's subprocess shell-out)
#
# Run from anywhere; the script cd's into the repo root.
#   $ ./scripts/install-vm.sh
#
# After install, ~/.local/bin must be on PATH (it usually is on NixOS).
# Verify with: command -v voice-claude-daemon && command -v speak
#
# DEBUG-TAG: install-vm
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "==> Repo root: $ROOT"

if ! command -v uv >/dev/null; then
  echo "uv not found on PATH. Install it (e.g. via nix or your package manager) and retry." >&2
  exit 1
fi

# `uv tool install --editable .` puts the package into an isolated tool venv
# and symlinks its [project.scripts] into ~/.local/share/uv/tools/.../bin,
# which uv then exposes via ~/.local/bin/ entries. `--reinstall` makes this
# idempotent across pyproject changes.
echo "==> Installing claude-voice-assistant as an editable uv tool"
uv tool install --editable --reinstall .

echo
echo "==> Installed. Check:"
echo "    command -v voice-claude-daemon"
echo "    command -v speak"
echo
echo "    If those aren't found, ensure \$HOME/.local/bin is on PATH."
echo "    uv tool dir --bin   # shows where uv installs script symlinks"
