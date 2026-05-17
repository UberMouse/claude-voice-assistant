#!/usr/bin/env bash
# Idempotent installer for the systemd user unit that runs
# voice-claude-daemon at boot/login.
#
# Prereqs:
#   - scripts/install-vm.sh has been run (so ~/.local/bin/voice-claude-daemon exists)
#   - systemd as the user's init (true on NixOS)
#
# What this does:
#   1. Symlinks scripts/claude-voice-daemon.service into ~/.config/systemd/user/
#   2. Seeds ~/.config/voice-assistant/daemon.env from the .example if missing
#   3. systemctl --user daemon-reload + enable --now
#   4. Reports whether linger is enabled (needed for boot-time start without login)
#
# Run from anywhere; the script cd's into the repo root.
#   $ ./scripts/install-vm-systemd.sh
#
# DEBUG-TAG: install-vm-systemd
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UNIT_NAME="claude-voice-daemon.service"
UNIT_SRC="$ROOT/scripts/$UNIT_NAME"
UNIT_DST="$HOME/.config/systemd/user/$UNIT_NAME"
ENV_EXAMPLE="$ROOT/scripts/voice-daemon.env.example"
ENV_DST_DIR="$HOME/.config/voice-assistant"
ENV_DST="$ENV_DST_DIR/daemon.env"

# 1. Pre-flight: the daemon binary must exist.
if [ ! -x "$HOME/.local/bin/voice-claude-daemon" ]; then
  echo "==> voice-claude-daemon not on \$HOME/.local/bin. Run ./scripts/install-vm.sh first." >&2
  exit 1
fi

# 2. Symlink the unit (force, so re-runs pick up edits to the source file).
mkdir -p "$(dirname "$UNIT_DST")"
ln -sf "$UNIT_SRC" "$UNIT_DST"
echo "==> Linked $UNIT_DST -> $UNIT_SRC"

# 3. Seed the env file from the example if the user hasn't created one.
if [ ! -e "$ENV_DST" ]; then
  mkdir -p "$ENV_DST_DIR"
  cp "$ENV_EXAMPLE" "$ENV_DST"
  echo "==> Seeded $ENV_DST from voice-daemon.env.example"
  echo "    Edit it to set VOICE_TTS_URL to your Windows host's LAN address."
else
  echo "==> $ENV_DST already present (not overwriting)"
fi

# 4. Reload + enable + start. `enable --now` is idempotent.
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

# 5. Report linger status. Without linger, the unit only runs while you're
#    logged in. Enabling it requires root, so we just print the command.
linger_state="$(loginctl show-user "$USER" 2>/dev/null | awk -F= '/^Linger=/ {print $2}')"
echo
if [ "$linger_state" = "yes" ]; then
  echo "==> Linger is enabled -- the daemon will start at boot."
else
  echo "==> Linger is NOT enabled. The daemon only runs while you're logged in."
  echo "    To start it at boot, run:"
  echo "        sudo loginctl enable-linger $USER"
fi

# 6. Quick status hint.
echo
echo "==> Useful commands:"
echo "    systemctl --user status  $UNIT_NAME"
echo "    systemctl --user restart $UNIT_NAME"
echo "    journalctl --user -u $UNIT_NAME -f"
