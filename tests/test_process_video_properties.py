import subprocess
import time

import pytest

import process


def _make_test_video(path, duration=3, size="64x64", fps=10):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate={fps}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, check=True, timeout=30,
    )


@pytest.fixture
def test_video(tmp_path):
    path = tmp_path / "clip_1_test.mp4"
    try:
        _make_test_video(path)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        pytest.skip(f"ffmpeg not usable in this environment: {e}")
    return path


# --- get_video_properties vs individual functions (M-02) -------------------------------

def test_video_properties_matches_individual_functions(test_video):
    width, height, duration = process.get_video_properties(test_video)
    assert (width, height) == process.get_video_dimensions(test_video)
    assert duration == pytest.approx(process.get_video_duration(test_video), rel=0.01)
    assert width == 64 and height == 64
    assert duration == pytest.approx(3.0, abs=0.3)


def test_get_video_dimensions_still_works_standalone(test_video):
    width, height = process.get_video_dimensions(test_video)
    assert width > 0 and height > 0


def test_get_video_duration_still_works_standalone(test_video):
    duration = process.get_video_duration(test_video)
    assert duration > 0


# --- preview-frame extraction caching (L-05) --------------------------------------------

def test_frame_extraction_is_cached_on_second_call(test_video, tmp_path):
    frames_dir = tmp_path / "frames"
    first = process.extract_preview_frames(test_video, frames_dir=frames_dir)
    assert len(first) == len(process.PREVIEW_FRAME_FRACTIONS)
    mtimes_before = [p.stat().st_mtime for p in first]

    time.sleep(0.05)
    second = process.extract_preview_frames(test_video, frames_dir=frames_dir)
    mtimes_after = [p.stat().st_mtime for p in second]

    # Cache hit: same paths, untouched mtimes — ffmpeg was never re-invoked.
    assert [p.name for p in second] == [p.name for p in first]
    assert mtimes_after == mtimes_before


def test_frame_extraction_invalidates_when_video_is_newer(test_video, tmp_path):
    frames_dir = tmp_path / "frames"
    first = process.extract_preview_frames(test_video, frames_dir=frames_dir)
    mtimes_before = [p.stat().st_mtime for p in first]

    time.sleep(0.05)
    # Simulate a re-render under the same filename (ffmpeg -y overwrites in production) —
    # the cached frames must NOT be trusted once the source video is newer than them.
    _make_test_video(test_video, duration=1)

    second = process.extract_preview_frames(test_video, frames_dir=frames_dir)
    mtimes_after = [p.stat().st_mtime for p in second]
    assert mtimes_after != mtimes_before
