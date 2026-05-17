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
#   VOICE_HOTKEY     = f8
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

$venvActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"

function Start-Service {
  param([string]$Title, [string]$Command)
  if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    # Open a new tab in the focused Windows Terminal window.
    Start-Process wt.exe -ArgumentList @(
      "-w", "0", "new-tab", "--title", $Title,
      "powershell", "-NoExit", "-Command", ". '$venvActivate'; $Command"
    )
  } else {
    Start-Process powershell -ArgumentList @(
      "-NoExit",
      "-Command",
      ". '$venvActivate'; `$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    )
  }
}

Start-Service -Title "voice-stt"          -Command "voice-stt"
Start-Sleep -Milliseconds 400
Start-Service -Title "voice-tts"          -Command "voice-tts"
Start-Sleep -Milliseconds 400
Start-Service -Title "voice-orchestrator" -Command "voice-orchestrator"

Write-Host "==> Three services launched. Watch each tab/window for startup logs." -ForegroundColor Green
