"""2026-08-23 fix: run_cycle() used to return immediately when a cycle's analysis found zero
new clips worth rendering, BEFORE ever reaching Phase 5 (Deployment) — which is where the
output/ backlog sweep (find_backlog_clips) and the YouTube/Instagram missing-upload retry
sweeps (retry_missing_youtube_uploads/retry_missing_instagram_uploads) live. Those sweeps'
own docstrings promise they "run every cycle regardless of whether this cycle had its own
survivors", but the early return silently broke that promise for any content-quiet cycle.

Found live: upload_ledger.json had several "pending" entries 14-37 hours old, every one
belonging to a streamer whose recent cycles kept coming back with zero new clips — the retry
sweep that would have released them (upload_ledger.try_mark_pending()'s own staleness check)
never got a chance to run at all. These tests lock in that Phase 5 now always runs."""

import json

import pytest

import auto_pilot


def _touch(path, content="x"):
    path.write_text(content, encoding="utf-8")
    return path


def _common_mocks(monkeypatch, tmp_path, video_path, wav_path, transcription_path, clips_path):
    monkeypatch.setattr(auto_pilot, "update_agent_state", lambda **kw: {})
    monkeypatch.setattr(auto_pilot.ingest, "extract_audio", lambda video_path: wav_path)
    monkeypatch.setattr(auto_pilot.transcribe, "transcribe", lambda wav_path: transcription_path)
    monkeypatch.setattr(auto_pilot.analyze, "analyze", lambda *a, **k: clips_path)
    monkeypatch.setattr(auto_pilot, "_cleanup_cycle_temp_files", lambda *a: None)
    monkeypatch.setattr(auto_pilot.process_module, "OUTPUT_DIR", tmp_path / "output")
    (tmp_path / "output").mkdir(exist_ok=True)


def test_run_cycle_still_runs_backlog_and_retry_sweeps_when_no_new_clips(tmp_path, monkeypatch):
    # This test locks in the "runs every cycle" promise for the YouTube sweep itself, which only
    # applies while YouTube uploads are enabled at all (upload_manager.YOUTUBE_UPLOADS_ENABLED,
    # 2026-08-25 TikTok-only mode) — see test_youtube_retry_sweep_skipped_entirely_when_youtube_
    # uploads_disabled below for the disabled-mode behavior this same gate now has.
    monkeypatch.setattr(auto_pilot.upload_manager, "YOUTUBE_UPLOADS_ENABLED", True)
    video_path = _touch(tmp_path / "live_chunk_1.ts")
    wav_path = _touch(tmp_path / "live_chunk_1.wav")
    transcription_path = _touch(tmp_path / "live_chunk_1_transcription.json")
    clips_path = _touch(tmp_path / "live_chunk_1_clips.json", json.dumps({"clips": []}))

    _common_mocks(monkeypatch, tmp_path, video_path, wav_path, transcription_path, clips_path)

    # Phase 1-3 helpers must NOT be touched -- nothing was rendered this cycle.
    def boom(*a, **k):
        raise AssertionError("Phase 1-3 helpers should not run when there are no new clips")
    monkeypatch.setattr(auto_pilot, "_trim_to_batch", boom)
    monkeypatch.setattr(auto_pilot.process_module, "process_clips_iter", boom)
    monkeypatch.setattr(auto_pilot.train_loop, "run_training_loop", boom)
    monkeypatch.setattr(auto_pilot, "purge_low_scoring_clips", boom)

    backlog_calls = []
    yt_retry_calls = []
    ig_retry_calls = []
    monkeypatch.setattr(auto_pilot, "find_backlog_clips", lambda *a, **k: backlog_calls.append((a, k)) or [])
    monkeypatch.setattr(
        auto_pilot, "retry_missing_youtube_uploads",
        lambda *a, **k: yt_retry_calls.append((a, k)) or (0, 0),
    )
    monkeypatch.setattr(
        auto_pilot, "retry_missing_instagram_uploads",
        lambda *a, **k: ig_retry_calls.append((a, k)) or (0, 0),
    )

    kept, deleted, uploaded = auto_pilot.run_cycle(
        video_path=video_path, profile=None, layout="split_screen", video_format="9:16",
        highlight_color="#FFFFFF", purge_threshold=-2, critic_model="gemini-x", cycle=1,
        target_streamer="eliasn97", kept_total=0, purged_total=0, uploaded_total=0,
        live=True, auto_upload=True, publish=True, streamer_handle="eliasn97",
    )

    assert (kept, deleted, uploaded) == (0, 0, 0)
    assert len(backlog_calls) == 1, "find_backlog_clips() must still run on a content-quiet cycle"
    assert len(yt_retry_calls) == 1, "YouTube retry sweep must still run on a content-quiet cycle"
    assert len(ig_retry_calls) == 1, "Instagram retry sweep must still run on a content-quiet cycle"


