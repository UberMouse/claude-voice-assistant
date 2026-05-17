# Diagnostic check for the Windows host setup. Run after install-host.ps1.
#   PS> .\scripts\verify-host.ps1
#
# Exits 0 if everything looks good; 1 if any check fails.
#
# DEBUG-TAG: verify-host

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$script:fails = 0

function Check {
  param([string]$Label, [scriptblock]$Test)
  try {
    $result = & $Test
    if ($result -eq $false) { throw "predicate returned false" }
    Write-Host "[ OK ] $Label" -ForegroundColor Green
  } catch {
    Write-Host "[FAIL] $Label`n       $_" -ForegroundColor Red
    $script:fails++
  }
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"

Check "venv exists"               { Test-Path $venvPython }
Check "venv runs Python 3.12"     {
  $v = & $venvPython -c "import sys; print('OK' if sys.version_info[:2]==(3,12) else 'BAD')"
  $v -eq "OK"
}
Check "nvidia-smi available"      { Get-Command nvidia-smi -ErrorAction Stop | Out-Null; $true }
Check "ctranslate2 imports w/ CUDA DLLs on PATH" {
  & $venvPython -c "from host.stt.cli import _ensure_windows_cuda_dlls_on_path; _ensure_windows_cuda_dlls_on_path(); import ctranslate2"
  $LASTEXITCODE -eq 0
}
Check "kokoro model present"      { Test-Path "kokoro-v1.0.onnx" }
Check "voices file present"       { Test-Path "voices-v1.0.bin" }
Check "sounddevice can enumerate" { & $venvPython -c "import sounddevice as sd; print(len(sd.query_devices()))" | Out-Null; $LASTEXITCODE -eq 0 }

# Env checks (warnings only -- these are user-set)
if (-not $env:VOICE_CLAUDE_URL) {
  Write-Host "[WARN] VOICE_CLAUDE_URL not set (orchestrator will not reach the VM)" -ForegroundColor Yellow
}
if (-not $env:VOICE_MIC_NAME) {
  Write-Host "[WARN] VOICE_MIC_NAME not set (will use default input device)" -ForegroundColor Yellow
}

# Reachability to VM if URL is set
if ($env:VOICE_CLAUDE_URL) {
  $url = $env:VOICE_CLAUDE_URL.TrimEnd("/") + "/health"
  Check "Claude daemon reachable: $url" {
    $r = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing
    $r.StatusCode -eq 200
  }
}

if ($script:fails -gt 0) {
  Write-Host "`n$($script:fails) check(s) failed." -ForegroundColor Red
  exit 1
} else {
  Write-Host "`nAll checks passed." -ForegroundColor Green
}
