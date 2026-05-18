# Windows host setup

One-time walkthrough for getting the Windows side of the voice assistant running. The Linux VM side is covered by the main README's `nix develop` flow.

The Windows host runs: **STT server** (faster-whisper on RTX 4090), **TTS server** (Kokoro), and the **orchestrator** (mic capture + Lshift+F3 hotkey + glue). The VM runs the **Claude wrapper daemon** and the **`speak` CLI**. Two HTTP wires connect them: host → VM `POST /ask`, VM → host `POST /speak`.

## 0. Network model (bridged)

Both the Windows host and the dev VM have LAN IPs. Note them down:

- On Windows (PowerShell):

  ```powershell
  (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'Ethernet*','Wi-Fi*' -ErrorAction SilentlyContinue).IPAddress
  ```

- On the VM:

  ```bash
  hostname -I | awk '{print $1}'
  ```

You'll wire two env vars based on those:

- On Windows: `VOICE_CLAUDE_URL = http://<vm-ip>:8003`
- On the VM: `VOICE_TTS_URL = http://<host-ip>:8002`

Verify the two can ping each other before going further.

## 1. Prerequisites on Windows (one-time, manual)

If any of these aren't already on your machine:

```powershell
# Python 3.12 (from python.org), Git, Windows Terminal
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
winget install --id Microsoft.WindowsTerminal -e

# NVIDIA driver: download "Game Ready" or "Studio" driver from nvidia.com.
# Confirm: nvidia-smi runs and shows the 4090.
```

Open a *new* PowerShell after installs so the updated PATH is picked up.

## 2. Clone the repo

```powershell
mkdir $HOME\code -Force
cd $HOME\code
git clone <repo-url> claude-voice-assistant
cd claude-voice-assistant
git checkout phase1-mvp   # or main, after merge
```

## 3. Run the installer

```powershell
.\scripts\install-host.ps1
```

This is idempotent: re-run it after `git pull` and it'll only do the new work. It will:

- create `.venv` with Python 3.12
- install `.[host-cuda,dev]` (faster-whisper, kokoro-onnx, pinned CUDA wheels, etc.)
- download `kokoro-v1.0.onnx` and `voices-v1.0.bin` into the repo root if missing
- add a Windows firewall rule allowing inbound TCP **8002** on the Private profile (so the VM can POST to the TTS server)

If the firewall step fails because PowerShell isn't running as Administrator, re-launch it elevated and rerun just that section, or add the rule manually:

```powershell
New-NetFirewallRule -DisplayName "Claude Voice Assistant TTS (in TCP 8002)" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8002 -Profile Private
```

## 4. Set environment variables

For a single session:

```powershell
$env:VOICE_CLAUDE_URL = "http://192.168.x.x:8003"   # VM's LAN IP
$env:VOICE_MIC_NAME   = "Yeti"                       # substring of the mic device name
```

To persist them per user:

```powershell
[Environment]::SetEnvironmentVariable("VOICE_CLAUDE_URL", "http://192.168.x.x:8003", "User")
[Environment]::SetEnvironmentVariable("VOICE_MIC_NAME",   "Yeti", "User")
```

(Open a new PowerShell after `setx`-style edits for the change to take effect.)

List your audio devices to find the right `VOICE_MIC_NAME` substring:

```powershell
.\.venv\Scripts\python.exe -m sounddevice
```

The orchestrator does a case-insensitive *substring* match against the device name — pick the shortest unique fragment.

## 5. Verify

```powershell
.\scripts\verify-host.ps1
```

Expected output: every check `[ OK ]`. Common failures:

| Failure | Fix |
|---|---|
| `venv runs Python 3.12` fails | The system `python` is not 3.12 — install via `winget install Python.Python.3.12` and re-run `install-host.ps1`. |
| `ctranslate2 imports w/ CUDA DLLs on PATH` fails | NVIDIA wheels are missing or the DLL-path shim isn't picking them up. Re-run install, then check `.\.venv\Lib\site-packages\nvidia\` exists. |
| `kokoro model present` fails | Re-run `install-host.ps1` (it skips downloads that already exist). |
| `Claude daemon reachable` fails | Check the VM's claude daemon is running (`voice-claude-daemon` on the VM, listening on `0.0.0.0:8003`), the VM IP is correct, and no Windows/Linux firewall blocks port 8003. |

## 6. Run the host services

```powershell
.\scripts\run-host.ps1
```

Three Windows Terminal tabs open (or three PowerShell windows if you don't have WT), one per service. Watch each tab for startup. The first STT call is slow (model load); subsequent calls are fast.

Then on the VM, start the Claude daemon:

```bash
nix develop --command sh -c '. .venv/bin/activate && VOICE_CLAUDE_HOST=0.0.0.0 voice-claude-daemon'
```

`VOICE_CLAUDE_HOST=0.0.0.0` is important: the daemon must bind to all interfaces so the Windows orchestrator can reach it over the LAN.

Press **Left-Shift + F3** on the Windows side, speak within five seconds, release — you should hear a reply through the host's speakers.

## 7. Ongoing maintenance

Updates: `git pull` on Windows, re-run `.\scripts\install-host.ps1`. The install script is a no-op for everything that hasn't changed.

To stop services: close each PowerShell tab/window. There's no shared parent process.

## Env-var reference (Windows host)

| Var | Default | Purpose |
|---|---|---|
| `VOICE_STT_HOST` | `127.0.0.1` | STT server bind (local-only is fine; orchestrator is on the same host) |
| `VOICE_STT_PORT` | `8001` | STT server port |
| `VOICE_STT_MODEL` | `distil-large-v3` | faster-whisper model name |
| `VOICE_STT_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `VOICE_TTS_HOST` | `0.0.0.0` (set by `run-host.ps1`) | TTS server bind (must be non-loopback for the VM to reach it) |
| `VOICE_TTS_PORT` | `8002` | TTS server port (must match the firewall rule) |
| `VOICE_TTS_VOICE` | `af_sarah` | Kokoro voice id |
| `VOICE_CLAUDE_URL` | _required_ | URL of the VM's claude daemon, e.g. `http://192.168.x.x:8003` |
| `VOICE_HOTKEY` | `lshift+f3` | Push-to-talk key. Single key (`f3`) or chord (`lshift+f3`, `ctrl+alt+f3`). Modifier tokens: `shift`/`lshift`/`rshift`, `ctrl`/`lctrl`/`rctrl`, `alt`/`lalt`/`ralt`, `cmd`/`win` |
| `VOICE_CAPTURE_SECS` | `5` | MVP fixed-window capture length (Phase 1.5 will replace with hold-to-talk) |
| `VOICE_MIC_NAME` | _unset_ | Substring of the input device name; unset falls back to system default |

## Debug logging

Each service logs with a tag prefix. To tail everything in PowerShell (assuming you redirect each service to a file):

```powershell
Get-Content -Wait .\voice-assistant.log | Select-String "stt-server|tts-(server|queue)|audio-capture|hotkey|orchestrator|speak-cli"
```

(The corresponding daemon-side regex is `claude-daemon|speak-cli`.)
