import fnmatch
import subprocess

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
    output_dir = tmp_path / "eliasn97"
    output_dir.mkdir()
    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    # render_clip() checks the output file actually exists and is non-empty afterwards —
    # simulate ffmpeg having written it, since _run_ffmpeg itself is faked above.
    expected_path = output_dir / process.clip_output_filename(1, clip["title"])
    expected_path.write_bytes(b"fake video bytes")

    result = process.render_clip(
        tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
        1080, 1920, output_dir=output_dir,
    )
    assert result == expected_path


def test_render_clip_defaults_to_shared_output_dir_when_none_given(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "_run_ffmpeg", _fake_success)
    monkeypatch.setattr(process, "OUTPUT_DIR", tmp_path)
    clip = {"title": "Test Clip", "start_time": 0.0, "end_time": 1.0}
    expected_path = tmp_path / process.clip_output_filename(1, clip["title"])
    expected_path.write_bytes(b"fake video bytes")

    result = process.render_clip(
        tmp_path / "source.mp4", clip, tmp_path / "subs.ass", 1, process.LAYOUT_BLUR_BACKGROUND,
        1080, 1920,
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
