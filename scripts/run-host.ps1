# Start the three Windows host services in separate Windows Terminal tabs.
# Falls back to separate PowerShell windows if `wt.exe` isn't available.
#
# Prereqs: install-host.ps1 has been run. Env vars below should be set in your
# PowerShell session (or in your profile / a .env loader).
#
# Required env (set per shell or in $PROFILE):
#   VOICE_CLAUDE_URL = http://<vm-lan-ip>:8003       # where the VM's claude daemon listens
#   VOICE_MIC_NAME   = "<substring of mic name>"     # see `python -m sounddevice` for names
#
# Optional:
#   VOICE_TTS_HOST   = 0.0.0.0   # default here (so the VM can reach the TTS server)
#   VOICE_HOTKEY     = lshift+f3
#   VOICE_CAPTURE_SECS = 5
#
# DEBUG-TAG: run-host

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

# Bind TTS to all interfaces so the VM's `speak` CLI can reach it.
if (-not $env:VOICE_TTS_HOST) { $env:VOICE_TTS_HOST = "0.0.0.0" }

if (-not $env:VOICE_CLAUDE_URL) {
  Write-Warning "VOICE_CLAUDE_URL is not set. The orchestrator will try http://127.0.0.1:8003 (wrong)."
  Write-Warning "Set it to the VM's LAN URL, e.g.: `$env:VOICE_CLAUDE_URL = 'http://192.168.1.50:8003'"
}
if (-not $env:VOICE_MIC_NAME) {
  Write-Warning "VOICE_MIC_NAME is not set. The default input device will be used (often wrong on Windows)."
  Write-Warning "List devices: .\.venv\Scripts\python.exe -m sounddevice"
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  throw "Not found: $venvPython (run .\scripts\install-host.ps1 first)"
}

function Start-VoiceService {
  param([string]$Title, [string]$Module)
  # Host scripts are invoked via `python -m <module>` rather than console
  # entry points -- only the VM-side scripts (voice-claude-daemon, speak) are
  # exposed in [project.scripts]. See pyproject.toml.
  #
  # NOTE: do not put `;` inside the -Command string passed to wt.exe -- wt
  # interprets `;` as a tab separator and you get extra empty tabs.
  # Each tab must start in $Root so voice-tts can find kokoro-v1.0.onnx /
  # voices-v1.0.bin via their default relative paths.
  if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    Start-Process wt.exe -ArgumentList @(
      "-w", "0", "new-tab", "--title", $Title, "-d", "$Root",
      "powershell", "-NoExit", "-Command", "& '$venvPython' -m $Module"
    )
  } else {
    Start-Process powershell -WorkingDirectory $Root -ArgumentList @(
      "-NoExit", "-Command", "& '$venvPython' -m $Module"
    )
  }
}

Start-VoiceService -Title "voice-stt"          -Module "host.stt.cli"
Start-VoiceService -Title "voice-tts"          -Module "host.tts.cli"
Start-VoiceService -Title "voice-orchestrator" -Module "host.orchestrator.cli"

Write-Host "==> Three services launched. Watch each tab/window for startup logs." -ForegroundColor Green
