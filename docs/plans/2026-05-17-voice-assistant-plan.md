# Voice Assistant — Implementation Plan (Phase 0 + Phase 1 MVP)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stand up a working one-shot voice loop end-to-end — press a key, speak, faster-whisper transcribes, Claude responds, Kokoro speaks the reply — running entirely on the user's NixOS dev VM as localhost services.

**Architecture:** Four cooperating services (orchestrator, STT, TTS, Claude wrapper) plus a tiny `speak` CLI Claude calls as a tool. Each service is a small FastAPI app communicating over HTTP. The orchestrator owns a state machine that wires audio capture → STT → Claude → TTS. Audio capture/playback uses `sounddevice` (PortAudio). The deployment-time split between Windows host and Linux VM is just a URL change; for MVP everything is localhost.

**Tech Stack:** Python 3.12, FastAPI, `uv` for packaging, `pytest` for tests, `faster-whisper` for STT (GPU-backed, falls back to CPU during dev), `kokoro` (kokoro-onnx) for TTS, `sounddevice` for audio I/O, `pynput` for the dev-mode hotkey, Nix flake dev shell.

**Scope:** This plan covers Phase 0 spikes (de-risking the riskiest assumptions) and Phase 1 MVP (the simplest end-to-end loop). Out of scope for this plan: NixOS-WSL packaging (Phase 2), conversational mode + VAD (Phase 3), and polish/integrations (Phase 4). Those will be planned as separate documents once Phase 0 and 1 are done.

**Conventions:**
- Every task ends with a commit. Commit messages use conventional-commits style (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
- TDD where it fits naturally (state machine, HTTP handlers, parsing). Smoke tests where it doesn't (audio devices, subprocess shelling).
- File paths are repo-relative. The repo root is `/home/taylorl/code/claude-voice-assistant/`.
- Service URLs are config-driven via env vars from day one — no hardcoded `localhost`.

---

## Phase 0: De-risking Spikes

Three things need verification before we invest in the full MVP. Each spike is timeboxed and exploratory — record findings in `docs/spikes/`, not the codebase.

### Spike A: Windows audio capture / playback sanity check

**Goal:** Confirm `sounddevice` on Windows enumerates the user's mic and speakers and round-trips audio with sub-300ms latency. Windows audio is well-trodden ground — this is a sanity check, not a full investigation.

**Steps:**

1. On the Windows host: install Python 3.12 (from python.org or `winget install Python.Python.3.12`) and `uv` (`winget install astral-sh.uv`).
2. In a scratch directory: `uv venv && uv pip install sounddevice numpy soundfile`.
3. Run a 30-line Python script: enumerate `sd.query_devices()`, list the default input and output, record 3 seconds @ 16kHz mono, write WAV, play it back.
4. Verify the user's actual mic (not a virtual cable) is the default input. If not, note the device index for later config.
5. Run a 30-second continuous recording. Verify the sample count matches `30 * 16000` within 1%.

**Acceptance:**
- `sd.query_devices()` lists the user's mic and speakers
- Round-trip audio works (you can hear your voice)
- 30s recording has no dropouts

**Output:** `docs/spikes/spike-a-windows-audio.md` with the device names/indices and any quirks (Bluetooth headset behavior, mic gain, etc.).

**Commit:** `chore(spike): Windows audio sanity check`

---

### Spike B: CUDA on Windows with faster-whisper and Kokoro

**Goal:** Confirm both models run on the RTX 4090 from a stock Windows Python install, with acceptable latency. Identify the exact PyTorch+CUDA wheel combo we'll pin.

**Steps:**

1. Confirm a recent Windows NVIDIA driver is installed (open NVIDIA Control Panel → System Information; or `nvidia-smi.exe` from PowerShell). Note driver version.
2. In the spike scratch venv from Spike A: install the CUDA-flavored PyTorch wheel for your driver's CUDA version (`uv pip install torch --index-url https://download.pytorch.org/whl/cu124` or similar). Verify with `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`.
3. `uv pip install faster-whisper`. Download `distil-large-v3`. Transcribe a 10s sample clip on GPU. Log wall-clock latency end-to-end.
4. `uv pip install kokoro-onnx`. Download the Kokoro v1 model + voices file (URLs in the kokoro-onnx README). Synthesize a 1-sentence reply. Log latency. Play it through your default output.
5. Back-to-back run: STT then TTS, watching VRAM in another PowerShell window (`while ($true) { nvidia-smi.exe; Start-Sleep 1; cls }`).

**Acceptance:**
- `torch.cuda.is_available()` returns True; the 4090 is named
- `distil-large-v3` transcribes a 10s clip in < 1s on GPU
- Kokoro synthesizes a 1-sentence reply in < 500 ms
- Combined VRAM under 8 GB

**Output:** `docs/spikes/spike-b-cuda-windows.md` with the exact NVIDIA driver version, PyTorch index URL used, model choices, latency numbers, VRAM measurements.

**Commit:** `chore(spike): CUDA-on-Windows findings`

---

### Spike C: `claude --print --resume` behavior

**Goal:** Confirm Claude Code's CLI supports the session model we want (persistent context across one-shot invocations) and measure first-token latency and any subscription-plan friction.

**Steps:**

1. `claude --print "hello, remember the number 42"` — capture stdout, time wall-clock.
2. Note the session ID (from `claude --output-format=json` or wherever it's exposed). Document where it lives.
3. `claude --print --resume <session-id> "what number did I just tell you?"` — verify it answers 42.
4. Run 10 invocations back-to-back. Note rate-limit warnings, latency drift, or 429-style errors.
5. Try `--output-format=stream-json` and parse it (we'll use this if we want streaming later).
6. Test what happens if a session ID is unknown — does it create a new one, or error?

**Acceptance:**
- `--resume` reliably persists conversation context
- First-response latency for a trivial prompt < 5s (cold) and < 3s (warm)
- No subscription-plan hard-stops in a short burst (10 invocations)
- We can extract the session ID programmatically

**Output:** `docs/spikes/spike-c-claude-print.md`. If `--print` looks unsuitable, recommend the Agent SDK fallback path (this is the design's known fallback).

**Commit:** `chore(spike): claude --print/--resume findings`

---

**Phase 0 gate:** Read all three spike docs together. If any blocks the design, update the design doc before starting Phase 1.

---

## Phase 1: MVP Vertical Slice

End state: from the dev VM, you press F8, speak "what's the capital of France", and a few seconds later your speakers say "Paris". Plus the system can take and save a voice note: "save a note that I need to email Sam tomorrow" should write a markdown file to `~/voice-assistant/notes/`.

All services run on the NixOS dev VM with localhost URLs. Conversational mode, VAD, AHK shim, and Nix packaging are deferred.

### Task 1: Project scaffolding

**Goal:** Repo layout, Python tooling, Nix dev shell, baseline `.gitignore`. No services yet.

**Files:**
- Create: `flake.nix`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `host/__init__.py`, `host/orchestrator/__init__.py`, `host/stt/__init__.py`, `host/tts/__init__.py`, `host/audio/__init__.py`
- Create: `vm/__init__.py`, `vm/claude_daemon/__init__.py`, `vm/speak/__init__.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Create: `README.md` (stub)

**Step 1: Write `flake.nix` with a dev shell**

```nix
{
  description = "Claude voice assistant";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";
  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python312
            uv
            ffmpeg
            portaudio
            pulseaudio
            pkg-config
          ];
          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.portaudio}/lib:${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
          '';
        };
      });
}
```

**Step 2: Write `pyproject.toml`**

```toml
[project]
name = "claude-voice-assistant"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "httpx>=0.27",
  "pydantic>=2.8",
  "sounddevice>=0.5",
  "numpy>=2.0",
  "soundfile>=0.12",
  "pynput>=1.7",
  "faster-whisper>=1.0",
  "kokoro-onnx>=0.3",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "respx>=0.21"]

