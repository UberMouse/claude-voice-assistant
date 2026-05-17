"""Voice activity detection + end-of-utterance state machine.

`Endpointer` is pure (testable without torch). `SileroVadModel` is the
concrete scorer; load it lazily so unit tests that exercise the state
machine don't pay the torch import cost.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

log = logging.getLogger(__name__)

# DEBUG-TAG: vad
# Grep all VAD debug logs: grep -E "vad" voice-assistant.log

# Silero v5 requires exactly 512 samples at 16kHz per inference call.
SILERO_FRAME_SAMPLES = 512
SILERO_SAMPLE_RATE = 16000


class VadScorer(Protocol):
    """Anything that maps a fixed-size audio frame to a 0..1 speech probability."""
    def score(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


@dataclass
class EndpointResult:
    reason: str           # "silence" | "no_speech" | "max_duration"
    elapsed_ms: int       # total ms of audio observed
    last_speech_ms: int   # ms-since-start of the last speech frame (0 if no speech)


class Endpointer:
    """Pure end-of-utterance state machine.

    Feed it a per-frame speech probability via `feed(score)` and it tells
    you when to stop recording. Three exit conditions:

    - `silence`      — we saw speech, then trailing silence for `silence_ms`.
    - `no_speech`    — `no_speech_ms` elapsed without ever crossing the speech threshold.
    - `max_duration` — `max_ms` elapsed regardless.
    """
    def __init__(
        self,
        *,
        frame_samples: int = SILERO_FRAME_SAMPLES,
        sample_rate: int = SILERO_SAMPLE_RATE,
        speech_threshold: float = 0.5,
        silence_ms: int = 800,
        max_ms: int = 30_000,
        no_speech_ms: int = 3_000,
    ):
        self.frame_ms = int(round(1000 * frame_samples / sample_rate))
        self.speech_threshold = speech_threshold
        self.silence_ms = silence_ms
        self.max_ms = max_ms
        self.no_speech_ms = no_speech_ms
        self.reset()

    def reset(self) -> None:
        self._elapsed_ms = 0
        self._triggered = False
        self._last_speech_ms = 0
        self._result: Optional[EndpointResult] = None

    def feed(self, score: float) -> Optional[EndpointResult]:
        if self._result is not None:
            return self._result
        self._elapsed_ms += self.frame_ms
        if score >= self.speech_threshold:
            self._triggered = True
            self._last_speech_ms = self._elapsed_ms
        if self._triggered:
            silence_run = self._elapsed_ms - self._last_speech_ms
            if silence_run >= self.silence_ms:
                return self._finish("silence")
        elif self._elapsed_ms >= self.no_speech_ms:
            return self._finish("no_speech")
        if self._elapsed_ms >= self.max_ms:
            return self._finish("max_duration")
        return None

    @property
    def result(self) -> Optional[EndpointResult]:
        return self._result

    def _finish(self, reason: str) -> EndpointResult:
        self._result = EndpointResult(
            reason=reason,
            elapsed_ms=self._elapsed_ms,
            last_speech_ms=self._last_speech_ms,
        )
        log.info(
            "vad: endpoint reason=%s elapsed_ms=%d last_speech_ms=%d",
            reason, self._elapsed_ms, self._last_speech_ms,
        )
        return self._result


class SileroVadModel:
    """Silero v5 scorer. Imports torch lazily — tests should not need this class."""
    def __init__(self, sample_rate: int = SILERO_SAMPLE_RATE):
        if sample_rate != SILERO_SAMPLE_RATE:
            raise ValueError(f"SileroVadModel only supports {SILERO_SAMPLE_RATE}Hz, got {sample_rate}")
        # Lazy imports so the test module can import vad.py without torch.
        import torch  # noqa: F401
        from silero_vad import load_silero_vad
        self._sample_rate = sample_rate
        log.info("vad: loading silero model")
        self._model = load_silero_vad()
        self._torch = torch

    def score(self, frame: np.ndarray) -> float:
        if frame.shape != (SILERO_FRAME_SAMPLES,):
            raise ValueError(f"expected frame shape ({SILERO_FRAME_SAMPLES},), got {frame.shape}")
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        with self._torch.no_grad():
            tensor = self._torch.from_numpy(frame)
            return float(self._model(tensor, self._sample_rate).item())

    def reset(self) -> None:
        self._model.reset_states()