def test_youtube_retry_sweep_skipped_entirely_when_youtube_uploads_disabled(tmp_path, monkeypatch):
    # TikTok-only mode (2026-08-25): with upload_manager.YOUTUBE_UPLOADS_ENABLED off, run_cycle()
    # must not even call retry_missing_youtube_uploads() — every candidate would otherwise no-op
    # and get logged as "failed" every cycle forever, since a disabled leg never becomes
    # youtube_uploaded=True and so never ages out of find_missing_youtube_uploads(). The
    # Instagram sweep is untouched by this toggle and must still run.
    monkeypatch.setattr(auto_pilot.upload_manager, "YOUTUBE_UPLOADS_ENABLED", False)
    video_path = _touch(tmp_path / "live_chunk_1.ts")
    wav_path = _touch(tmp_path / "live_chunk_1.wav")
    transcription_path = _touch(tmp_path / "live_chunk_1_transcription.json")
    clips_path = _touch(tmp_path / "live_chunk_1_clips.json", json.dumps({"clips": []}))

    _common_mocks(monkeypatch, tmp_path, video_path, wav_path, transcription_path, clips_path)
    monkeypatch.setattr(auto_pilot, "_trim_to_batch", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(auto_pilot, "find_backlog_clips", lambda *a, **k: [])

    ig_retry_calls = []
    monkeypatch.setattr(auto_pilot, "retry_missing_youtube_uploads", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("YouTube retry sweep must not run when YOUTUBE_UPLOADS_ENABLED is False")
    ))
    monkeypatch.setattr(
        auto_pilot, "retry_missing_instagram_uploads",
        lambda *a, **k: ig_retry_calls.append((a, k)) or (0, 0),
    )

    kept, deleted, uploaded = auto_pilot.run_cycle(
        video_path=video_path, profile=None, layout="split_screen", video_format="9:16",
        highlight_color="#FFFFFF", purge_threshold=-2, critic_model="gemini-x", cycle=1,
        target_streamer="eliasn97", kept_total=0, purged_total=0, uploaded_total=0,
        live=True, auto_upload=True, publish=True, streamer_handle="eliasn97",
    )

    assert (kept, deleted, uploaded) == (0, 0, 0)
    assert len(ig_retry_calls) == 1, "Instagram retry sweep must still run regardless of the YouTube toggle"


def test_run_cycle_uploads_a_real_backlog_clip_even_with_no_new_clips(tmp_path, monkeypatch):
    # The actual incident this fix closes: an already-rendered, already-scored backlog clip
    # sitting in output/ must still get a fresh upload attempt on a cycle with no new clips.
    video_path = _touch(tmp_path / "live_chunk_1.ts")
    wav_path = _touch(tmp_path / "live_chunk_1.wav")
    transcription_path = _touch(tmp_path / "live_chunk_1_transcription.json")
    clips_path = _touch(tmp_path / "live_chunk_1_clips.json", json.dumps({"clips": []}))

    _common_mocks(monkeypatch, tmp_path, video_path, wav_path, transcription_path, clips_path)
    monkeypatch.setattr(auto_pilot, "retry_missing_youtube_uploads", lambda *a, **k: (0, 0))
    monkeypatch.setattr(auto_pilot, "retry_missing_instagram_uploads", lambda *a, **k: (0, 0))

    backlog_clip = ({"title": "Stuck backlog clip"}, tmp_path / "output" / "clip_1.mp4", 3)
    monkeypatch.setattr(auto_pilot, "find_backlog_clips", lambda *a, **k: [backlog_clip])

    deployment_calls = []

    def fake_deploy(deploy_batch, publish, instagram, streamer_name=None):
        deployment_calls.append(deploy_batch)
        return 1, 0

    monkeypatch.setattr(auto_pilot, "run_deployment_phase", fake_deploy)

    kept, deleted, uploaded = auto_pilot.run_cycle(
        video_path=video_path, profile=None, layout="split_screen", video_format="9:16",
        highlight_color="#FFFFFF", purge_threshold=-2, critic_model="gemini-x", cycle=1,
        target_streamer="eliasn97", kept_total=0, purged_total=0, uploaded_total=0,
        live=True, auto_upload=True, publish=True, streamer_handle="eliasn97",
    )

    assert len(deployment_calls) == 1
    assert deployment_calls[0] == [backlog_clip]
    assert uploaded == 1


def test_run_cycle_skips_deployment_entirely_when_auto_upload_off(tmp_path, monkeypatch):
    # Unchanged behavior: auto_upload=False must not touch find_backlog_clips/retry sweeps at
    # all, empty cycle or not.
    video_path = _touch(tmp_path / "live_chunk_1.ts")
    wav_path = _touch(tmp_path / "live_chunk_1.wav")
    transcription_path = _touch(tmp_path / "live_chunk_1_transcription.json")
    clips_path = _touch(tmp_path / "live_chunk_1_clips.json", json.dumps({"clips": []}))

    _common_mocks(monkeypatch, tmp_path, video_path, wav_path, transcription_path, clips_path)

    def boom(*a, **k):
        raise AssertionError("must not run when auto_upload is False")
    monkeypatch.setattr(auto_pilot, "find_backlog_clips", boom)
    monkeypatch.setattr(auto_pilot, "retry_missing_youtube_uploads", boom)
    monkeypatch.setattr(auto_pilot, "retry_missing_instagram_uploads", boom)

    kept, deleted, uploaded = auto_pilot.run_cycle(
        video_path=video_path, profile=None, layout="split_screen", video_format="9:16",
        highlight_color="#FFFFFF", purge_threshold=-2, critic_model="gemini-x", cycle=1,
        target_streamer="eliasn97", kept_total=0, purged_total=0, uploaded_total=0,
        live=True, auto_upload=False, publish=False, streamer_handle="eliasn97",
    )

    assert (kept, deleted, uploaded) == (0, 0, 0)
