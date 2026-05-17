# Spike A: Windows audio sanity check — findings

**Date:** 2026-05-17
**Result:** ✅ Pass. Proceed with Windows-native `sounddevice` + PortAudio for both capture and playback.

## Setup tested

- Windows 11 host
- Python 3.12.10 (winget `Python.Python.3.12`)
- `uv` 0.11.14
- `sounddevice`, `numpy`, `soundfile` (latest from PyPI)

## Devices in use

| Role | Device index | Name | Backend / channels |
|------|-------------|------|---------------------|
| Input  | 4  | (User's actual mic — corrected from initial device 53 mis-pick) | mono in |
| Output | 6  | Headphones (Sony WH-1000XM4) | MME, 2-ch out |

The default device enumeration from `sd.query_devices()` showed several virtual / generic entries before the real hardware. We will pin device indices (or device-name substrings) in the orchestrator config to avoid auto-pick surprises.

## Measured

- 3-second mono recording at 16 kHz: captured exactly 48000 samples (no dropouts).
- Wall-clock for 3s record + `sd.wait()`: ~3.17s (init + teardown overhead ~170 ms — fine).
- Round-trip playback through WH-1000XM4 over Bluetooth: clear, user heard their own voice.
- Peak amplitude during normal speech: ~0.034 (well above STT-usable threshold).

## Decisions / notes

- **Pin devices by name substring**, not just index — Windows reorders enumeration when devices come/go (Bluetooth especially). Use something like `next(d for d in sd.query_devices() if "Antlion" in d["name"])` rather than hardcoding `4`.
- **MME for output is fine** for TTS playback (pre-rendered audio, latency uncritical). If we ever need lower-latency playback (e.g. barge-in or live confirmation tones), switch the output device to its WASAPI variant.
- **30-second stability test skipped** — short test produced sample-exact output with no glitches, sounddevice on Windows is well-trodden. Revisit if we see dropouts during integration.

## Implications for design

None. The native-Windows audio path the design assumed works as expected.
