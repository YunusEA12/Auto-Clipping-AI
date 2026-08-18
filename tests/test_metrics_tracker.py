import json
from datetime import datetime, timedelta, timezone

import metrics_tracker


# --- scrape health tracking (M-01) -----------------------------------------------------

def test_record_success_resets_streak(tmp_path):
    path = tmp_path / "health.json"
    metrics_tracker._record_scrape_result(success=False, path=path)
    metrics_tracker._record_scrape_result(success=False, path=path)
    health = metrics_tracker._record_scrape_result(success=True, path=path)
    assert health["consecutive_failures"] == 0
    assert health["last_success"] is not None


def test_record_failure_increments_streak(tmp_path):
    path = tmp_path / "health.json"
    for expected in (1, 2, 3):
        health = metrics_tracker._record_scrape_result(success=False, path=path)
        assert health["consecutive_failures"] == expected


def test_load_scrape_health_defaults_when_missing(tmp_path):
    health = metrics_tracker.load_scrape_health(tmp_path / "nope.json")
    assert health["consecutive_failures"] == 0


def test_load_scrape_health_recovers_from_corrupt_file(tmp_path):
    path = tmp_path / "health.json"
    path.write_text("{not valid json", encoding="utf-8")
    health = metrics_tracker.load_scrape_health(path)
    assert health["consecutive_failures"] == 0


# --- caption matching / indexed lookup equivalence (M-08) ------------------------------

def _brute_force_match(uploaded, content_rows):
    """The original O(N*M) approach — used as the reference implementation to confirm the
    indexed version in _match_uploaded_to_content_rows never disagrees with it."""
    result = {}
    for entry in uploaded:
        match = next(
            (row for row in content_rows if metrics_tracker._captions_match(entry.get("caption", ""), row["caption"])),
            None,
        )
        if match is not None:
            result[entry["_clip_id"]] = match
    return result


def test_indexed_match_agrees_with_brute_force_for_long_captions():
    long_caption_a = "This is a genuinely long TikTok caption " + "#fyp #viral #gaming #shorts #twitch"
    uploaded = [{"_clip_id": "a", "caption": long_caption_a}]
    content_rows = [{"caption": long_caption_a, "views": 100, "likes": 10}]

    assert metrics_tracker._match_uploaded_to_content_rows(uploaded, content_rows) == _brute_force_match(uploaded, content_rows)


def test_indexed_match_agrees_with_brute_force_for_short_captions():
    # Short captions (<40 chars) are exactly the case where a naive fixed-length bucket
    # would silently diverge from _captions_match()'s variable-length prefix comparison.
    uploaded = [{"_clip_id": "a", "caption": "short one"}]
    content_rows = [{"caption": "short one but scraped longer than the sidecar version", "views": 5, "likes": 1}]

    indexed = metrics_tracker._match_uploaded_to_content_rows(uploaded, content_rows)
    brute = _brute_force_match(uploaded, content_rows)
    assert indexed == brute
    assert "a" in indexed  # sanity: this pair genuinely should match


def test_indexed_match_no_false_positive_for_unrelated_short_captions():
    uploaded = [{"_clip_id": "a", "caption": "hello world"}]
    content_rows = [{"caption": "totally different", "views": 5, "likes": 1}]
    assert metrics_tracker._match_uploaded_to_content_rows(uploaded, content_rows) == {}


def test_indexed_match_handles_mixed_length_batch():
    long_caption = "x" * 60
    uploaded = [
        {"_clip_id": "long", "caption": long_caption},
        {"_clip_id": "short", "caption": "hi"},
    ]
    content_rows = [
        {"caption": long_caption, "views": 1, "likes": 1},
        {"caption": "hi there", "views": 2, "likes": 2},
    ]
    indexed = metrics_tracker._match_uploaded_to_content_rows(uploaded, content_rows)
    brute = _brute_force_match(uploaded, content_rows)
    assert indexed == brute
    assert set(indexed) == {"long", "short"}


# --- viral_memory.json pruning (L-04) ---------------------------------------------------

def test_prune_drops_entries_older_than_max_age():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    memory = {
        "old_clip": {"checked_at": old, "views": 1},
        "recent_clip": {"checked_at": recent, "views": 2},
    }
    pruned = metrics_tracker.prune_viral_memory(memory, max_age_days=180)
    assert set(pruned) == {"recent_clip"}


def test_prune_keeps_entries_with_no_checked_at():
    memory = {"mystery_clip": {"views": 1}}
    pruned = metrics_tracker.prune_viral_memory(memory, max_age_days=180)
    assert "mystery_clip" in pruned


def test_prune_keeps_entries_with_unparseable_checked_at():
    memory = {"weird_clip": {"checked_at": "not-a-date", "views": 1}}
    pruned = metrics_tracker.prune_viral_memory(memory, max_age_days=180)
    assert "weird_clip" in pruned
