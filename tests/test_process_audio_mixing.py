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


def test_loudnorm_applies_to_voice_music_and_music_only():
    for has_audio in (True, False):
        filt = process.build_audio_filter(has_audio, 10.)
        assert filt.endswith("loudnorm=I=-14:TP=-1:LRA=11[outa]")


def test_source_only_audio_normalized_without_background_track():
    cmd = process._build_render_cmd(process.Path("source.mp4"), 0, 10, "null[outv]", [], process.Path("out.mp4"))
    assert cmd[cmd.index("-af") + 1] == "loudnorm=I=-14:TP=-1:LRA=11"


def test_music_mix_normalized_once_in_filtergraph():
    cmd = process._build_render_cmd(process.Path("source.mp4"), 0, 10, "null[outv]", [], process.Path("out.mp4"), music_path=process.Path("music.mp3"))
    assert "-af" not in cmd
    assert cmd[cmd.index("-filter_complex") + 1].count("loudnorm=I=-14:TP=-1:LRA=11") == 1
