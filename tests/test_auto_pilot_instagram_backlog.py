"""2026-08-22 upload-parity audit finding: upload_clip_everywhere() attempts TikTok, YouTube,
and Instagram independently per clip, but only TikTok's outcome decides whether a clip moves
into uploaded_clips/ at all (run_deployment_phase's own docstring: "based on TikTok's result
alone"). YouTube already had a backlog-retry sweep for exactly this gap
(find_missing_youtube_uploads/retry_missing_youtube_uploads, see
test_auto_pilot_youtube_backlog.py) — Instagram had none at all until this same audit added
find_missing_instagram_uploads()/retry_missing_instagram_uploads() as its direct mirror. These
tests mirror that file's own structure and guard the same core property: a retry must NEVER
re-touch TikTok, and (Instagram-specific) a "clicked but unconfirmed" outcome must NOT be
treated as done — only success AND confirmed marks instagram_uploaded."""

import json

import auto_pilot


def _write_uploaded(uploaded_dir, name, sidecar_data):
    uploaded_dir.mkdir(parents=True, exist_ok=True)
    mp4 = uploaded_dir / f"{name}.mp4"
    mp4.write_bytes(b"fake video")
    (uploaded_dir / f"{name}.json").write_text(json.dumps(sidecar_data), encoding="utf-8")
    return mp4


# --- find_missing_instagram_uploads --------------------------------------------------------

def test_finds_clip_missing_instagram_upload(tmp_path):
    mp4 = _write_uploaded(tmp_path, "clip_1_A", {"title": "A", "instagram_enabled": True, "instagram_uploaded": False})
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == [mp4]


def test_finds_clip_where_instagram_uploaded_key_is_absent(tmp_path):
    # Sidecars written before the Instagram leg existed at all have no key -- must still count.
    mp4 = _write_uploaded(tmp_path, "clip_1_Old", {"title": "Old", "instagram_enabled": True})
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == [mp4]


def test_skips_clip_already_on_instagram(tmp_path):
    _write_uploaded(tmp_path, "clip_1_B", {"title": "B", "instagram_enabled": True, "instagram_uploaded": True})
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == []


def test_skips_clip_with_instagram_not_enabled(tmp_path):
    # The overwhelming majority of real ledger "instagram: MISSING" entries are this case --
    # a streamer that never opted in, not a dropped upload. Must not be treated as a gap.
    _write_uploaded(tmp_path, "clip_1_C", {"title": "C", "instagram_enabled": False, "instagram_uploaded": False})
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == []


def test_skips_clip_with_instagram_enabled_key_absent(tmp_path):
    # A clip from before this streamer turned Instagram on at all, or before the feature
    # existed -- absent means "not opted in", not "gap to retry".
    _write_uploaded(tmp_path, "clip_1_D", {"title": "D", "instagram_uploaded": False})
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == []


def test_skips_missing_sidecar_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "orphan.mp4").write_bytes(b"fake")
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == []


def test_skips_corrupt_sidecar_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "clip_1_Bad.mp4").write_bytes(b"fake")
    (tmp_path / "clip_1_Bad.json").write_text("{not json", encoding="utf-8")
    assert auto_pilot.find_missing_instagram_uploads(tmp_path) == []


def test_missing_uploaded_clips_dir_is_empty(tmp_path):
    assert auto_pilot.find_missing_instagram_uploads(tmp_path / "does_not_exist") == []


# --- streamer_name scoping (same reasoning as the YouTube sweep) --------------------------

def test_streamer_filter_excludes_another_streamers_clip(tmp_path):
    mp4 = _write_uploaded(tmp_path, "clip_1_Mine", {
        "title": "Mine", "instagram_enabled": True, "instagram_uploaded": False, "streamer_name": "alice",
    })
    _write_uploaded(tmp_path, "clip_1_Theirs", {
        "title": "Theirs", "instagram_enabled": True, "instagram_uploaded": False, "streamer_name": "bob",
    })
    assert auto_pilot.find_missing_instagram_uploads(tmp_path, streamer_name="alice") == [mp4]


