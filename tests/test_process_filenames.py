import fnmatch
import subprocess
from pathlib import Path

import process


def test_clip_output_filename_matches_its_own_glob_pattern():
    # H-06: process.render_clip() writes clip_<index>_<slug>.mp4 and train_loop.py
    # independently re-derives that pattern via glob to locate rendered files when no
    # explicit path dict was handed to it. Both now come from these two functions — this
    # test is the regression guard that keeps them from silently drifting apart again.
    for index in (1, 2, 42):
        for title in ("Krasser Moment!", "  weird///chars??", "Ünïcödé Titel", ""):
            filename = process.clip_output_filename(index, title)
            assert fnmatch.fnmatch(filename, process.clip_glob_pattern(index))


def test_clip_glob_pattern_does_not_match_a_different_index():
    filename = process.clip_output_filename(3, "Some Clip")
    assert not fnmatch.fnmatch(filename, process.clip_glob_pattern(4))


def test_clip_output_filename_is_slugified():
    filename = process.clip_output_filename(1, "Hello, World!")
    assert filename == "clip_1_Hello_World.mp4"


def test_empty_title_still_produces_a_valid_filename():
    filename = process.clip_output_filename(1, "")
    assert filename == "clip_1_clip.mp4"


# --- render_clip output_dir (2026-08-18: H-14 — two streamers rendering concurrently into
# the shared flat output/ dir could both produce clip_1_*.mp4 and clobber each other; a
# namespaced output_dir per streamer is what auto_pilot.py now passes here) ----------------

def _fake_success(cmd, timeout=None):
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_render_clip_writes_into_given_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "_run_ffmpeg", _fake_success)
    # _fake_success's "fake video bytes" below isn't a real, decodable video — _is_valid_render()
    # (2026-08-21) would correctly reject it, since these tests are about output_dir/filename
    # placement, not video validity (see the _is_valid_render / QSV-fallback tests below for
    # that behavior's own coverage).
    monkeypatch.setattr(process, "_is_valid_render", lambda output_path: True)
    output_dir = tmp_path / "eliasn97"
    output_dir.mkdir()
    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    # render_clip() checks the output file actually exists and is non-empty afterwards —
    # simulate ffmpeg having written it, since _run_ffmpeg itself is faked above.
    expected_path = output_dir / process.clip_output_filename(1, clip["title"])
    expected_path.write_bytes(b"fake video bytes")

    # music_path=None: these tests are about output_dir behavior, not background-music
    # selection — without this, render_clip()'s default ("unset" -> pick one automatically)
    # would scan the real background_music/ directory and, since a track exists there, shell
    # out to a real ffprobe against a source.mp4 that's never actually created here (found in
    # review, 2026-08-21: ambient-repo-state-dependent, non-hermetic test behavior).
    result = process.render_clip(
        tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
        1080, 1920, output_dir=output_dir, music_path=None,
    )
    assert result == expected_path


# --- encoder selection / _is_valid_render (2026-08-22: h264_qsv removed entirely — confirmed
# /dev/dri doesn't exist on this VPS at all, so every QSV attempt failed (either a nonzero
# exit, or exit 0 while writing a nonempty but undecodable file, which the old exists()/size>0
# guard alone could not catch) and only ever wasted an attempt before the CPU fallback anyway.
# render_clip() now goes straight to libx264.) -------------------------------------------------

def test_render_clip_never_invokes_h264_qsv(tmp_path, monkeypatch):
    calls = []

    def _fake_run(cmd, timeout=None):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"valid")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(process, "_run_ffmpeg", _fake_run)
    monkeypatch.setattr(process, "_is_valid_render", lambda output_path: True)

    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    process.render_clip(
        tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
        1080, 1920, output_dir=tmp_path, music_path=None,
    )

    assert len(calls) == 1  # succeeds on the first (and only) attempt
    assert "h264_qsv" not in calls[0]
    assert "libx264" in calls[0]


def test_render_clip_retries_without_music_when_the_music_mix_is_unplayable(tmp_path, monkeypatch):
    calls = []

    def _fake_run(cmd, timeout=None):
        has_music = "-stream_loop" in cmd
        calls.append("music" if has_music else "no_music")
        Path(cmd[-1]).write_bytes(b"corrupt" if has_music else b"valid")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(process, "_run_ffmpeg", _fake_run)
    monkeypatch.setattr(process, "_is_valid_render", lambda output_path: output_path.read_bytes() == b"valid")

    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    result = process.render_clip(
        tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
        1080, 1920, output_dir=tmp_path, music_path=tmp_path / "track.mp3", has_source_audio=True,
    )

    assert calls == ["music", "no_music"]
    assert result.read_bytes() == b"valid"


def test_render_clip_raises_when_every_attempt_produces_an_unplayable_file(tmp_path, monkeypatch):
    def _fake_run(cmd, timeout=None):
        Path(cmd[-1]).write_bytes(b"corrupt")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(process, "_run_ffmpeg", _fake_run)
    monkeypatch.setattr(process, "_is_valid_render", lambda output_path: False)

    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    try:
        process.render_clip(
            tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
            1080, 1920, output_dir=tmp_path, music_path=None,
        )
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "FFmpeg failed for clip 1" in str(e)


def test_render_clip_defaults_to_shared_output_dir_when_none_given(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "_run_ffmpeg", _fake_success)
    monkeypatch.setattr(process, "_is_valid_render", lambda output_path: True)
    monkeypatch.setattr(process, "OUTPUT_DIR", tmp_path)
    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    expected_path = tmp_path / process.clip_output_filename(1, clip["title"])
    expected_path.write_bytes(b"fake video bytes")

    result = process.render_clip(
        tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
        1080, 1920, music_path=None,
    )
    assert result == expected_path


# --- _write_clip_metadata_sidecar (2026-08-18: H-14 — app.py's Clip Archiv used to
# re-derive a clip's metadata from its filename index against "whichever *_clips.json is
# newest," which attributes the wrong clip's title/score once cycles or streamers overlap
# on the same index; a sidecar written right next to the render is unambiguous) -----------

def test_metadata_sidecar_written_next_to_clip(tmp_path):
    output_path = tmp_path / "clip_1_Test_Clip.mp4"
    clip = {"title": "Test Clip", "viral_score": 8, "hook_explanation": "Because it's great"}

    process._write_clip_metadata_sidecar(output_path, clip)

    sidecar = output_path.with_suffix(".json")
    assert sidecar.exists()
    import json
    saved = json.loads(sidecar.read_text(encoding="utf-8"))
    assert saved["title"] == "Test Clip"
    assert saved["viral_score"] == 8
    assert "rendered_at" in saved


def test_metadata_sidecar_write_failure_does_not_raise(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(process.atomic_io, "atomic_write_json", _boom)
    # Must not raise — a failed debug/metadata write must never fail the render it describes.
    process._write_clip_metadata_sidecar(tmp_path / "clip_1_x.mp4", {"title": "x"})
