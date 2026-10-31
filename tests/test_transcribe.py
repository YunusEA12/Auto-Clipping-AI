"""transcribe.py with a fake WhisperModel (no model download), realistic German word data."""
import json
from types import SimpleNamespace

import pytest

import transcribe


def W(text, start, end):
    return SimpleNamespace(word=f" {text}", start=start, end=end)


def S(text, start, end, words):
    return SimpleNamespace(text=f"  {text} ", start=start, end=end, words=words)


SEGMENTS = [
    S("Das war unglaublich", 0.0, 2.4, [W("Das", 0.0, 0.4), W("war", 0.5, 0.9), W("unglaublich", 1.0, 2.4)]),
    S("Chat dreht durch", 2.6, 4.0, [W("Chat", 2.6, 3.0), W("dreht", 3.1, 3.5), W("durch", 3.6, 4.0)]),
]
INFO = SimpleNamespace(language="de", language_probability=0.98)


class FakeModel:
    calls = []
    first_error = None

    def __init__(self, size, device, compute_type, cpu_threads):
        FakeModel.init = (size, device, compute_type, cpu_threads)

    def transcribe(self, path, word_timestamps):
        FakeModel.calls.append(word_timestamps)
        if word_timestamps and FakeModel.first_error:
            def gen():
                yield SEGMENTS[0]
                raise FakeModel.first_error
            return gen(), INFO
        if not word_timestamps:
            return iter([S(s.text.strip(), s.start, s.end, None) for s in SEGMENTS]), INFO
        return iter(SEGMENTS), INFO


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    FakeModel.calls, FakeModel.first_error = [], None
    monkeypatch.setattr(transcribe, "WhisperModel", FakeModel)
    monkeypatch.setattr(transcribe, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setenv("WHISPER_CPU_THREADS", "2")
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF")
    return audio


def test_whisper_cpu_threads(monkeypatch):
    monkeypatch.setattr(transcribe.os, "cpu_count", lambda: 8)
    monkeypatch.setenv("WHISPER_CPU_THREADS", "4")
    assert transcribe.whisper_cpu_threads() == 4
    monkeypatch.setenv("WHISPER_CPU_THREADS", "99")
    assert transcribe.whisper_cpu_threads() == 8
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv("WHISPER_CPU_THREADS", bad)
        assert transcribe.whisper_cpu_threads() == 2
    monkeypatch.setattr(transcribe.os, "cpu_count", lambda: None)
    monkeypatch.setenv("WHISPER_CPU_THREADS", "6")
    assert transcribe.whisper_cpu_threads() == 2


def test_transcribe_writes_expected_json(fake, tmp_path):
    out = transcribe.transcribe(fake)
    assert out == tmp_path / "temp" / "chunk_transcription.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["language"] == "de"
    assert data["segments"][0] == {
        "text": "Das war unglaublich", "start": 0.0, "end": 2.4,
        "words": [{"text": "Das", "start": 0.0, "end": 0.4}, {"text": "war", "start": 0.5, "end": 0.9},
                  {"text": "unglaublich", "start": 1.0, "end": 2.4}],
    }
    assert FakeModel.init == ("base", "cpu", "int8", 2)


def test_existing_transcript_is_reused(fake, tmp_path):
    existing = tmp_path / "temp"
    existing.mkdir()
    (existing / "chunk_transcription.json").write_text("{}", encoding="utf-8")
    transcribe.transcribe(fake)
    assert FakeModel.calls == []  # Whisper never ran


def test_missing_audio_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe.transcribe(tmp_path / "none.wav")


def test_alignment_bug_retries_without_word_timestamps(fake):
    FakeModel.first_error = IndexError("boolean index did not match indexed array")
    out = transcribe.transcribe(fake)
    assert FakeModel.calls == [True, False]
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [s["words"] for s in data["segments"]] == [[], []]
    assert data["segments"][1]["text"] == "Chat dreht durch"


def test_other_index_errors_are_not_swallowed(fake):
    FakeModel.first_error = IndexError("list index out of range")
    with pytest.raises(IndexError):
        transcribe.transcribe(fake)


def test_no_segments_raises(fake, monkeypatch):
    monkeypatch.setattr(FakeModel, "transcribe", lambda self, p, word_timestamps: (iter([]), INFO))
    with pytest.raises(RuntimeError, match="no segments"):
        transcribe.transcribe(fake)


def test_main(fake, monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["transcribe.py", str(fake), "--model", "small"])
    transcribe.main()
    assert FakeModel.init[0] == "small"
    assert (tmp_path / "temp" / "chunk_transcription.json").exists()
