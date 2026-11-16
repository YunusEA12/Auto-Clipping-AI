import hashlib
import subprocess

import pytest

from autoclip.rendering import audio_repair


def test_two_pass_repair_corrects_quiet_audio_without_changing_video(tmp_path):
    video = tmp_path / "render.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=10:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-af",
            "volume=0.03",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        timeout=30,
    )

    def video_hash():
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(video),
                "-map",
                "0:v",
                "-c",
                "copy",
                "-f",
                "hash",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout

    original_video = video_hash()
    result = audio_repair.repair_render_loudness(video)
    assert result["changed"]
    assert result["before_lufs"] < -25
    assert abs(result["after_lufs"] + 14) <= 3
    assert video_hash() == original_video
    assert audio_repair.repair_render_loudness(video)["changed"] is False


def test_failed_repair_preserves_original_file(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"keep-original-render")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        audio_repair,
        "_measure",
        lambda _: {"input_i": -25, "input_tp": -5, "input_lra": 5, "input_thresh": -35, "target_offset": 0},
    )
    monkeypatch.setattr(audio_repair, "_run", lambda _: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 300)))
    with pytest.raises(subprocess.TimeoutExpired):
        audio_repair.repair_render_loudness(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert list(tmp_path.iterdir()) == [path]
