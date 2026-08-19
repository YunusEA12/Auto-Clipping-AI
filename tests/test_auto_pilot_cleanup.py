"""2026-08-19: raw .ts chunks, extracted .wav files, and per-cycle transcript/clips JSON
temp files used to never get cleaned up in --live mode, accumulating forever on disk over a
24/7 run. _cleanup_cycle_temp_files() closes that, gated to live=True only — non-live (VOD)
mode deliberately reuses these same paths across cycles via stem-based caching in
ingest.extract_audio()/transcribe.transcribe() (see L-05 in the audit), so deleting them
there would silently force a full re-extraction/re-transcription every cycle. Called from a
finally block in run_cycle() so it runs on every exit path, not just the happy one."""

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