[project.scripts]
voice-orchestrator = "host.orchestrator.cli:main"
voice-stt = "host.stt.server:main"
voice-tts = "host.tts.server:main"
voice-claude-daemon = "vm.claude_daemon.server:main"
speak = "vm.speak.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

**Step 3: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
result
result-*
*.wav
*.mp3
models/
docs/spikes/*.local.md
```

**Step 4: Create empty package files**

Just `touch` the `__init__.py` files listed above. Add to each top-level package's `__init__.py`:

```python
"""Voice assistant: <component>."""
```

**Step 5: Verify dev shell works**

Run: `nix develop --command python -c "import sys; print(sys.version)"`
Expected: prints Python 3.12.x.

Run: `nix develop --command sh -c "uv venv && uv pip install -e '.[dev]'"`
Expected: installs without errors.

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest"`
Expected: collects 0 tests, exits 5 (no tests yet — pytest exit 5 is OK for now).

**Step 6: Commit**

```bash
git add flake.nix pyproject.toml .gitignore host vm tests README.md
git commit -m "chore: scaffold repo, dev shell, packaging"
```

---

### Task 2: STT server (faster-whisper)

**Goal:** A FastAPI server exposing `POST /transcribe` that accepts WAV bytes and returns a transcript.

**Files:**
- Create: `host/stt/server.py`
- Create: `host/stt/cli.py`
- Create: `tests/host/stt/test_server.py`
- Create: `tests/fixtures/hello.wav` (a tiny known WAV — generate with espeak or grab from sample data)

**Step 1: Write the failing test**

`tests/host/stt/test_server.py`:

```python
from pathlib import Path
from fastapi.testclient import TestClient
from host.stt.server import build_app

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "hello.wav"

def test_transcribe_returns_text():
    app = build_app(model_name="tiny.en")  # smallest, fastest model for tests
    client = TestClient(app)
    with FIXTURE.open("rb") as f:
        resp = client.post("/transcribe", files={"audio": ("hello.wav", f, "audio/wav")})
    assert resp.status_code == 200
    body = resp.json()
    assert "text" in body
    assert "hello" in body["text"].lower()

def test_health():
    app = build_app(model_name="tiny.en")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
```

**Step 2: Generate the test fixture WAV**

In `tests/fixtures/`:

```bash
nix develop --command sh -c "espeak-ng -w tests/fixtures/hello.wav 'hello world'"
```

If `espeak-ng` isn't in the dev shell, add it to `flake.nix` packages and re-enter. Verify with `soxi tests/fixtures/hello.wav` (also add `sox` to dev shell if you want it).

**Step 3: Run the test to verify it fails**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/stt -v"`
Expected: FAIL with `ImportError: No module named 'host.stt.server'` or similar.

**Step 4: Write `host/stt/server.py`**

```python
"""STT HTTP server wrapping faster-whisper."""
from __future__ import annotations
import io
import logging
from functools import lru_cache
from fastapi import FastAPI, UploadFile, File, HTTPException
from faster_whisper import WhisperModel
import soundfile as sf
import numpy as np

log = logging.getLogger(__name__)

# DEBUG-TAG: stt-server
# To grep all STT debug logs: grep -E "stt-server" voice-assistant.log

def build_app(model_name: str = "distil-large-v3", device: str = "auto") -> FastAPI:
    app = FastAPI(title="voice-stt")

    @lru_cache(maxsize=1)
    def get_model() -> WhisperModel:
        log.info("stt-server: loading model %s on %s", model_name, device)
        compute_type = "float16" if device != "cpu" else "int8"
        return WhisperModel(model_name, device=device, compute_type=compute_type)

    @app.get("/health")
    def health():
        return {"ok": True, "model": model_name}

    @app.post("/transcribe")
    async def transcribe(audio: UploadFile = File(...)):
        if not audio.filename.lower().endswith((".wav", ".flac", ".mp3", ".ogg")):
            raise HTTPException(400, "unsupported audio format")
        raw = await audio.read()
        try:
            data, sr = sf.read(io.BytesIO(raw))
        except Exception as e:
            raise HTTPException(400, f"could not decode audio: {e}")
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        log.debug("stt-server: transcribing %d samples @ %dHz", len(data), sr)
        segments, _ = get_model().transcribe(data, language=None, beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("stt-server: transcribed %d chars", len(text))
        return {"text": text}

    return app
```

`host/stt/cli.py`:

```python
import os
import logging
import uvicorn
from .server import build_app

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    model = os.environ.get("VOICE_STT_MODEL", "distil-large-v3")
    device = os.environ.get("VOICE_STT_DEVICE", "auto")
    host = os.environ.get("VOICE_STT_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_STT_PORT", "8001"))
    uvicorn.run(build_app(model, device), host=host, port=port)
```

**Step 5: Run the test to verify it passes**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/stt -v"`
Expected: 2 passed. First run may be slow as the tiny model downloads.

**Step 6: Smoke-test the server**

Run: `nix develop --command sh -c ". .venv/bin/activate && VOICE_STT_MODEL=tiny.en voice-stt &" && sleep 5 && curl -F audio=@tests/fixtures/hello.wav http://127.0.0.1:8001/transcribe && pkill -f voice-stt`
Expected: JSON `{"text": "hello world"}` (or similar).

**Step 7: Commit**

```bash
git add host/stt tests/host/stt tests/fixtures/hello.wav
git commit -m "feat(stt): HTTP server wrapping faster-whisper"
```

**Debug logging tag:** `stt-server`. Grep regex: `stt-server`.

---

### Task 3: TTS server (Kokoro) with playback queue

**Goal:** A FastAPI server exposing `POST /speak {text}` that synthesizes audio and plays it on the local default output. Multiple rapid requests are queued and played sequentially.

**Files:**
- Create: `host/tts/server.py`
- Create: `host/tts/cli.py`
- Create: `host/tts/queue.py`
- Create: `tests/host/tts/test_queue.py`
- Create: `tests/host/tts/test_server.py`

**Step 1: Write a failing test for the queue (synthesis logic mocked)**

`tests/host/tts/test_queue.py`:

```python
import asyncio
import pytest
from host.tts.queue import PlaybackQueue

@pytest.mark.asyncio
async def test_queue_serializes_playback():
    order = []
    async def fake_play(text: str):
        order.append(("start", text))
        await asyncio.sleep(0.05)
        order.append(("end", text))
    q = PlaybackQueue(play_fn=fake_play)
    await q.start()
    await q.enqueue("a")
    await q.enqueue("b")
    await q.drain()
    await q.stop()
    assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]
```

**Step 2: Run, see it fail**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/tts -v"`
Expected: FAIL — module missing.

**Step 3: Implement the queue**

`host/tts/queue.py`:

```python
"""Serialized playback queue."""
from __future__ import annotations
import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

# DEBUG-TAG: tts-queue
# Grep: grep -E "tts-(queue|server)"

class PlaybackQueue:
    def __init__(self, play_fn: Callable[[str], Awaitable[None]]):
        self._play = play_fn
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._run())

    async def enqueue(self, text: str) -> None:
        log.debug("tts-queue: enqueue %r (size=%d)", text[:40], self._q.qsize())
        await self._q.put(text)

    async def drain(self) -> None:
        await self._q.join()

    async def stop(self) -> None:
        self._stopping = True
        await self._q.put("")  # poison pill
        if self._worker:
            await self._worker

    async def _run(self) -> None:
        while True:
            text = await self._q.get()
            try:
                if self._stopping and text == "":
                    return
                await self._play(text)
            except Exception:
                log.exception("tts-queue: play failed")
            finally:
                self._q.task_done()
```

**Step 4: Run the queue test — should pass**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/tts/test_queue.py -v"`
Expected: PASS.

**Step 5: Write a failing test for the server**

`tests/host/tts/test_server.py`:

```python
from fastapi.testclient import TestClient
from host.tts.server import build_app

class FakeSynth:
    def __init__(self):
        self.calls = []
    async def play(self, text: str):
        self.calls.append(text)

def test_speak_enqueues():
    synth = FakeSynth()
    app = build_app(play_fn=synth.play)
    client = TestClient(app)
    resp = client.post("/speak", json={"text": "hello"})
    assert resp.status_code == 202
    # The TestClient drives lifespan via context manager; use it for drain:
    with TestClient(app) as c:
        c.post("/speak", json={"text": "one"})
        c.post("/speak", json={"text": "two"})
    # By the time the context manager exits, the lifespan shuts the queue down,
    # which drains pending items.
    assert "one" in synth.calls and "two" in synth.calls

def test_health():
    app = build_app(play_fn=lambda t: None)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
```

**Step 6: Run, see it fail**

Expected: FAIL — `build_app` missing.

**Step 7: Implement the TTS server**

`host/tts/server.py`:

```python
"""TTS HTTP server: Kokoro synthesis with a serialized playback queue."""
from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Awaitable, Callable
from fastapi import FastAPI
from pydantic import BaseModel
import numpy as np
import sounddevice as sd
from .queue import PlaybackQueue

log = logging.getLogger(__name__)

# DEBUG-TAG: tts-server
# Grep: grep -E "tts-(queue|server)"

class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None

def _default_kokoro_play(voice_default: str):
    from kokoro_onnx import Kokoro  # local import: heavy

    model_path = os.environ.get("VOICE_TTS_MODEL", "kokoro-v1.0.onnx")
    voices_path = os.environ.get("VOICE_TTS_VOICES", "voices-v1.0.bin")
    k = Kokoro(model_path, voices_path)

    async def play(text: str) -> None:
        log.info("tts-server: synth %r", text[:60])
        samples, sr = k.create(text, voice=voice_default, speed=1.0)
        # sounddevice's play is blocking when wait=True; run in thread
        await asyncio.to_thread(sd.play, samples, sr, blocking=True)

    return play

def build_app(play_fn: Callable[[str], Awaitable[None]] | None = None,
              voice_default: str = "af_sarah") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        fn = play_fn or _default_kokoro_play(voice_default)
        q = PlaybackQueue(fn)
        await q.start()
        app.state.q = q
        try:
            yield
        finally:
            await q.drain()
            await q.stop()

    app = FastAPI(title="voice-tts", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/speak", status_code=202)
    async def speak(req: SpeakRequest):
        await app.state.q.enqueue(req.text)
        return {"queued": True}

    return app
```

`host/tts/cli.py`:

```python
import logging, os, uvicorn
from .server import build_app

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("VOICE_TTS_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_TTS_PORT", "8002"))
    voice = os.environ.get("VOICE_TTS_VOICE", "af_sarah")
    uvicorn.run(build_app(voice_default=voice), host=host, port=port)
```

**Step 8: Run all tts tests**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/tts -v"`
Expected: all pass.

**Step 9: Smoke-test playback manually** *(skip if running in a headless dev env — verify on the host instead during integration)*

Run: `nix develop --command sh -c ". .venv/bin/activate && voice-tts &" && sleep 3 && curl -X POST -H "content-type: application/json" -d '{"text":"hello from kokoro"}' http://127.0.0.1:8002/speak && sleep 5 && pkill -f voice-tts`
Expected: hear "hello from kokoro" on the speakers.

**Step 10: Commit**

```bash
git add host/tts tests/host/tts
git commit -m "feat(tts): HTTP server with kokoro synth + playback queue"
```

**Debug logging tag:** `tts-server`, `tts-queue`. Grep regex: `tts-(server|queue)`.

---

### Task 4: Audio capture utility

**Goal:** A small library function that records mic input on demand and returns a WAV-encoded bytes buffer.

**Files:**
- Create: `host/audio/capture.py`
- Create: `tests/host/audio/test_capture.py`

**Step 1: Failing test (capture via injected stream)**

`tests/host/audio/test_capture.py`:

```python
import io
import numpy as np
import soundfile as sf
from host.audio.capture import encode_wav

def test_encode_wav_roundtrip():
    samples = (np.random.rand(16000) * 2 - 1).astype(np.float32)
    blob = encode_wav(samples, sample_rate=16000)
    data, sr = sf.read(io.BytesIO(blob))
    assert sr == 16000
    assert data.shape == (16000,)
    assert np.allclose(data, samples, atol=1e-3)
```

**Step 2: Run, see it fail**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/audio -v"`
Expected: FAIL — module missing.

**Step 3: Implement**

`host/audio/capture.py`:

```python
"""Mic capture utility. Uses sounddevice (PortAudio)."""
from __future__ import annotations
import io
import logging
import threading
import time
from typing import Optional
import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger(__name__)

# DEBUG-TAG: audio-capture
# Grep: grep -E "audio-capture"

DEFAULT_SAMPLE_RATE = 16000

def encode_wav(samples: np.ndarray, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, subtype="FLOAT", format="WAV")
    return buf.getvalue()

class Recorder:
    """Push-to-talk recorder. start() opens stream, stop() returns the captured audio."""

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._chunks: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("audio-capture: stream status %s", status)
        with self._lock:
            self._chunks.append(indata.copy().flatten())

    def start(self) -> None:
        log.info("audio-capture: start sr=%d", self.sample_rate)
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        assert self._stream is not None, "start() must be called first"
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            samples = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        log.info("audio-capture: stop, %d samples (%.2fs)", len(samples), len(samples)/self.sample_rate)
        return samples
```

**Step 4: Run tests, pass**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/audio -v"`
Expected: PASS.

**Step 5: Manual smoke test** *(skipped in CI; do on a machine with a mic)*

Run a 5-line script that does `r = Recorder(); r.start(); time.sleep(3); samples = r.stop(); sf.write('/tmp/cap.wav', samples, 16000)` and play it back with `paplay /tmp/cap.wav`. Confirm your voice comes through.

**Step 6: Commit**

```bash
git add host/audio tests/host/audio
git commit -m "feat(audio): mic capture utility with WAV encoding"
```

**Debug logging tag:** `audio-capture`. Grep regex: `audio-capture`.

---

### Task 5: Hotkey trigger (dev-mode, pynput)

**Goal:** Listen for a configurable hotkey, fire press/release callbacks. AutoHotkey/Win32 stuff comes later in Phase 2.

**Files:**
- Create: `host/orchestrator/hotkey.py`
- Create: `tests/host/orchestrator/test_hotkey.py`

**Step 1: Failing test — drive callbacks directly**

`tests/host/orchestrator/test_hotkey.py`:

```python
from host.orchestrator.hotkey import HotkeyDispatcher, PressKind

def test_short_press_classification():
    events = []
    d = HotkeyDispatcher(short_press_ms=300, on_event=events.append)
    d._on_press(t_ms=0)
    d._on_release(t_ms=100)
    assert events == [PressKind.SHORT]

def test_long_press_classification():
    events = []
    d = HotkeyDispatcher(short_press_ms=300, on_event=events.append)
    d._on_press(t_ms=0)
    d._on_release(t_ms=500)
    assert events == [PressKind.LONG]
```

**Step 2: Run, fail**

Expected: FAIL — module missing.

**Step 3: Implement**

`host/orchestrator/hotkey.py`:

```python
"""Hotkey dispatcher. Wraps pynput at runtime, but the classification is pure."""
from __future__ import annotations
import enum
import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

# DEBUG-TAG: hotkey
# Grep: grep -E "hotkey"

class PressKind(str, enum.Enum):
    SHORT = "short"
    LONG = "long"

class HotkeyDispatcher:
    def __init__(self, short_press_ms: int = 300, on_event: Optional[Callable[[PressKind], None]] = None):
        self._short_ms = short_press_ms
        self._on_event = on_event or (lambda _: None)
        self._press_ts_ms: Optional[int] = None
        self._lock = threading.Lock()

    def _on_press(self, t_ms: int) -> None:
        with self._lock:
            if self._press_ts_ms is None:
                self._press_ts_ms = t_ms
                log.debug("hotkey: press at %d", t_ms)

    def _on_release(self, t_ms: int) -> None:
        with self._lock:
            if self._press_ts_ms is None:
                return
            held = t_ms - self._press_ts_ms
            self._press_ts_ms = None
        kind = PressKind.LONG if held >= self._short_ms else PressKind.SHORT
        log.info("hotkey: %s press, held=%dms", kind, held)
        self._on_event(kind)

def run_pynput(key: str, dispatcher: HotkeyDispatcher) -> None:
    """Blocking pynput loop. Maps key (e.g. 'f8') to the dispatcher."""
    from pynput import keyboard

    target_key = getattr(keyboard.Key, key.lower(), None)
    if target_key is None:
        raise ValueError(f"unknown key: {key}")

    def now_ms() -> int:
        return int(time.perf_counter() * 1000)

    def on_press(k):
        if k == target_key:
            dispatcher._on_press(now_ms())

    def on_release(k):
        if k == target_key:
            dispatcher._on_release(now_ms())

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()
```

**Step 4: Run tests, pass**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/orchestrator/test_hotkey.py -v"`
Expected: PASS.

**Step 5: Commit**

```bash
git add host/orchestrator/hotkey.py tests/host/orchestrator/test_hotkey.py
git commit -m "feat(orchestrator): hotkey dispatcher with short/long classification"
```

**Debug logging tag:** `hotkey`. Grep regex: `hotkey`.

---

### Task 6: Orchestrator state machine (one-shot only)

**Goal:** Wire hotkey + capture + STT client + Claude wrapper client + TTS client. Conversational mode is skipped here — long-press just behaves like a short press for MVP, with a TODO. State machine is testable in isolation by injecting all collaborators.

**Files:**
- Create: `host/orchestrator/state.py`
- Create: `host/orchestrator/clients.py`
- Create: `host/orchestrator/runner.py`
- Create: `host/orchestrator/cli.py`
- Create: `tests/host/orchestrator/test_state.py`

**Step 1: Failing test for the state machine**

`tests/host/orchestrator/test_state.py`:

```python
import asyncio
import pytest
from host.orchestrator.state import OneShotMachine

class FakeRecorder:
    def __init__(self): self.started = False; self.samples = b"audio"
    def start(self): self.started = True
    def stop(self): return self.samples

class FakeSttClient:
    async def transcribe(self, audio_bytes): return "what time is it"

class FakeClaudeClient:
    async def ask(self, text, mode): self.last = (text, mode); return None

class FakeTtsClient:
    async def health(self): return True

@pytest.mark.asyncio
async def test_oneshot_happy_path():
    rec, stt, claude, tts = FakeRecorder(), FakeSttClient(), FakeClaudeClient(), FakeTtsClient()
    m = OneShotMachine(recorder=rec, stt=stt, claude=claude, tts=tts)
    await m.on_press()
    assert rec.started
    await m.on_release()
    assert claude.last == ("what time is it", "oneshot")
    assert m.state == "idle"
```

**Step 2: Run, fail**

Expected: FAIL — module missing.

**Step 3: Implement state machine**

`host/orchestrator/state.py`:

```python
"""One-shot orchestrator state machine."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Protocol, Literal

log = logging.getLogger(__name__)

# DEBUG-TAG: orchestrator
# Grep: grep -E "orchestrator"

class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> "object": ...  # returns raw samples or bytes

class SttClient(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> str: ...

class ClaudeClient(Protocol):
    async def ask(self, text: str, mode: Literal["oneshot", "conversational"]) -> None: ...

class TtsClient(Protocol):
    async def health(self) -> bool: ...

State = Literal["idle", "recording", "transcribing", "awaiting_claude"]

@dataclass
class OneShotMachine:
    recorder: Recorder
    stt: SttClient
    claude: ClaudeClient
    tts: TtsClient
    state: State = "idle"

    async def on_press(self) -> None:
        if self.state != "idle":
            log.warning("orchestrator: press ignored in state %s", self.state)
            return
        self.state = "recording"
        log.info("orchestrator: -> recording")
        self.recorder.start()

    async def on_release(self) -> None:
        if self.state != "recording":
            return
        audio = self.recorder.stop()
        # FakeRecorder may return bytes; real recorder returns numpy. Caller adapts.
        audio_bytes = audio if isinstance(audio, (bytes, bytearray)) else _encode(audio)
        self.state = "transcribing"
        log.info("orchestrator: -> transcribing (%d bytes)", len(audio_bytes))
        text = await self.stt.transcribe(audio_bytes)
        log.info("orchestrator: transcript=%r", text[:80])
        if not text.strip():
            log.info("orchestrator: empty transcript, back to idle")
            self.state = "idle"
            return
        self.state = "awaiting_claude"
        await self.claude.ask(text, mode="oneshot")
        self.state = "idle"
        log.info("orchestrator: -> idle")

def _encode(samples) -> bytes:
    # Lazy import to keep state.py thin
    from host.audio.capture import encode_wav
    return encode_wav(samples)
```

**Step 4: Run, pass**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/host/orchestrator/test_state.py -v"`
Expected: PASS.

**Step 5: Implement HTTP clients**

`host/orchestrator/clients.py`:

```python
"""Thin HTTP clients for STT, TTS, and the Claude VM daemon."""
from __future__ import annotations
import logging
import httpx

log = logging.getLogger(__name__)

class SttHttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def transcribe(self, audio_bytes: bytes) -> str:
        r = await self._client.post(
            f"{self._base}/transcribe",
            files={"audio": ("clip.wav", audio_bytes, "audio/wav")},
        )
        r.raise_for_status()
        return r.json()["text"]

    async def aclose(self): await self._client.aclose()

class ClaudeHttpClient:
    def __init__(self, base_url: str, timeout: float = 300.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def ask(self, text: str, mode: str = "oneshot") -> None:
        r = await self._client.post(f"{self._base}/ask", json={"text": text, "mode": mode})
        r.raise_for_status()

    async def aclose(self): await self._client.aclose()

class TtsHttpClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10.0)

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{self._base}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self): await self._client.aclose()
```

**Step 6: Runner that wires everything together**

`host/orchestrator/runner.py`:

```python
"""Run the orchestrator: hotkey -> state machine."""
from __future__ import annotations
import asyncio
import logging
import os
from .state import OneShotMachine
from .hotkey import HotkeyDispatcher, PressKind, run_pynput
from .clients import SttHttpClient, ClaudeHttpClient, TtsHttpClient
from host.audio.capture import Recorder

log = logging.getLogger(__name__)

async def amain():
    stt_url    = os.environ.get("VOICE_STT_URL",    "http://127.0.0.1:8001")
    tts_url    = os.environ.get("VOICE_TTS_URL",    "http://127.0.0.1:8002")
    claude_url = os.environ.get("VOICE_CLAUDE_URL", "http://127.0.0.1:8003")
    hotkey     = os.environ.get("VOICE_HOTKEY",     "f8")

    rec = Recorder()
    stt = SttHttpClient(stt_url)
    tts = TtsHttpClient(tts_url)
    claude = ClaudeHttpClient(claude_url)
    machine = OneShotMachine(recorder=rec, stt=stt, claude=claude, tts=tts)

    loop = asyncio.get_event_loop()
    def on_kind(kind: PressKind):
        if kind == PressKind.SHORT:
            asyncio.run_coroutine_threadsafe(_press_release_cycle(machine), loop)
        else:
            log.info("orchestrator: long-press received (conversational mode not implemented yet)")
            asyncio.run_coroutine_threadsafe(_press_release_cycle(machine), loop)

    # For dev MVP we wrap pynput's press/release into a fake "tap" — record on press, stop on release.
    # See README for the proper press/release wiring once we want long-press semantics.
    dispatcher = HotkeyDispatcher(on_event=on_kind)
    log.info("orchestrator: listening on key %s", hotkey)
    await asyncio.to_thread(run_pynput, hotkey, dispatcher)

async def _press_release_cycle(machine: OneShotMachine):
    """For MVP we simulate a press/release tied to the HotkeyDispatcher firing post-release.
    Capture for a default window of N seconds. A future iteration moves this to true press/release semantics."""
    await machine.on_press()
    await asyncio.sleep(float(os.environ.get("VOICE_CAPTURE_SECS", "5")))
    await machine.on_release()
```

Note: the MVP hotkey wiring records for a fixed `VOICE_CAPTURE_SECS` window after the keypress. True hold-to-talk semantics (record while held, stop on release) is a Phase 1.5 improvement once we have something working. Add a TODO comment in the file.

`host/orchestrator/cli.py`:

```python
import asyncio, logging
from .runner import amain

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(amain())
```

**Step 7: Commit**

```bash
git add host/orchestrator tests/host/orchestrator
git commit -m "feat(orchestrator): one-shot state machine + HTTP clients + runner"
```

**Debug logging tag:** `orchestrator`. Grep regex: `orchestrator`.

---

### Task 7: Claude wrapper daemon (VM)

**Goal:** A FastAPI service that turns `POST /ask` into a subprocess call to `claude --print --resume <session-id>`. Persists session ID across requests in `~/.local/state/voice-assistant/session-id`.

**Files:**
- Create: `vm/claude_daemon/server.py`
- Create: `vm/claude_daemon/session.py`
- Create: `vm/claude_daemon/cli.py`
- Create: `tests/vm/claude_daemon/test_server.py`
- Create: `tests/vm/claude_daemon/test_session.py`

**Step 1: Failing test for session persistence**

`tests/vm/claude_daemon/test_session.py`:

```python
import os
from pathlib import Path
from vm.claude_daemon.session import SessionStore

def test_session_read_write(tmp_path):
    store = SessionStore(tmp_path / "sess")
    assert store.read() is None
    store.write("abc-123")
    assert store.read() == "abc-123"

def test_session_corrupt_file_returns_none(tmp_path):
    p = tmp_path / "sess"
    p.write_text("")
    store = SessionStore(p)
    assert store.read() is None
```

**Step 2: Run, fail**

Expected: FAIL.

**Step 3: Implement session store**

`vm/claude_daemon/session.py`:

```python
"""Persist Claude session IDs across requests."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon
# Grep: grep -E "claude-daemon"

class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> Optional[str]:
        if not self.path.exists():
            return None
        txt = self.path.read_text().strip()
        return txt or None

    def write(self, session_id: str) -> None:
        self.path.write_text(session_id + "\n")
        log.info("claude-daemon: session id persisted: %s", session_id)
```

**Step 4: Failing test for the server (subprocess mocked)**

`tests/vm/claude_daemon/test_server.py`:

```python
import asyncio
import json
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from vm.claude_daemon.server import build_app
from vm.claude_daemon.session import SessionStore

class FakeRunner:
    def __init__(self):
        self.calls = []
        self.next_session_id = "new-sid"
        self.next_stdout = json.dumps({"session_id": "new-sid", "result": "ok"})
    async def run(self, text, session_id):
        self.calls.append((text, session_id))
        return self.next_stdout

def test_ask_calls_runner_and_persists_session(tmp_path):
    runner = FakeRunner()
    store = SessionStore(tmp_path / "sid")
    app = build_app(runner=runner, store=store)
    client = TestClient(app)
    r = client.post("/ask", json={"text": "hi", "mode": "oneshot"})
    assert r.status_code == 200
    assert runner.calls == [("hi", None)]
    assert store.read() == "new-sid"
    # Second call reuses session
    client.post("/ask", json={"text": "again", "mode": "oneshot"})
    assert runner.calls[-1] == ("again", "new-sid")
```

**Step 5: Run, fail**

Expected: FAIL.

**Step 6: Implement runner + server**

`vm/claude_daemon/server.py`:

```python
"""Claude wrapper daemon. POST /ask -> shells claude --print."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Optional, Protocol
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .session import SessionStore

log = logging.getLogger(__name__)

# DEBUG-TAG: claude-daemon

class AskRequest(BaseModel):
    text: str
    mode: str = "oneshot"  # "oneshot" | "conversational" — same wiring for MVP

class Runner(Protocol):
    async def run(self, text: str, session_id: Optional[str]) -> str: ...

class ClaudeSubprocessRunner:
    def __init__(self, workdir: Path, binary: str = "claude"):
        self.workdir = Path(workdir)
        self.binary = binary

    async def run(self, text: str, session_id: Optional[str]) -> str:
        cmd = [self.binary, "--print", "--output-format=json"]
        if session_id:
            cmd += ["--resume", session_id]
        cmd += ["--", text]
        log.info("claude-daemon: %s (cwd=%s)", " ".join(shlex.quote(a) for a in cmd), self.workdir)
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(self.workdir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error("claude-daemon: claude exited %d: %s", proc.returncode, stderr.decode()[:400])
            raise HTTPException(502, f"claude failed: {stderr.decode()[:200]}")
        return stdout.decode()

def build_app(runner: Runner, store: SessionStore) -> FastAPI:
    app = FastAPI(title="voice-claude-daemon")

    @app.get("/health")
    def health():
        return {"ok": True, "session": store.read()}

    @app.post("/ask")
    async def ask(req: AskRequest):
        sid = store.read()
        out = await runner.run(req.text, sid)
        # Best-effort: parse JSON output to capture the new session id
        try:
            obj = json.loads(out)
            new_sid = obj.get("session_id")
            if new_sid:
                store.write(new_sid)
        except json.JSONDecodeError:
            log.warning("claude-daemon: could not parse JSON output")
        return {"ok": True}

    return app
```

`vm/claude_daemon/cli.py`:

```python
import os, logging, uvicorn
from pathlib import Path
from .server import build_app, ClaudeSubprocessRunner
from .session import SessionStore

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    workdir = Path(os.environ.get("VOICE_WORKSPACE", str(Path.home() / "voice-assistant")))
    workdir.mkdir(parents=True, exist_ok=True)
    state_dir = Path(os.environ.get("VOICE_STATE_DIR", str(Path.home() / ".local/state/voice-assistant")))
    runner = ClaudeSubprocessRunner(workdir=workdir)
    store = SessionStore(state_dir / "session-id")
    host = os.environ.get("VOICE_CLAUDE_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_CLAUDE_PORT", "8003"))
    uvicorn.run(build_app(runner, store), host=host, port=port)
```

**Step 7: Run all daemon tests**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/vm/claude_daemon -v"`
Expected: PASS.

**Step 8: Commit**

```bash
git add vm/claude_daemon tests/vm/claude_daemon
git commit -m "feat(claude-daemon): HTTP wrapper shelling claude --print --resume"
```

**Debug logging tag:** `claude-daemon`. Grep regex: `claude-daemon`.

---

### Task 8: `speak` CLI

**Goal:** A tiny CLI on the VM's `$PATH` that Claude calls as a tool. `speak "hello"` POSTs to the TTS server.

**Files:**
- Create: `vm/speak/cli.py`
- Create: `tests/vm/speak/test_cli.py`

**Step 1: Failing test (mocked HTTP)**

`tests/vm/speak/test_cli.py`:

```python
import respx, httpx, pytest
from vm.speak.cli import speak

@respx.mock
def test_speak_posts_text():
    route = respx.post("http://127.0.0.1:8002/speak").mock(return_value=httpx.Response(202, json={"queued": True}))
    rc = speak("hello world", url="http://127.0.0.1:8002")
    assert rc == 0
    assert route.called
    assert route.calls.last.request.read() == b'{"text": "hello world"}'

@respx.mock
def test_speak_handles_failure():
    respx.post("http://127.0.0.1:8002/speak").mock(return_value=httpx.Response(500))
    rc = speak("hi", url="http://127.0.0.1:8002")
    assert rc != 0
```

**Step 2: Run, fail**

Expected: FAIL.

**Step 3: Implement**

`vm/speak/cli.py`:

```python
"""`speak <text>` — Claude calls this as a tool to speak via the host TTS server."""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import httpx

log = logging.getLogger(__name__)

# DEBUG-TAG: speak-cli
# Grep: grep -E "speak-cli"

def speak(text: str, url: str | None = None, timeout: float = 5.0) -> int:
    url = url or os.environ.get("VOICE_TTS_URL", "http://127.0.0.1:8002")
    try:
        r = httpx.post(f"{url.rstrip('/')}/speak", json={"text": text}, timeout=timeout)
        r.raise_for_status()
        log.info("speak-cli: queued %r", text[:60])
        return 0
    except Exception as e:
        log.error("speak-cli: failed: %s", e)
        return 1

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Speak text via the voice-assistant TTS server")
    ap.add_argument("text", help="Text to speak. Use quotes for multi-word strings.")
    ap.add_argument("--url", default=None, help="TTS server URL (defaults to $VOICE_TTS_URL or localhost:8002)")
    args = ap.parse_args()
    sys.exit(speak(args.text, url=args.url))
```

**Step 4: Run, pass**

Run: `nix develop --command sh -c ". .venv/bin/activate && pytest tests/vm/speak -v"`
Expected: PASS.

**Step 5: Commit**

```bash
git add vm/speak tests/vm/speak
git commit -m "feat(speak): CLI tool POSTing to the TTS server"
```

**Debug logging tag:** `speak-cli`. Grep regex: `speak-cli`.

---

### Task 9: Voice workspace template + runtime CLAUDE.md

**Goal:** Ship a `vm/workspace-template/` directory that becomes `~/voice-assistant/` on first daemon run, including the runtime `CLAUDE.md` that tells Claude how to behave in voice mode.

**Files:**
- Create: `vm/workspace_template/CLAUDE.md`
- Create: `vm/workspace_template/notes/.gitkeep`
- Create: `vm/workspace_template/.claude/settings.json`
- Modify: `vm/claude_daemon/cli.py` — copy template on first start if workspace doesn't exist

**Step 1: Write `vm/workspace_template/CLAUDE.md`**

```markdown
# Voice assistant runtime

You are running inside a voice assistant. Each user message is a transcript from speech-to-text — assume punctuation and proper nouns may be wrong.

## How to respond

- **Use the `speak` CLI to talk to the user.** Plain stdout is NOT heard. Example:
  `speak "Saved that note."`
- Keep spoken replies short. Voice is slow to listen to. Default to one sentence; offer detail only if asked.
- For multi-step work, you may `speak` a progress update mid-task (e.g. `speak "Looking that up..."`), then `speak` the answer at the end. Use sparingly.
- If you just performed an action that doesn't need a verbal response (e.g. saved a note), `speak` a 2-3 word confirmation: `speak "Noted."`
- If you can't help, say so briefly. Don't apologize at length.

## Tools available

- `speak <text>` — speak text on the host. Always at least once per response.
- File tools — read/write within `~/voice-assistant/` only.
- `WebFetch` — fetch URLs for research.
- Whatever MCP servers are configured in `.claude/settings.json`.

## Notes

- Save user-requested notes as markdown in `~/voice-assistant/notes/`, filename `YYYY-MM-DD-<slug>.md`.
- When the user asks "what did I say about X", grep `~/voice-assistant/notes/`.
```

**Step 2: Write `vm/workspace_template/.claude/settings.json`**

```json
{
  "permissions": {
    "allow": [
      "Bash(speak:*)",
      "Read(~/voice-assistant/**)",
      "Write(~/voice-assistant/**)",
      "Edit(~/voice-assistant/**)",
      "WebFetch"
    ]
  }
}
```

(Adjust to match the user's actual settings.json schema if it has diverged. The principle: no permission prompts during voice operation — they'd block silently.)

**Step 3: Modify `vm/claude_daemon/cli.py` to bootstrap the workspace**

Add to `main()` just before `runner = ...`:

```python
import shutil
from importlib.resources import files
template = files("vm.workspace_template")
if not (workdir / "CLAUDE.md").exists():
    log.info("claude-daemon: bootstrapping workspace at %s", workdir)
    for src in template.iterdir():
        dst = workdir / src.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy(src, dst)
```

Also ensure `vm/workspace_template/__init__.py` exists (empty) so `importlib.resources` finds it. Add `[tool.hatch.build.targets.wheel.force-include]` entries to `pyproject.toml` if hatchling doesn't include the template by default.

**Step 4: Smoke test bootstrap**

Run: `nix develop --command sh -c ". .venv/bin/activate && VOICE_WORKSPACE=/tmp/vw VOICE_CLAUDE_PORT=18003 voice-claude-daemon &" && sleep 2 && ls /tmp/vw && pkill -f voice-claude-daemon`
Expected: `CLAUDE.md` and `notes/` appear in `/tmp/vw`.

**Step 5: Commit**

```bash
git add vm/workspace_template vm/claude_daemon/cli.py pyproject.toml
git commit -m "feat(workspace): runtime CLAUDE.md template and bootstrap"
```

---

### Task 10: End-to-end smoke test + dev launcher

**Goal:** Single script `scripts/dev.sh` that starts all four services in a tmux session. Manual smoke test documented.

**Files:**
- Create: `scripts/dev.sh`
- Create: `docs/smoke-test.md`

**Step 1: Write the launcher**

`scripts/dev.sh`:

```bash
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
```

Make executable: `chmod +x scripts/dev.sh`.

**Step 2: Write `docs/smoke-test.md`**

```markdown
# MVP smoke test

Prerequisites:
- Phase 0 spikes passed (or at least: faster-whisper runs, kokoro runs, claude --print works).
- `claude` is on `$PATH` and authenticated.
- Mic and speakers work in the current env (`parecord/paplay` round-trip succeeds).

Run:

```bash
./scripts/dev.sh
tmux attach -t voice-dev
```

Cycle through windows (Ctrl-b n) and verify each service started without error.

Manual tests (run with focus on a window that won't swallow F8):

1. **Q&A**: Press F8. Within 5s say "what's the capital of France". Wait. You should hear "Paris" (or a brief similar reply).
2. **Note**: Press F8. Say "save a note that I need to email Sam tomorrow". Confirm a file appears in `~/voice-assistant/notes/`.
3. **Recall**: Press F8. Say "what notes did I save today". Should describe the note.

Expected end-to-end latency budget:
- Mic stop -> Claude first speak: < 4s with distil-large-v3 STT on GPU
- Total Q&A turn: 3-7s on GPU; double that on CPU
```

**Step 3: Commit**

```bash
git add scripts/dev.sh docs/smoke-test.md
git commit -m "chore: dev launcher + smoke test docs"
```

---

### Task 11: README

**Goal:** A README that gets a new dev unblocked.

**Files:**
- Modify: `README.md`

**Step 1: Replace the stub README**

`README.md`:

```markdown
# Claude Voice Assistant

A push-to-talk voice interface to Claude Code.

## Status

- Phase 0 spikes: see `docs/spikes/`
- Phase 1 MVP: see `docs/plans/2026-05-17-voice-assistant-plan.md`
- Design: see `docs/plans/2026-05-17-voice-assistant-design.md`

## Quick start (dev)

```bash
nix develop
uv venv && uv pip install -e '.[dev]'
. .venv/bin/activate
./scripts/dev.sh
tmux attach -t voice-dev
```

Press F8 and speak. See `docs/smoke-test.md`.

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
| `VOICE_HOTKEY` | `f8` | orchestrator |
| `VOICE_CAPTURE_SECS` | `5` | orchestrator (MVP — fixed capture window) |

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
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with quickstart, env vars, debug tags"
```

---

## Phase 1 exit criteria

Before declaring Phase 1 done:

1. All pytest tests pass: `nix develop --command sh -c ". .venv/bin/activate && pytest"`
2. Smoke test in `docs/smoke-test.md` passes end-to-end.
3. The three sample voice flows (Q&A, save note, recall note) all work.
4. Debug logs from each component are greppable with the regex in the README.
5. Phase 0 spike findings are committed and any blockers addressed in the design doc.

## Out of scope (planned for later)

- **Phase 1.5 polish:** true hold-to-talk semantics (record while held, not fixed window). Confirmation tones. Streaming JSON output from Claude (so we can speak progress before final exit).
- **Phase 2 — Windows-native packaging and deploy:** `scripts/install-host.ps1` that creates a Windows venv, installs deps, downloads model weights, registers services (Task Scheduler vs NSSM — TBD); `scripts/verify-host.ps1` sanity check; deploy/update docs.
- **Phase 3 — Conversational mode:** Silero VAD, multi-turn state machine, exit conditions (silence timeout, second tap, Claude signaling end).
- **Phase 4 — Polish + integrations:** barge-in / TTS ducking, wake-word (openWakeWord), MCP integrations (calendar, email, GitHub PRs), voice tuning.

## Debug logging summary

All MVP components use stdlib logging with a one-tag prefix per component:

| Component | Tag | File |
|---|---|---|
| STT server | `stt-server` | `host/stt/server.py` |
| TTS server | `tts-server` | `host/tts/server.py` |
| TTS queue | `tts-queue` | `host/tts/queue.py` |
| Audio capture | `audio-capture` | `host/audio/capture.py` |
| Hotkey | `hotkey` | `host/orchestrator/hotkey.py` |
| Orchestrator | `orchestrator` | `host/orchestrator/state.py` |
| Claude daemon | `claude-daemon` | `vm/claude_daemon/server.py`, `vm/claude_daemon/session.py` |
| Speak CLI | `speak-cli` | `vm/speak/cli.py` |

**Combined grep regex:** `(stt-server|tts-(server|queue)|audio-capture|hotkey|orchestrator|claude-daemon|speak-cli)`
