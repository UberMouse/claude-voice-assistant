# Idempotent installer for the Windows host side of claude-voice-assistant.
#
# Prereqs (one-time, not handled here):
#   - Python 3.12 from python.org on PATH
#   - Git for Windows on PATH
#   - NVIDIA driver (>= recent for RTX 40-series). `nvidia-smi` should work.
#
# Run from anywhere; the script cd's into the repo root.
#   PS> .\scripts\install-host.ps1
#
# DEBUG-TAG: install-host

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
Write-Host "==> Repo root: $Root"

# 1. Verify prerequisites
Write-Host "==> Checking prerequisites"
$python = (Get-Command python -ErrorAction Stop).Path
$pyverOk = & $python -c "import sys; print('OK' if sys.version_info[:2]==(3,12) else 'BAD')"
if ($pyverOk -ne "OK") {
  throw "Python 3.12 required (found a different version at $python). Install Python 3.12 from python.org and ensure it's first on PATH."
}
Get-Command git -ErrorAction Stop | Out-Null
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
  Write-Warning "nvidia-smi not found. STT will fall back to CPU (much slower)."
}

# 2. Create venv if missing
if (-not (Test-Path ".venv")) {
  Write-Host "==> Creating .venv"
  & $python -m venv .venv
}
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"

# 3. Bootstrap pip + uv inside the venv
Write-Host "==> Bootstrapping pip + uv in .venv"
& $venvPython -m pip install --quiet --upgrade pip uv

# 4. Install host runtime + CUDA wheels + dev tools
Write-Host "==> Installing dependencies (.[host,host-cuda,dev]) -- slow first time"
& $venvPython -m uv pip install -e ".[host,host-cuda,dev]"
if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }

# 5. Fetch Kokoro voice model files into the repo root if not present
$modelUrl  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
$voicesUrl = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
foreach ($pair in @(
  @{ Url = $modelUrl;  Path = "kokoro-v1.0.onnx" },
  @{ Url = $voicesUrl; Path = "voices-v1.0.bin" }
)) {
  if (Test-Path $pair.Path) {
    Write-Host "==> $($pair.Path) already present"
  } else {
    Write-Host "==> Downloading $($pair.Path)"
    Invoke-WebRequest -Uri $pair.Url -OutFile $pair.Path -UseBasicParsing
  }
}

# 6. Open Windows firewall for the TTS port (VM -> host).
#    Default profile = Private (assumes you're on a trusted LAN).
$ttsPort = if ($env:VOICE_TTS_PORT) { $env:VOICE_TTS_PORT } else { 8002 }
$ruleName = "Claude Voice Assistant TTS (in TCP $ttsPort)"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
  Write-Host "==> Adding inbound firewall rule for TCP $ttsPort (Private profile)"
  New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $ttsPort -Profile Private | Out-Null
} else {
  Write-Host "==> Firewall rule already present: $ruleName"
}

Write-Host ""
Write-Host "==> Install complete." -ForegroundColor Green
Write-Host "    Next: set VOICE_CLAUDE_URL to the VM's LAN URL, then run:"
Write-Host "          .\scripts\verify-host.ps1"
Write-Host "          .\scripts\run-host.ps1"
