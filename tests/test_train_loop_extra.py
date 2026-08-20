import json
from datetime import datetime, timedelta, timezone

import train_loop


def _verdict(title, score=5):
    return train_loop.ClipVerdict(clip_title=title, reward_score=score, reasoning="x")


# --- filter_verdicts_to_known_clips (M-04) -----------------------------------------------

def test_keeps_verdicts_matching_real_clips():
    clips = [{"title": "A"}, {"title": "B"}]
    batch = train_loop.CriticBatch(verdicts=[_verdict("A"), _verdict("B")])
    result = train_loop.filter_verdicts_to_known_clips(batch, clips)
    assert [v.clip_title for v in result.verdicts] == ["A", "B"]


def test_drops_hallucinated_verdict_title():
    clips = [{"title": "A"}]
    batch = train_loop.CriticBatch(verdicts=[_verdict("A"), _verdict("Nonexistent Clip")])
    result = train_loop.filter_verdicts_to_known_clips(batch, clips)
    assert [v.clip_title for v in result.verdicts] == ["A"]


def test_drops_duplicate_verdict_keeping_first():
    clips = [{"title": "A"}]
    batch = train_loop.CriticBatch(verdicts=[_verdict("A", score=3), _verdict("A", score=9)])
    result = train_loop.filter_verdicts_to_known_clips(batch, clips)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].reward_score == 3


def test_empty_batch_stays_empty():
    clips = [{"title": "A"}]
    result = train_loop.filter_verdicts_to_known_clips(train_loop.CriticBatch(verdicts=[]), clips)
    assert result.verdicts == []


# --- ai_guidelines.txt rule capping (L-04) -----------------------------------------------

def test_cap_rules_leaves_short_list_untouched():
    rules = ["a", "b", "c"]
    assert train_loop._cap_rules(rules, max_count=10) == rules


def test_cap_rules_keeps_most_recent_when_over_limit():
    rules = [f"rule-{i}" for i in range(10)]
    capped = train_loop._cap_rules(rules, max_count=3)
    assert capped == ["rule-7", "rule-8", "rule-9"]


# --- load_viral_memory_section age-gating (found live 2026-08-18: viral_memory.json was
# full of 0-view entries checked minutes after upload — TikTok hadn't distributed them yet,
# not a real flop signal, but load_viral_memory_section() used to hand it to the critic
# unfiltered as if it meant something) -----------------------------------------------------

def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_old_enough_true_past_threshold():
    entry = {"uploaded_at": _iso(48)}
    assert train_loop._old_enough_for_viral_signal(entry, datetime.now(timezone.utc)) is True


def test_old_enough_false_when_too_fresh():
    entry = {"uploaded_at": _iso(1)}
    assert train_loop._old_enough_for_viral_signal(entry, datetime.now(timezone.utc)) is False


def test_old_enough_false_when_uploaded_at_missing():
    # Ambiguous timing must never be treated as a green light — same principle as
    # metrics_tracker.prune_viral_memory's handling of a missing checked_at.
    assert train_loop._old_enough_for_viral_signal({}, datetime.now(timezone.utc)) is False


def test_old_enough_false_on_naive_uploaded_at_does_not_raise():
    # Regression: naive (no-timezone) uploaded_at parses fine via fromisoformat, but
    # subtracting it from an aware `now` raises TypeError, not ValueError — this must not
    # crash the whole critic run over one hand-edited or malformed viral_memory.json entry.
    naive = (datetime.now() - timedelta(hours=48)).isoformat()  # no tzinfo
    assert train_loop._old_enough_for_viral_signal({"uploaded_at": naive}, datetime.now(timezone.utc)) is False


def test_old_enough_false_on_non_string_uploaded_at_does_not_raise():
    assert train_loop._old_enough_for_viral_signal({"uploaded_at": 12345}, datetime.now(timezone.utc)) is False


def test_viral_memory_section_excludes_fresh_zero_view_clips(tmp_path, monkeypatch):
    memory_path = tmp_path / "viral_memory.json"
    memory_path.write_text(json.dumps({
        "fresh_clip": {
            "title": "Fresh Clip", "viral_score": 8, "energy_rating": 8,
            "views": 0, "likes": 0, "uploaded_at": _iso(1),
        },
    }), encoding="utf-8")
    monkeypatch.setattr(train_loop, "VIRAL_MEMORY_PATH", memory_path)

    assert train_loop.load_viral_memory_section() == ""


def test_viral_memory_section_includes_old_enough_clips(tmp_path, monkeypatch):
    memory_path = tmp_path / "viral_memory.json"
    memory_path.write_text(json.dumps({
        "old_clip": {
            "title": "Old Clip", "viral_score": 9, "energy_rating": 9,
            "views": 15000, "likes": 900, "uploaded_at": _iso(48),
        },
    }), encoding="utf-8")
    monkeypatch.setattr(train_loop, "VIRAL_MEMORY_PATH", memory_path)

    section = train_loop.load_viral_memory_section()
    assert "Old Clip" in section
    assert "15000 views" in section