def test_streamer_filter_still_includes_unowned_legacy_clips(tmp_path):
    mp4 = _write_uploaded(tmp_path, "clip_1_Legacy", {
        "title": "Legacy", "instagram_enabled": True, "instagram_uploaded": False,
    })
    assert auto_pilot.find_missing_instagram_uploads(tmp_path, streamer_name="alice") == [mp4]


# --- retry_missing_instagram_uploads -------------------------------------------------------

def test_retry_success_updates_sidecar_and_never_touches_tiktok(tmp_path, monkeypatch):
    _write_uploaded(tmp_path, "clip_1_D", {
        "title": "D", "description": "desc", "hashtags": ["#fyp"], "instagram_enabled": True, "instagram_uploaded": False,
    })

    tiktok_calls = []
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: tiktok_calls.append((a, k)),
    )
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_instagram",
        lambda *a, **k: auto_pilot.upload_manager.InstagramOutcome(attempted=True, success=True, confirmed=True),
    )

    ok, failed = auto_pilot.retry_missing_instagram_uploads(tmp_path)

    assert ok == 1
    assert failed == 0
    assert tiktok_calls == []  # never re-touched TikTok
    saved = json.loads((tmp_path / "clip_1_D.json").read_text(encoding="utf-8"))
    assert saved["instagram_uploaded"] is True


def test_retry_success_but_unconfirmed_is_not_marked_done(tmp_path, monkeypatch):
    # Instagram's own "clicked but no confirming signal seen" ambiguity -- must stay eligible
    # for another retry next cycle rather than being guessed as done.
    _write_uploaded(tmp_path, "clip_1_U", {
        "title": "U", "instagram_enabled": True, "instagram_uploaded": False,
    })
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_instagram",
        lambda *a, **k: auto_pilot.upload_manager.InstagramOutcome(attempted=True, success=True, confirmed=False),
    )

    ok, failed = auto_pilot.retry_missing_instagram_uploads(tmp_path)

    assert ok == 0
    assert failed == 1
    saved = json.loads((tmp_path / "clip_1_U.json").read_text(encoding="utf-8"))
    assert saved["instagram_uploaded"] is False


def test_retry_failure_leaves_sidecar_untouched(tmp_path, monkeypatch):
    _write_uploaded(tmp_path, "clip_1_E", {
        "title": "E", "instagram_enabled": True, "instagram_uploaded": False,
    })
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_instagram",
        lambda *a, **k: auto_pilot.upload_manager.InstagramOutcome(attempted=True, success=False, detail="crashed"),
    )

    ok, failed = auto_pilot.retry_missing_instagram_uploads(tmp_path)

    assert ok == 0
    assert failed == 1
    saved = json.loads((tmp_path / "clip_1_E.json").read_text(encoding="utf-8"))
    assert saved["instagram_uploaded"] is False  # unchanged, eligible for the next cycle's retry


def test_retry_respects_batch_limit(tmp_path, monkeypatch):
    for i in range(auto_pilot.INSTAGRAM_RETRY_BATCH_LIMIT + 3):
        _write_uploaded(tmp_path, f"clip_{i}_X", {"title": f"X{i}", "instagram_enabled": True, "instagram_uploaded": False})

    calls = []
    monkeypatch.setattr(
        auto_pilot.upload_manager, "_upload_to_instagram",
        lambda *a, **k: (calls.append(a) or auto_pilot.upload_manager.InstagramOutcome(attempted=True, success=True, confirmed=True)),
    )

    ok, failed = auto_pilot.retry_missing_instagram_uploads(tmp_path)

    assert ok == auto_pilot.INSTAGRAM_RETRY_BATCH_LIMIT
    assert len(calls) == auto_pilot.INSTAGRAM_RETRY_BATCH_LIMIT


def test_no_candidates_is_a_clean_noop(tmp_path):
    assert auto_pilot.retry_missing_instagram_uploads(tmp_path) == (0, 0)
