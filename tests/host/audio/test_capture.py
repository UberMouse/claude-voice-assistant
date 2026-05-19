import io
import numpy as np
import soundfile as sf

from host.audio.capture import Recorder, encode_wav
from host.audio.vad import EndpointResult


def test_encode_wav_roundtrip():
    samples = (np.random.rand(16000) * 2 - 1).astype(np.float32)
    blob = encode_wav(samples, sample_rate=16000)
    data, sr = sf.read(io.BytesIO(blob))
    assert sr == 16000
    assert data.shape == (16000,)
    assert np.allclose(data, samples, atol=1e-3)


def _make_rec_for_trim() -> Recorder:
    """Build a Recorder without VAD so we can exercise _trim directly. The
    trim path doesn't actually need the endpointer/vad objects — only the
    state fields we set on the instance."""
    return Recorder(sample_rate=16000)


def test_trim_silence_keeps_held_portion_plus_tail():
    """User holds 1s (16000 samples), then released; VAD trailed and reported
    last_speech_ms=500. Trim should keep the full hold plus 500ms + postroll."""
    rec = _make_rec_for_trim()
    rec._endpointing_start_samples = 16000  # 1.0s held
    rec._endpoint_result = EndpointResult(reason="silence", elapsed_ms=1300, last_speech_ms=500)
    samples = np.zeros(48000, np.float32)  # 3s captured total
    trimmed = rec._trim(samples)
    # 500ms speech tail + 200ms postroll = 700ms = 11200 samples after the hold.
    assert len(trimmed) == 16000 + 11200


def test_trim_no_speech_keeps_only_held_portion():
    """User holds for 800ms, then VAD sees only silence after release. The
    held portion should be preserved — STT will get to decide if it's empty."""
    rec = _make_rec_for_trim()
    rec._endpointing_start_samples = 12800  # 0.8s
    rec._endpoint_result = EndpointResult(reason="no_speech", elapsed_ms=3000, last_speech_ms=0)
    samples = np.zeros(60000, np.float32)
    trimmed = rec._trim(samples)
    assert len(trimmed) == 12800


def test_trim_max_duration_keeps_everything():
    rec = _make_rec_for_trim()
    rec._endpointing_start_samples = 16000
    rec._endpoint_result = EndpointResult(reason="max_duration", elapsed_ms=30000, last_speech_ms=29000)
    samples = np.zeros(500000, np.float32)
    trimmed = rec._trim(samples)
    assert len(trimmed) == 500000


def test_trim_no_endpoint_result_keeps_everything():
    """If endpointing was never started (no result), the cycle hit the
    timeout path and we keep all captured audio."""
    rec = _make_rec_for_trim()
    samples = np.zeros(32000, np.float32)
    trimmed = rec._trim(samples)
    assert len(trimmed) == 32000
