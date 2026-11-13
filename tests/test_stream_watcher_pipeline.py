"""process_chunk / render_hot_clips / watch_stream / main with realistic clip data; every
heavy step (ffmpeg, Whisper, LLM, upload, Discord) is replaced by a recording fake."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import stream_watcher as sw

# Captured at import, before conftest's autouse guard replaces it (same pattern as test_stream_watcher_chunks.py).
_real_cleanup = sw.cleanup_stale_chunks

CLIPS = {"clips": [
    {"title": "Chat dreht durch", "start_time": 12.0, "end_time": 41.5, "energy_rating": 9,
     "viral_score": 8, "description": "Das glaubt keiner", "hashtags": ["#fyp"]},
    {"title": "Ruhige Erklärung", "start_time": 60.0, "end_time": 95.0, "energy_rating": 4,
     "viral_score": 5, "description": "", "hashtags": []},
]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(sw, "HOT_CLIPS_LOG_PATH", tmp_path / "hot.jsonl")
    chunk = tmp_path / "live_chunk_x_1.ts"
    chunk.write_bytes(b"ts")
    wav = tmp_path / "live_chunk_x_1.wav"
    wav.write_bytes(b"wav")
    clips_path = tmp_path / "live_chunk_x_1_clips.json"
    clips_path.write_text(json.dumps(CLIPS), encoding="utf-8")
    calls = SimpleNamespace(notify=[], upload=[], render=[])
    monkeypatch.setattr(sw.ingest, "extract_audio", lambda p: wav)
    monkeypatch.setattr(sw.transcribe, "transcribe", lambda a: tmp_path / "t.json")
    monkeypatch.setattr(sw.analyze, "analyze", lambda t, audio_path, profile: clips_path)
    monkeypatch.setattr(sw.analyze, "load_transcript", lambda p: {"segments": []})
    monkeypatch.setattr(sw.notify, "send_notification", lambda **kw: calls.notify.append(kw))

    def fake_process(video, **kw):
        calls.render.append(kw)
        return [tmp_path / f"out_{i}.mp4" for i in range(len(json.loads((tmp_path / f"{video.stem}_clips.json").read_text())["clips"]))]
    monkeypatch.setattr(sw.process_module, "process", fake_process)

    def fake_upload(path, title, desc, tags, publish):
        calls.upload.append((path.name, title, publish))
        return SimpleNamespace(tiktok=SimpleNamespace(success=True))
    monkeypatch.setattr(sw.upload_manager, "upload_clip_everywhere", fake_upload)
    return SimpleNamespace(chunk=chunk, wav=wav, calls=calls, tmp=tmp_path)


def test_process_chunk_keeps_only_hot_clips_and_logs_them(env):
    hot = sw.process_chunk(env.chunk, energy_threshold=7)
    assert [c["title"] for c in hot] == ["Chat dreht durch"]
    assert not env.wav.exists()          # transient wav removed
    assert env.chunk.exists()            # raw chunk kept for manual rendering
    logged = [json.loads(l) for l in (env.tmp / "hot.jsonl").read_text(encoding="utf-8").splitlines()]
    assert logged[0]["chunk"] == env.chunk.name and logged[0]["energy_rating"] == 9
    assert env.calls.render == []


def test_process_chunk_without_hot_clips_deletes_chunk(env):
    assert sw.process_chunk(env.chunk, energy_threshold=10) == []
    assert not env.chunk.exists()


def test_process_chunk_failure_cleans_up(env, monkeypatch):
    monkeypatch.setattr(sw.transcribe, "transcribe", lambda a: (_ for _ in ()).throw(RuntimeError("whisper died")))
    assert sw.process_chunk(env.chunk) == []
    assert not env.chunk.exists() and not env.wav.exists()


def test_auto_render_without_publish_never_uploads(env):
    sw.process_chunk(env.chunk, auto_render=True, auto_upload=True, publish=False)
    assert env.calls.upload == []
    assert len(env.calls.render) == 1
    assert env.calls.notify[0]["upload_status"] == "rendered"
    assert not env.chunk.exists()


def test_auto_render_with_publish_uploads_and_notifies(env):
    sw.process_chunk(env.chunk, auto_render=True, auto_upload=True, publish=True)
    assert env.calls.upload == [("out_0.mp4", "Chat dreht durch", True)]
    assert env.calls.notify[0]["upload_status"] == "uploaded"
    assert env.calls.notify[0]["hashtags"] == ["#fyp"]


def test_failed_upload_status(env, monkeypatch):
    monkeypatch.setattr(sw.upload_manager, "upload_clip_everywhere",
                        lambda *a, **k: SimpleNamespace(tiktok=SimpleNamespace(success=False)))
    sw.process_chunk(env.chunk, auto_render=True, auto_upload=True, publish=True)
    assert env.calls.notify[0]["upload_status"] == "failed"


def test_render_failure_returns_empty(env, monkeypatch):
    monkeypatch.setattr(sw.process_module, "process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg")))
    assert sw.render_hot_clips(env.chunk, CLIPS["clips"][:1], {}) == []
    assert env.calls.notify == []


def test_drain_completed_keeps_running_futures():
    class F:
        def __init__(self, done, exc=None):
            self._d, self._e = done, exc
        def done(self): return self._d
        def exception(self): return self._e
    running = F(False)
    assert sw._drain_completed([F(True), F(True, RuntimeError("x")), running]) == [running]


def test_watch_stream_records_and_processes_chunks(env, monkeypatch):
    recorded, processed = [], []
    monkeypatch.setattr(sw, "record_stream_chunk", lambda url, dur: recorded.append(url) or env.chunk)
    monkeypatch.setattr(sw, "process_chunk", lambda *a: processed.append(a[0]))
    sw.watch_stream("https://twitch.tv/x", chunk_duration=1, max_chunks=3, max_workers=1)
    assert len(recorded) == 3 and len(processed) == 3


def test_watch_stream_retries_after_recording_error(env, monkeypatch):
    attempts = iter([RuntimeError("stream offline"), env.chunk])
    def record(url, dur):
        r = next(attempts)
        if isinstance(r, Exception):
            raise r
        return r
    sleeps = []
    monkeypatch.setattr(sw, "record_stream_chunk", record)
    monkeypatch.setattr(sw.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(sw, "process_chunk", lambda *a: None)
    sw.watch_stream("u", max_chunks=1, max_workers=1, auto_upload=True)
    assert sleeps == [sw.RETRY_DELAY_SECONDS]


def run_main(monkeypatch, argv):
    seen = {}
    monkeypatch.setattr(sw, "watch_stream", lambda *a: seen.setdefault("args", a))
    monkeypatch.setattr("sys.argv", ["stream_watcher.py", *argv])
    sw.main()
    return seen.get("args")


def test_main_defaults(monkeypatch):
    args = run_main(monkeypatch, ["--url", "https://twitch.tv/x", "--max-chunks", "2"])
    assert args[0] == "https://twitch.tv/x" and args[2] == 2 and args[3] == sw.ENERGY_THRESHOLD


def test_main_validates_flag_combinations(monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, ["--url", "u", "--auto-upload"])
    with pytest.raises(SystemExit):
        run_main(monkeypatch, ["--url", "u", "--publish"])
    with pytest.raises(SystemExit):
        run_main(monkeypatch, [])  # no url, no profile


def test_main_uses_profile_values(monkeypatch):
    profile = SimpleNamespace(model_dump=lambda: {"name": "p", "stream_url": "https://twitch.tv/p", "energy_threshold": 6})
    monkeypatch.setattr(sw.profiles, "load_profile_or_fallback", lambda n: profile)
    args = run_main(monkeypatch, ["--profile", "p", "--auto-render", "--auto-upload"])
    assert args[0] == "https://twitch.tv/p" and args[3] == 6


# --- cleanup_stale_chunks / periodic sweep (real function restored locally) ---------------

def test_stale_chunks_deleted_with_siblings_fresh_kept(env, monkeypatch):
    monkeypatch.setattr(sw, "cleanup_stale_chunks", _real_cleanup)
    old = env.tmp / "live_chunk_old_1.ts"
    old.write_bytes(b"x")
    (env.tmp / "live_chunk_old_1.wav").write_bytes(b"x")
    ancient = time.time() - 48 * 3600
    os.utime(old, (ancient, ancient))
    monkeypatch.setattr(sw.shutil, "disk_usage", lambda p: SimpleNamespace(free=500e9))
    deleted = sw.cleanup_stale_chunks(env.tmp)
    assert deleted == 1
    assert not old.exists() and not (env.tmp / "live_chunk_old_1.wav").exists()
    assert env.chunk.exists()  # fresh
