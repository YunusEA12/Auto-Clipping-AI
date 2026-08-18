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


def test_cap_rules_exact_boundary():
    rules = ["a", "b", "c"]
    assert train_loop._cap_rules(rules, max_count=3) == rules
