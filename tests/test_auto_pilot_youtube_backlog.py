"""2026-08-21: upload_manager.upload_clip_everywhere() attempts TikTok and YouTube
independently per clip, but only TikTok's outcome decides whether a clip moves into
uploaded_clips/ at all (run_deployment_phase's own docstring: "based on TikTok's result
alone"). That leaves a real gap: a clip whose YouTube leg failed (quota exceeded, a
transient error, an expired token) or was never attempted has no automatic retry once it's
archived there -- find_backlog_clips() only ever looks at output/, never uploaded_clips/.
find_missing_youtube_uploads()/retry_missing_youtube_uploads() close that gap. These tests
also guard the one property that matters most here: a retry must NEVER re-touch TikTok --
that clip is already live and public; a second TikTok attempt would post a duplicate."""

import json

import auto_pilot


def _write_uploaded(uploaded_dir, name, sidecar_data):
    uploaded_dir.mkdir(parents=True, exist_ok=True)
    mp4 = uploaded_dir / f"{name}.mp4"
    mp4.write_bytes(b"fake video")
    (uploaded_dir / f"{name}.json").write_text(json.dumps(sidecar_data), encoding="utf-8")
    return mp4


# --- find_missing_youtube_uploads ----------------------------------------------------------

def test_finds_clip_missing_youtube_upload(tmp_path):
    mp4 = _write_uploaded(tmp_path, "clip_1_A", {"title": "A", "publish": True, "youtube_uploaded": False})
    assert auto_pilot.find_missing_youtube_uploads(tmp_path) == [mp4]


def test_finds_clip_where_youtube_uploaded_key_is_absent(tmp_path):
    # Sidecars written before the YouTube leg existed at all have no key -- must still count.
    mp4 = _write_uploaded(tmp_path, "clip_1_Old", {"title": "Old", "publish": True})
    assert auto_pilot.find_missing_youtube_uploads(tmp_path) == [mp4]


def test_skips_clip_already_on_youtube(tmp_path):
    _write_uploaded(tmp_path, "clip_1_B", {"title": "B", "publish": True, "youtube_uploaded": True})
    assert auto_pilot.find_missing_youtube_uploads(tmp_path) == []


def test_skips_clip_with_publish_false(tmp_path):
    _write_uploaded(tmp_path, "clip_1_C", {"title": "C", "publish": False, "youtube_uploaded": False})
    assert auto_pilot.find_missing_youtube_uploads(tmp_path) == []


def test_skips_missing_sidecar_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "orphan.mp4").write_bytes(b"fake")
    assert auto_pilot.find_missing_youtube_uploads(tmp_path) == []


def test_skips_corrupt_sidecar_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "clip_1_Bad.mp4").write_bytes(b"fake")
    (tmp_path / "clip_1_Bad.json").write_text("{not json", encoding="utf-8")
    assert auto_pilot.find_missing_youtube_uploads(tmp_path) == []


def test_missing_uploaded_clips_dir_is_empty(tmp_path):
    assert auto_pilot.find_missing_youtube_uploads(tmp_path / "does_not_exist") == []


# --- retry_missing_youtube_uploads ---------------------------------------------------------

def test_retry_success_updates_sidecar_and_never_touches_tiktok(tmp_path, monkeypatch):
    _write_uploaded(tmp_path, "clip_1_D", {
        "title": "D", "description": "desc", "hashtags": ["#fyp"], "publish": True, "youtube_uploaded": False,
    })

    tiktok_calls = []
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: tiktok_calls.append((a, k)),
    )
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_youtube",
        lambda *a, **k: auto_pilot.upload_manager.YouTubeOutcome(attempted=True, success=True, video_id="abc123"),
    )

    ok, failed = auto_pilot.retry_missing_youtube_uploads(tmp_path)

    assert ok == 1
    assert failed == 0
    assert tiktok_calls == []  # never re-touched TikTok
    saved = json.loads((tmp_path / "clip_1_D.json").read_text(encoding="utf-8"))
    assert saved["youtube_uploaded"] is True
    assert saved["youtube_url"] == "https://youtu.be/abc123"


def test_retry_failure_leaves_sidecar_untouched(tmp_path, monkeypatch):
    _write_uploaded(tmp_path, "clip_1_E", {
        "title": "E", "publish": True, "youtube_uploaded": False,
    })
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_youtube",
        lambda *a, **k: auto_pilot.upload_manager.YouTubeOutcome(attempted=True, success=False, detail="quota exceeded"),
    )

    ok, failed = auto_pilot.retry_missing_youtube_uploads(tmp_path)

    assert ok == 0
    assert failed == 1
    saved = json.loads((tmp_path / "clip_1_E.json").read_text(encoding="utf-8"))
    assert saved["youtube_uploaded"] is False  # unchanged, eligible for the next cycle's retry


def test_retry_respects_batch_limit(tmp_path, monkeypatch):
    for i in range(auto_pilot.YOUTUBE_RETRY_BATCH_LIMIT + 3):
        _write_uploaded(tmp_path, f"clip_{i}_X", {"title": f"X{i}", "publish": True, "youtube_uploaded": False})

    calls = []
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_youtube",
        lambda *a, **k: (calls.append(a) or auto_pilot.upload_manager.YouTubeOutcome(attempted=True, success=True, video_id="x")),
    )

    ok, failed = auto_pilot.retry_missing_youtube_uploads(tmp_path)

    assert ok == auto_pilot.YOUTUBE_RETRY_BATCH_LIMIT
    assert len(calls) == auto_pilot.YOUTUBE_RETRY_BATCH_LIMIT


def test_no_candidates_is_a_clean_noop(tmp_path):
    assert auto_pilot.retry_missing_youtube_uploads(tmp_path) == (0, 0)
