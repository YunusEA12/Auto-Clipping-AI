"""A real CPU render with generated ASS and audio; no models or publishing."""

import hashlib
import json
import subprocess

from autoclip.quality import auto_qc
from autoclip.rendering import process


def test_real_render_preserves_source_and_produces_playable_vertical_video(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=15:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    clip = {"start_time": 0.0, "end_time": 3.0, "title": "Moment"}
    transcript = {
        "segments": [{"start": 0.0, "end": 3.0, "text": "Test", "words": [{"start": 0.0, "end": 3.0, "text": "Test"}]}]
    }
    subtitles = process.build_ass_for_clip(clip, transcript, 1, output_w=360, output_h=640, layout="blur_background")
    target = process.render_clip(
        source, clip, subtitles, 1, "blur_background", 360, 640, output_dir=tmp_path, music_path=None
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target)],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(row for row in streams if row["codec_type"] == "video")
    assert (video["width"], video["height"]) == (360, 640)
    assert any(row["codec_type"] == "audio" for row in streams)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    measurements = auto_qc.measure_media(target)
    assert 2.8 < measurements["duration"] < 3.2
    assert measurements["black_seconds"] == 0
    assert abs(measurements["lufs"] + 14) <= 3
