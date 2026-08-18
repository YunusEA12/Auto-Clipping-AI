import pytest

import analyze


def test_analyze_default_transcription_path_respects_monkeypatch(tmp_path, monkeypatch):
    # analyze(transcription_path=None, ...) must resolve to the monkeypatched
    # TRANSCRIPTION_PATH, not a value bound at function-definition time — verified here via
    # load_transcript() raising on the *monkeypatched* (nonexistent) path specifically,
    # never touching the real project's temp/transcription.json.
    fake_path = tmp_path / "transcription.json"
    monkeypatch.setattr(analyze, "TRANSCRIPTION_PATH", fake_path)

    with pytest.raises(FileNotFoundError) as exc_info:
        analyze.analyze()
    assert fake_path.name in str(exc_info.value)
    assert str(tmp_path) in str(exc_info.value)
