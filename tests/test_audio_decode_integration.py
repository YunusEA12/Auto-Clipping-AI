"""Exercise the real Whisper/PyAV boundary without a model or network access."""

import io
import wave

import numpy as np
from faster_whisper.audio import decode_audio


def test_whisper_decodes_and_resamples_real_wav():
    buffer = io.BytesIO()
    samples = (np.sin(np.arange(8000) * 2 * np.pi * 440 / 8000) * 12000).astype('<i2')
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(samples.tobytes())
    buffer.seek(0)

    decoded = decode_audio(buffer, sampling_rate=16000)

    assert decoded.dtype == np.float32
    assert abs(len(decoded) - 16000) < 10
    assert np.isfinite(decoded).all()
    assert np.max(np.abs(decoded)) > 0.1
