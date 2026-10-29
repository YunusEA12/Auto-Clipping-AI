"""Regression coverage uses the actual production ASS generator, not tiny fake text."""
import pytest
import atomic_io
import auto_qc
import process


def generated(tmp_path, monkeypatch, title, words=None):
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path)
    words = words or ["UNGLAUBLICH", "SPANNENDER", "AUGENBLICK", "GEWESEN"]
    transcript = {"segments": [{"start": 0, "end": 4, "words": [
        {"start": i, "end": i + .9, "text": word} for i, word in enumerate(words)
    ]}]}
    return process.build_ass_for_clip({"start_time": 0, "end_time": 4, "title": title}, transcript, 1)


@pytest.mark.parametrize("title,length", [("Was bleibt", 10), ("Dieser Moment ist einfach", 25), ("Dieser Moment bleibt uns lange im Kopf!!", 40)])
def test_real_four_word_ass_and_realistic_title_pass(tmp_path, monkeypatch, title, length):
    assert len(title) == length
    path = generated(tmp_path, monkeypatch, title)
    text = path.read_text(encoding="utf-8")
    assert "Style: Default,Arial Black,80" in text
    assert "WrapStyle: 1" in text
    result = auto_qc.check_subtitles(path)
    assert result["passed"], result
    assert max(result["line_counts"]) > 1


def test_overlong_single_word_still_fails(tmp_path, monkeypatch):
    path = generated(tmp_path, monkeypatch, "Titel", ["W" * 80])
    assert not auto_qc.check_subtitles(path)["passed"]


def test_nowrap_rejects_phrase_that_normally_wraps(tmp_path, monkeypatch):
    path = generated(tmp_path, monkeypatch, "Titel")
    assert auto_qc.check_subtitles(path)["passed"]
    atomic_io.atomic_write_text(path, path.read_text(encoding="utf-8").replace("WrapStyle: 1", "WrapStyle: 2"))
    assert not auto_qc.check_subtitles(path)["passed"]


def test_narrow_margins_create_too_many_vertical_lines(tmp_path, monkeypatch):
    path = generated(tmp_path, monkeypatch, "Titel")
    text = path.read_text(encoding="utf-8").replace("5,50,50,50,1", "5,300,300,50,1")
    atomic_io.atomic_write_text(path, text)
    result = auto_qc.check_subtitles(path)
    assert not result["passed"]
    assert max(result["line_counts"]) >= 4

