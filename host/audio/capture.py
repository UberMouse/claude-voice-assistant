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

def resolve_input_device(name_substring: Optional[str]) -> Optional[int]:
    """Spike A finding: Windows reorders device indices when Bluetooth devices
    come/go. Pin by name substring instead. Returns a device index or None
    (= sounddevice's default)."""
    if not name_substring:
        return None
    needle = name_substring.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and needle in d["name"].lower():
            log.info("audio-capture: matched input %r -> device %d (%s)", name_substring, i, d["name"])
            return i
    log.warning("audio-capture: no input device matched %r; falling back to default", name_substring)
    return None

class Recorder:
    """Push-to-talk recorder. start() opens stream, stop() returns the captured audio."""

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE,
                 device_name: Optional[str] = None):
        self.sample_rate = sample_rate
        self.device_name = device_name
        self._device_index = resolve_input_device(device_name)
        self._chunks: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("audio-capture: stream status %s", status)
        with self._lock:
            self._chunks.append(indata.copy().flatten())

    def start(self) -> None:
        log.info("audio-capture: start sr=%d device=%r", self.sample_rate, self._device_index)
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1,
            dtype="float32", callback=self._callback,
            device=self._device_index,
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
