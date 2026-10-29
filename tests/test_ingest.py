"""ingest.py against real ffmpeg on a tiny synthetic clip; yt-dlp is faked (no network)."""
import subprocess
import wave
from pathlib import Path

import pytest
import yt_dlp

import ingest


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "stream_clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=44100",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def test_extract_audio_writes_16k_mono_wav(tmp_path, monkeypatch, video):
    monkeypatch.setattr(ingest, "TEMP_DIR", tmp_path / "temp")
    wav = ingest.extract_audio(video)
    assert wav == tmp_path / "temp" / "stream_clip.wav"
    with wave.open(str(wav), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert 1.8 < w.getnframes() / w.getframerate() < 2.2


def test_extract_audio_skips_when_wav_exists(tmp_path, monkeypatch, video):
    monkeypatch.setattr(ingest, "TEMP_DIR", tmp_path)
    existing = tmp_path / "stream_clip.wav"
    existing.write_bytes(b"RIFFexisting")
    assert ingest.extract_audio(video) == existing
    assert existing.read_bytes() == b"RIFFexisting"  # not re-extracted


def test_extract_audio_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest.extract_audio(tmp_path / "nope.mp4")


def test_extract_audio_ffmpeg_failure_is_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "TEMP_DIR", tmp_path / "temp")
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")
    with pytest.raises(ingest.ffmpeg.Error):
        ingest.extract_audio(broken)


class FakeYDL:
    outcome = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"title": "Epic Moment", "ext": "mp4"}

    def prepare_filename(self, info):
        return str(FakeYDL.target)


def test_download_returns_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "Epic Moment.mp4"
    target.write_bytes(b"x")
    FakeYDL.target, FakeYDL.outcome = target, None
    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", FakeYDL)
    assert ingest.download_from_url("https://twitch.tv/videos/1") == target


def test_download_missing_file_raises(tmp_path, monkeypatch):
    FakeYDL.target, FakeYDL.outcome = tmp_path / "ghost.mp4", None
    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(RuntimeError, match="file is missing"):
        ingest.download_from_url("https://twitch.tv/videos/1")


def test_download_error_propagates(tmp_path, monkeypatch):
    FakeYDL.target, FakeYDL.outcome = tmp_path / "x.mp4", yt_dlp.utils.DownloadError("private video")
    monkeypatch.setattr(ingest.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(yt_dlp.utils.DownloadError):
        ingest.download_from_url("https://twitch.tv/videos/1")


def test_main_extracts_audio(tmp_path, monkeypatch, video):
    monkeypatch.setattr(ingest, "TEMP_DIR", tmp_path / "t")
    monkeypatch.setattr("sys.argv", ["ingest.py", str(video)])
    ingest.main()
    assert (tmp_path / "t" / "stream_clip.wav").exists()
