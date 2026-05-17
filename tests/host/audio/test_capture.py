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
