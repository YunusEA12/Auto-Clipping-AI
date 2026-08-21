import subprocess

import process


# --- _has_audio_stream (2026-08-21) --------------------------------------------------------

def _fake_run(returncode, stdout="", stderr=""):
    def _run(cmd, capture_output=None, text=None, timeout=None):
        return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)
    return _run


def test_has_audio_stream_true_when_ffprobe_reports_a_stream(monkeypatch):
    monkeypatch.setattr(process.subprocess, "run", _fake_run(0, stdout="0\n"))
    assert process._has_audio_stream(process.Path("x.mp4")) is True


def test_has_audio_stream_false_when_ffprobe_succeeds_with_no_streams(monkeypatch):
    monkeypatch.setattr(process.subprocess, "run", _fake_run(0, stdout=""))
    assert process._has_audio_stream(process.Path("x.mp4")) is False


def test_has_audio_stream_true_on_process_exception(monkeypatch):
    def _raise(*a, **k):
        raise OSError("no such file")
    monkeypatch.setattr(process.subprocess, "run", _raise)
    assert process._has_audio_stream(process.Path("x.mp4")) is True


def test_has_audio_stream_true_on_nonzero_returncode_not_just_exception(monkeypatch):
    # found in review, 2026-08-21: the original version only checked for a raised exception,
    # not a clean-but-nonzero exit — a real ffprobe failure (corrupted container) read as
    # "confirmed no audio" and could silently drop a clip's real voice track.
    monkeypatch.setattr(process.subprocess, "run", _fake_run(1, stdout="", stderr="Invalid data found"))
    assert process._has_audio_stream(process.Path("x.mp4")) is True


# --- build_audio_filter --------------------------------------------------------------------

def test_build_audio_filter_with_source_audio_mixes_both_tracks():
    filt = process.build_audio_filter(has_source_audio=True, clip_duration=10.0)
    assert "[0:a]" in filt
    assert "[1:a]" in filt
    assert "amix" in filt
    assert filt.endswith("[outa]")


def test_build_audio_filter_without_source_audio_uses_music_only():
    filt = process.build_audio_filter(has_source_audio=False, clip_duration=10.0)
    assert "[0:a]" not in filt
    assert "[1:a]" in filt
    assert "atrim=duration=10.0" in filt
