"""Mic capture utility. Uses sounddevice (PortAudio)."""
from __future__ import annotations
import asyncio
import io
import logging
import queue
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from host.audio.vad import (
    Endpointer,
    EndpointResult,
    SILERO_FRAME_SAMPLES,
    SILERO_SAMPLE_RATE,
    VadScorer,
)

log = logging.getLogger(__name__)

# DEBUG-TAG: audio-capture
# Grep: grep -E "audio-capture|vad"

DEFAULT_SAMPLE_RATE = 16000
POSTROLL_MS = 200  # keep this many ms after the last speech frame so we don't clip final consonants


def encode_wav(samples: np.ndarray, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, subtype="FLOAT", format="WAV")
    return buf.getvalue()


def refresh_portaudio() -> None:
    """PortAudio caches the device list at init; without a re-init it never
    notices mics being plugged/unplugged after the process starts. The
    underscore-prefixed helpers are sounddevice's documented escape hatch
    for this case."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:  # don't let a quirky PortAudio build wedge a press
        log.warning("audio-capture: PortAudio re-init failed: %s", e)


def resolve_input_device(name_substring: Optional[str]) -> Optional[int]:
    """Spike A finding: Windows reorders device indices when Bluetooth devices
    come/go. Pin by name substring instead. Returns a device index or None
    (= sounddevice's default). If enumeration fails, fall back to default
    rather than raising."""
    if not name_substring:
        return None
    needle = name_substring.lower()
    try:
        devices = sd.query_devices()
    except Exception as e:
        log.error("audio-capture: device enumeration failed: %s", e)
        return None
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and needle in d["name"].lower():
            log.info("audio-capture: matched input %r -> device %d (%s)", name_substring, i, d["name"])
            return i
    log.warning("audio-capture: no input device matched %r; falling back to default", name_substring)
    return None


class Recorder:
    """Push-to-talk recorder.

    Without an endpointer: ``start()`` opens the stream and ``stop()`` returns
    the captured audio (fixed-window mode).

    With an endpointer: ``start()`` opens the stream and buffers audio without
    running VAD. The VAD worker only spins up once the caller invokes
    ``begin_endpointing()`` — typically when the user releases the push-to-talk
    button. This matches the user-facing model: hold-to-record, release-to-end
    with a VAD-driven tail. Audio captured before the release is always kept
    intact; VAD only decides where to cut the trailing tail.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        device_name: Optional[str] = None,
        endpointer: Optional[Endpointer] = None,
        vad_scorer: Optional[VadScorer] = None,
    ):
        if (endpointer is None) != (vad_scorer is None):
            raise ValueError("endpointer and vad_scorer must be provided together")
        if endpointer is not None and sample_rate != SILERO_SAMPLE_RATE:
            raise ValueError(
                f"VAD endpointing requires {SILERO_SAMPLE_RATE}Hz capture, got {sample_rate}"
            )
        self.sample_rate = sample_rate
        self.device_name = device_name
        self._endpointer = endpointer
        self._vad = vad_scorer
        self._chunks: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        # VAD-only state
        self._vad_thread: Optional[threading.Thread] = None
        self._vad_queue: Optional[queue.Queue] = None
        self._vad_stop = threading.Event()
        self.done_event = threading.Event()
        self._endpoint_result: Optional[EndpointResult] = None
        # Sample count at the moment VAD endpointing began. Trim uses it as a
        # floor so the held portion of the recording is always preserved.
        self._endpointing_start_samples: int = 0

    @property
    def has_endpointer(self) -> bool:
        return self._endpointer is not None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("audio-capture: stream status %s", status)
        chunk = indata.copy().flatten()
        with self._lock:
            self._chunks.append(chunk)
        # Snapshot the ref — begin_endpointing sets it from another thread.
        q = self._vad_queue
        if q is not None:
            q.put(chunk)

    def _vad_worker(self) -> None:
        assert self._endpointer is not None and self._vad is not None
        buf = np.zeros(0, dtype=np.float32)
        while not self._vad_stop.is_set():
            try:
                chunk = self._vad_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break
            buf = np.concatenate([buf, chunk.astype(np.float32, copy=False)])
            while len(buf) >= SILERO_FRAME_SAMPLES:
                frame = buf[:SILERO_FRAME_SAMPLES]
                buf = buf[SILERO_FRAME_SAMPLES:]
                score = self._vad.score(frame)
                result = self._endpointer.feed(score)
                if result is not None:
                    self._endpoint_result = result
                    self.done_event.set()
                    log.info("audio-capture: endpoint fired (%s)", result.reason)
                    return

    def start(self) -> None:
        # Re-enumerate devices every press so the user can plug/unplug the
        # mic at will without restarting the orchestrator. PortAudio caches
        # the list at init, so a full re-init is required.
        refresh_portaudio()
        device_index = resolve_input_device(self.device_name)
        log.info("audio-capture: start sr=%d device=%r vad=%s",
                 self.sample_rate, device_index, self._endpointer is not None)
        self._chunks = []
        self.done_event.clear()
        self._endpoint_result = None
        self._endpointing_start_samples = 0
        # Open the audio stream first — if there's no device at all this is
        # where it'll fail, and we don't want to leak any worker state.
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=1,
                dtype="float32", callback=self._callback,
                device=device_index,
            )
            self._stream.start()
        except Exception:
            self._stream = None
            raise
        # VAD worker is deferred to begin_endpointing(); see class docstring.

    def begin_endpointing(self) -> None:
        """Activate VAD endpointing. Called after the user releases the
        push-to-talk button. The worker only scores frames captured from this
        point forward — the held portion of the recording is preserved by
        ``stop()``'s trim logic regardless of what VAD does next.

        Safe no-op if the recorder has no endpointer attached or if the worker
        is already running."""
        if self._endpointer is None:
            return
        if self._vad_thread is not None:
            return
        with self._lock:
            self._endpointing_start_samples = sum(len(c) for c in self._chunks)
        self._endpointer.reset()
        self._vad.reset()
        self._vad_stop.clear()
        self._vad_queue = queue.Queue()
        self._vad_thread = threading.Thread(target=self._vad_worker, daemon=True)
        self._vad_thread.start()
        log.info(
            "audio-capture: endpointing begins at %d samples (%.2fs held)",
            self._endpointing_start_samples,
            self._endpointing_start_samples / self.sample_rate,
        )

    async def wait_for_end(self, *, timeout: float) -> Optional[EndpointResult]:
        """Block until VAD endpointing fires, or timeout. Returns the result
        (or None if there's no endpointer attached / the wait timed out)."""
        if self._endpointer is None:
            return None
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.done_event.wait, timeout)
        return self._endpoint_result

    def stop(self) -> np.ndarray:
        # Tolerant of stop() after a failed start(): we just return an empty
        # buffer and let upstream handle the "empty transcript" case.
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                # A mid-recording disconnect can make close() raise; log it
                # but still return whatever we captured so the cycle ends.
                log.warning("audio-capture: stream close raised: %s", e)
            self._stream = None
        if self._vad_thread is not None:
            self._vad_stop.set()
            if self._vad_queue is not None:
                self._vad_queue.put(None)
            self._vad_thread.join(timeout=1.0)
            self._vad_thread = None
            self._vad_queue = None
        with self._lock:
            samples = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        samples = self._trim(samples)
        log.info("audio-capture: stop, %d samples (%.2fs)", len(samples), len(samples) / self.sample_rate)
        return samples

    def _trim(self, samples: np.ndarray) -> np.ndarray:
        result = self._endpoint_result
        if result is None:
            return samples
        # ``last_speech_ms`` is measured from the moment VAD started feeding,
        # i.e. release-time. The held portion of the recording lives before
        # that and is always kept intact: the user explicitly chose to capture
        # it by holding the button.
        held_samples = self._endpointing_start_samples
        if result.reason == "no_speech":
            return samples[:held_samples] if held_samples < len(samples) else samples
        if result.reason == "silence":
            tail_samples = int(self.sample_rate * (result.last_speech_ms + POSTROLL_MS) / 1000)
            end = held_samples + tail_samples
            return samples[:end] if end < len(samples) else samples
        return samples  # max_duration: keep everything