# --- per-platform metrics (2026-08-21: metrics_tracker.py started recording YouTube
# view/like counts alongside TikTok's; _entry_has_metrics/_format_platform_metrics must keep
# reading pre-existing entries written in the old flat views/likes shape too, since some of
# those can never be rewritten -- their uploaded_clips/ sidecar is already gone by the time a
# clip is confirmed twice on TikTok, see metrics_tracker._delete_confirmed_upload()) ---------

def test_format_platform_metrics_shows_both_platforms_when_both_present():
    entry = {"tiktok_views": 100, "tiktok_likes": 10, "youtube_views": 50, "youtube_likes": 5}
    result = train_loop._format_platform_metrics(entry)
    assert "TikTok 100 views/10 likes" in result
    assert "YouTube 50 views/5 likes" in result


def test_format_platform_metrics_omits_platform_with_no_data():
    entry = {"tiktok_views": 100, "tiktok_likes": 10}
    result = train_loop._format_platform_metrics(entry)
    assert "TikTok" in result
    assert "YouTube" not in result


def test_format_platform_metrics_falls_back_to_legacy_flat_keys():
    # Pre-2026-08-21 shape, some of which can never be migrated -- see module comment above.
    entry = {"views": 15000, "likes": 900}
    result = train_loop._format_platform_metrics(entry)
    assert result == "TikTok 15000 views/900 likes"


def test_entry_has_metrics_true_for_new_schema_youtube_only():
    assert train_loop._entry_has_metrics({"youtube_views": 10}) is True


def test_entry_has_metrics_true_for_legacy_flat_schema():
    assert train_loop._entry_has_metrics({"views": 10}) is True


def test_entry_has_metrics_false_when_nothing_checked_yet():
    assert train_loop._entry_has_metrics({"title": "Unchecked Clip"}) is False


def test_viral_memory_section_includes_youtube_only_clip(tmp_path, monkeypatch):
    memory_path = tmp_path / "viral_memory.json"
    memory_path.write_text(json.dumps({
        "yt_clip": {
            "title": "YouTube Only Clip", "viral_score": 7, "energy_rating": 6,
            "youtube_views": 4200, "youtube_likes": 300, "uploaded_at": _iso(48),
        },
    }), encoding="utf-8")
    monkeypatch.setattr(train_loop, "VIRAL_MEMORY_PATH", memory_path)

    section = train_loop.load_viral_memory_section()
    assert "YouTube Only Clip" in section
    assert "YouTube 4200 views/300 likes" in section


# --- load_accepted_clips_section (2026-08-19: fallback signal for viral_pattern_rule
# generation when no real TikTok performance data exists yet — "we have overnight data now"
# — real metrics take real time (VIRAL_SIGNAL_MIN_AGE_HOURS) to mean anything, but a clip
# the critic already scored highly and that made it to a confirmed publish is still a
# weaker, but meaningful, signal worth reinforcing in the meantime) -------------------------

def test_accepted_clips_section_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(train_loop, "UPLOADED_CLIPS_DIR", tmp_path / "nope")
    assert train_loop.load_accepted_clips_section() == ""


def test_accepted_clips_section_includes_confirmed_high_score_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(train_loop, "UPLOADED_CLIPS_DIR", tmp_path)
    (tmp_path / "clip_1.json").write_text(json.dumps({
        "title": "Great Clip", "viral_score": 9, "energy_rating": 8, "confirmed": True,
    }), encoding="utf-8")

    section = train_loop.load_accepted_clips_section()

    assert "Great Clip" in section
    assert "viral_score=9" in section


def test_accepted_clips_section_excludes_unconfirmed_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(train_loop, "UPLOADED_CLIPS_DIR", tmp_path)
    (tmp_path / "clip_1.json").write_text(json.dumps({
        "title": "Unconfirmed Clip", "viral_score": 9, "confirmed": False,
    }), encoding="utf-8")

    assert train_loop.load_accepted_clips_section() == ""


def test_accepted_clips_section_excludes_low_score_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(train_loop, "UPLOADED_CLIPS_DIR", tmp_path)
    (tmp_path / "clip_1.json").write_text(json.dumps({
        "title": "Meh Clip", "viral_score": 4, "confirmed": True,
    }), encoding="utf-8")

    assert train_loop.load_accepted_clips_section() == ""


def test_accepted_clips_section_ignores_unreadable_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(train_loop, "UPLOADED_CLIPS_DIR", tmp_path)
    (tmp_path / "corrupt.json").write_text("{not valid json", encoding="utf-8")
    (tmp_path / "good.json").write_text(json.dumps({
        "title": "Good Clip", "viral_score": 8, "confirmed": True,
    }), encoding="utf-8")

    section = train_loop.load_accepted_clips_section()

    assert "Good Clip" in section


def test_cap_rules_exact_boundary():
    rules = ["a", "b", "c"]
    assert train_loop._cap_rules(rules, max_count=3) == rules
