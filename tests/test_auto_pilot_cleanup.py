"""2026-08-19: raw .ts chunks, extracted .wav files, and per-cycle transcript/clips JSON
temp files used to never get cleaned up in --live mode, accumulating forever on disk over a
24/7 run. _cleanup_cycle_temp_files() closes that, gated to live=True only — non-live (VOD)
mode deliberately reuses these same paths across cycles via stem-based caching in
ingest.extract_audio()/transcribe.transcribe() (see L-05 in the audit), so deleting them
there would silently force a full re-extraction/re-transcription every cycle. Called from a
finally block in run_cycle() so it runs on every exit path, not just the happy one."""

import pytest

import auto_pilot


def _touch(path):
    path.write_text("x", encoding="utf-8")
    return path


def test_cleanup_deletes_all_four_paths_in_live_mode(tmp_path):
    video_path = _touch(tmp_path / "live_chunk_123.ts")
    wav_path = _touch(tmp_path / "live_chunk_123.wav")
    transcription_path = _touch(tmp_path / "live_chunk_123_transcription.json")
    clips_path = _touch(tmp_path / "live_chunk_123_clips.json")

    auto_pilot._cleanup_cycle_temp_files(video_path, wav_path, transcription_path, clips_path, live=True)

    assert not video_path.exists()
    assert not wav_path.exists()
    assert not transcription_path.exists()
    assert not clips_path.exists()


def test_cleanup_does_nothing_in_non_live_mode(tmp_path):
    # Non-live (VOD/--video) mode deliberately reuses these paths across cycles.
    video_path = _touch(tmp_path / "my_video.mp4")
    wav_path = _touch(tmp_path / "my_video.wav")
    transcription_path = _touch(tmp_path / "my_video_transcription.json")
    clips_path = _touch(tmp_path / "my_video_clips.json")

    auto_pilot._cleanup_cycle_temp_files(video_path, wav_path, transcription_path, clips_path, live=False)

    assert video_path.exists()
    assert wav_path.exists()
    assert transcription_path.exists()
    assert clips_path.exists()


def test_cleanup_tolerates_already_missing_files(tmp_path):
    missing = tmp_path / "gone.ts"
    # Must not raise even though the file was never created.
    auto_pilot._cleanup_cycle_temp_files(missing, missing, missing, missing, live=True)


# --- run_cycle() still cleans up when analyze.analyze() itself raises (2026-08-21: found
# live -- the try/finally used to start AFTER `clips_path = analyze.analyze(...)`, so when
# that call raised (e.g. a missing/invalid GEMINI_API_KEY -> a non-retryable 401), the
# finally's _cleanup_cycle_temp_files() never ran at all for that cycle. Confirmed by dozens
# of orphaned live_chunk_*.ts/.wav/*_transcription.json files that accumulated, unbounded,
# every 90s-cooldown retry for as long as the failure persisted.) -------------------------

def test_run_cycle_cleans_up_temp_files_when_analysis_raises(tmp_path, monkeypatch):
    video_path = _touch(tmp_path / "live_chunk_1.ts")
    wav_path = _touch(tmp_path / "live_chunk_1.wav")
    transcription_path = _touch(tmp_path / "live_chunk_1_transcription.json")

    monkeypatch.setattr(auto_pilot, "update_agent_state", lambda **kw: {})
    monkeypatch.setattr(auto_pilot.ingest, "extract_audio", lambda video_path: wav_path)
    monkeypatch.setattr(auto_pilot.transcribe, "transcribe", lambda wav_path: transcription_path)

    class FakeAnalysisError(Exception):
        pass

    def _raise(*args, **kwargs):
        raise FakeAnalysisError("401 UNAUTHENTICATED — missing/invalid GEMINI_API_KEY")
    monkeypatch.setattr(auto_pilot.analyze, "analyze", _raise)

    cleanup_calls = []
    monkeypatch.setattr(
        auto_pilot, "_cleanup_cycle_temp_files", lambda *args: cleanup_calls.append(args),
    )

    with pytest.raises(FakeAnalysisError):
        auto_pilot.run_cycle(
            video_path=video_path, profile=None, layout="split_screen", video_format="9:16",
            highlight_color="#FFFFFF", purge_threshold=-2, critic_model="gemini-x", cycle=1,
            target_streamer="eliasn97", kept_total=0, purged_total=0, uploaded_total=0,
            live=True, auto_upload=False, publish=False,
        )

    assert len(cleanup_calls) == 1
    called_video, called_wav, called_transcription, called_clips, called_live = cleanup_calls[0]
    assert called_video == video_path
    assert called_wav == wav_path
    assert called_transcription == transcription_path
    assert called_clips is None  # analyze.analyze() raised before this was ever assigned
    assert called_live is True
