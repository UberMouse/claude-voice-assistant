# Spike B: CUDA on Windows with faster-whisper and Kokoro — findings

**Date:** 2026-05-17
**Result:** ✅ Pass with caveat. STT on GPU as planned. TTS on CPU (good enough, simpler).

## Hardware / driver

- RTX 4090, 24 GB VRAM, ~5 GB in use by host desktop at idle
- NVIDIA driver **596.21**, supports up to CUDA 13.2
- Python 3.12.10, uv 0.11.14

## STT — faster-whisper on GPU ✅

**Setup:**

```
uv pip install faster-whisper
uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12   # cuda-runtime came transitively
```

CTranslate2 sees the device: `ctranslate2.get_cuda_device_count() == 1`.

**Measured (RTX 4090, `distil-large-v3`, fp16):**
- Model load: 2.0s (cold), <0.5s (cached)
- 3-second clip transcription: **288 ms wall**
- Transcript quality: clean ("Test, test." for the user's recorded test phrase)

**Critical Windows-only quirk:** ctranslate2's native loader does NOT honor `os.add_dll_directory()`. The pip-installed CUDA DLLs (`site-packages/nvidia/*/bin`) must be prepended to `PATH` *before* the Python process starts. Setting `os.environ["PATH"]` from inside Python is unreliable because the loader has already begun resolving dependencies.

Working pattern (verified): prefix `PATH` in PowerShell before `uv run python ...`. For the real STT server, the launch wrapper must do this. See memory entry `faster-whisper-windows-dll-setup` for the production rule.

## TTS — Kokoro on CPU ✅ (downgrade from plan)

**Setup:**

```
uv pip install kokoro-onnx onnxruntime-gpu
# downloaded kokoro-v1.0.onnx + voices-v1.0.bin from thewh1teagle/kokoro-onnx releases
```

`onnxruntime.get_available_providers()` correctly lists `['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']`. CUDA is visible to ORT.

**However:** `kokoro_onnx==0.5.0` creates its `InferenceSession` without exposing a `providers` kwarg, and its `Kokoro.__init__` does not accept one. A monkeypatch on `ort.InferenceSession` failed to take effect — kokoro_onnx imports `InferenceSession` with `from`-import, capturing the original symbol before our patch can run. Forcing CUDA would require either (a) patching kokoro_onnx upstream, (b) sys.modules manipulation that imports kokoro_onnx after replacing the binding in its module namespace, or (c) forking the small `kokoro_onnx.Kokoro` class to expose providers.

**Decision: accept CPU.** Kokoro is small (82M params); steady-state CPU synthesis is well below realtime:

| Run | Audio produced | Wall time | Realtime factor |
|-----|----------------|-----------|-----------------|
| First (includes JIT warmup) | 4.54s | 1.90s | 0.42× |
| Second (steady state) | 1.64s | 0.44s | **0.27×** |

A 5-second spoken reply will synthesize in ~1.4s on CPU. Within latency budget; leaves the GPU for STT (and any future use). Revisit only if we add streaming TTS or barge-in cancellation that needs sub-200ms first-chunk latency.

## Implications for design / Phase 1

1. **Host STT server (Windows) must run via a launcher script that prefixes `PATH`.** The Python process can also call `os.add_dll_directory()` defensively, but PATH is the load-bearing piece.
2. **TTS server uses CPU ORT providers.** No `nvidia-*` CUDA DLLs are needed for the TTS process. `onnxruntime` (CPU) is sufficient; can uninstall `onnxruntime-gpu` to avoid dependency conflict and slim the venv.
3. **CUDA driver pin in docs:** require Windows NVIDIA driver supporting at least CUDA 12. Tested working at 596.21.
4. **Model files:** `kokoro-v1.0.onnx` (~330 MB) + `voices-v1.0.bin` (~5 MB). Should be downloaded by the Phase 1 `install-host.ps1` rather than committed to git.

## Files in the spike venv (snapshot)

```
faster-whisper        latest
kokoro-onnx           0.5.0
onnxruntime           1.26.0
onnxruntime-gpu       1.26.0   # can drop
nvidia-cublas-cu12    12.9.2.10
nvidia-cudnn-cu12     9.22.0.52
nvidia-cuda-nvrtc-cu12 12.9.86
```
