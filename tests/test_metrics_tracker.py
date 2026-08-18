import json
from datetime import datetime, timedelta, timezone

import metrics_tracker


# --- embedded-JSON content extraction (C-01: real per-row DOM has no views/likes words at
# all, only the column headers do, so this replaced the old text-pattern-regex scrape
# entirely) -------------------------------------------------------------------------------

# Trimmed-down but structurally real shape of TikTok Studio's __Creator_Center_Context__
# script tag payload, confirmed against a real content-list page snapshot on 2026-08-18 —
# not synthesized from guesswork.
REAL_SHAPE_CONTEXT_PAYLOAD = {
    "firstBatchQueryItems": {
        "cursor": 5,
        "has_more": False,
        "item_list": [
            {
                "desc": "clip_3_Preise_die_schockieren #fyp #viral",
                "play_count": "1234",
                "like_count": "56",
                "comment_count": "3",
                "share_count": "1",
                "item_id": "7675442700526570774",
                "status": 102,
                "visibility": 1,
            },
        ],
    },
}


def test_rows_from_context_extracts_real_shaped_payload():
    rows = metrics_tracker._rows_from_context(REAL_SHAPE_CONTEXT_PAYLOAD)
    assert rows == [{"caption": "clip_3_Preise_die_schockieren #fyp #viral", "views": 1234, "likes": 56}]


def test_rows_from_context_handles_empty_item_list():
    assert metrics_tracker._rows_from_context({"firstBatchQueryItems": {"item_list": []}}) == []


def test_rows_from_context_handles_missing_key_entirely():
    assert metrics_tracker._rows_from_context({}) == []


def test_rows_from_context_skips_malformed_items_not_the_whole_batch():
    payload = {
        "firstBatchQueryItems": {
            "item_list": [
                "not a dict",
                {"desc": "real one", "play_count": "10", "like_count": "2"},
            ]
        }
    }
    rows = metrics_tracker._rows_from_context(payload)
    assert rows == [{"caption": "real one", "views": 10, "likes": 2}]


def test_rows_from_context_defaults_missing_counts_to_none():
    payload = {"firstBatchQueryItems": {"item_list": [{"desc": "no counts here"}]}}
    rows = metrics_tracker._rows_from_context(payload)
    assert rows == [{"caption": "no counts here", "views": None, "likes": None}]


def test_coerce_int_handles_string_numbers_and_none():
    assert metrics_tracker._coerce_int("42") == 42
    assert metrics_tracker._coerce_int(None) is None
    assert metrics_tracker._coerce_int("not a number") is None


class _FakeScriptLocator:
    def __init__(self, text):
        self._text = text

    def text_content(self, timeout=None):
        return self._text


class _FakePage:
    def __init__(self, script_text):
        self._script_text = script_text

    def locator(self, selector):
        assert selector == f"script#{metrics_tracker.CONTEXT_SCRIPT_ID}"
        return _FakeScriptLocator(self._script_text)


def test_extract_context_json_parses_html_entity_escaped_json():
    # This is the real shape: <script> is an HTML "raw text" element, so the browser never
    # decodes entities inside it — .text_content() returns literal &quot; instead of ",
    # confirmed live (2026-08-18). A test feeding clean json.dumps() output would never catch
    # a regression here, since it skips exactly the step that needs testing (html.unescape());
    # that's exactly how this bug shipped once already, caught only by an actual live run.
    import html as html_module
    escaped_text = html_module.escape(json.dumps(REAL_SHAPE_CONTEXT_PAYLOAD), quote=True)
    assert "&quot;" in escaped_text  # sanity: prove this test actually exercises escaped input
    page = _FakePage(escaped_text)
    result = metrics_tracker._extract_context_json(page)
    assert result == REAL_SHAPE_CONTEXT_PAYLOAD


def test_extract_context_json_also_handles_already_clean_json():
    # Not the real-world shape, but should still work if TikTok ever stops double-escaping.
    page = _FakePage(json.dumps(REAL_SHAPE_CONTEXT_PAYLOAD))
    result = metrics_tracker._extract_context_json(page)
    assert result == REAL_SHAPE_CONTEXT_PAYLOAD


def test_extract_context_json_returns_none_on_missing_script():
    page = _FakePage(None)
    assert metrics_tracker._extract_context_json(page) is None


def test_extract_context_json_returns_none_on_invalid_json():
    page = _FakePage("{not valid json")
    assert metrics_tracker._extract_context_json(page) is None


def test_extract_context_json_returns_none_on_non_dict_json():
    page = _FakePage("[1, 2, 3]")
    assert metrics_tracker._extract_context_json(page) is None


# --- local-file deletion, gated on TWO separate confirmed matches, not one (safety-model
# correction, 2026-08-18: a click-based "confirmed" from tiktok_uploader.py was proven
# unreliable the same day — a post can be silently held private/under review while reporting
# success — so a single content-list match isn't trusted as sole grounds for permanent,
# unrecoverable deletion either; two independent matches across separate poll cycles is) ---

def test_delete_confirmed_upload_removes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_tracker, "UPLOADED_CLIPS_DIR", tmp_path)
    (tmp_path / "clip_1.mp4").write_bytes(b"fake video")
    (tmp_path / "clip_1.json").write_text("{}", encoding="utf-8")

    metrics_tracker._delete_confirmed_upload("clip_1")

    assert not (tmp_path / "clip_1.mp4").exists()
    assert not (tmp_path / "clip_1.json").exists()


def test_delete_confirmed_upload_never_raises_on_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_tracker, "UPLOADED_CLIPS_DIR", tmp_path)
    metrics_tracker._delete_confirmed_upload("does_not_exist")  # must not raise


def test_delete_confirmed_upload_does_not_touch_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_tracker, "UPLOADED_CLIPS_DIR", tmp_path)
    (tmp_path / "clip_1.mp4").write_bytes(b"fake video")
    (tmp_path / "clip_2.mp4").write_bytes(b"a different clip")
    (tmp_path / "clip_2.json").write_text("{}", encoding="utf-8")

    metrics_tracker._delete_confirmed_upload("clip_1")

    assert not (tmp_path / "clip_1.mp4").exists()
    assert (tmp_path / "clip_2.mp4").exists()
    assert (tmp_path / "clip_2.json").exists()


def test_update_viral_memory_does_not_delete_on_first_match(tmp_path, monkeypatch):
    uploaded_dir = tmp_path / "uploaded_clips"
    uploaded_dir.mkdir()
    monkeypatch.setattr(metrics_tracker, "UPLOADED_CLIPS_DIR", uploaded_dir)
    monkeypatch.setattr(metrics_tracker, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(metrics_tracker, "SCRAPE_HEALTH_PATH", tmp_path / "health.json")

    (uploaded_dir / "clip_1.mp4").write_bytes(b"fake video")
    (uploaded_dir / "clip_1.json").write_text(json.dumps({"caption": "hello world #fyp"}), encoding="utf-8")

    monkeypatch.setattr(
        metrics_tracker, "fetch_content_list",
        lambda headless=True: [{"caption": "hello world #fyp", "views": 5, "likes": 1}],
    )

    matched = metrics_tracker.update_viral_memory()

    assert matched == 1
    # First sighting only — not deleted yet, even though it matched.
    assert (uploaded_dir / "clip_1.mp4").exists()
    assert (uploaded_dir / "clip_1.json").exists()


def test_update_viral_memory_deletes_on_second_consecutive_match(tmp_path, monkeypatch):
    uploaded_dir = tmp_path / "uploaded_clips"
    uploaded_dir.mkdir()
    monkeypatch.setattr(metrics_tracker, "UPLOADED_CLIPS_DIR", uploaded_dir)
    monkeypatch.setattr(metrics_tracker, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(metrics_tracker, "SCRAPE_HEALTH_PATH", tmp_path / "health.json")

    (uploaded_dir / "clip_1.mp4").write_bytes(b"fake video")
    (uploaded_dir / "clip_1.json").write_text(json.dumps({"caption": "hello world #fyp"}), encoding="utf-8")

    monkeypatch.setattr(
        metrics_tracker, "fetch_content_list",
        lambda headless=True: [{"caption": "hello world #fyp", "views": 5, "likes": 1}],
    )

    metrics_tracker.update_viral_memory()  # first pass: matched, not deleted
    matched = metrics_tracker.update_viral_memory()  # second pass: matched again -> deleted

    assert matched == 1
    assert not (uploaded_dir / "clip_1.mp4").exists()
    assert not (uploaded_dir / "clip_1.json").exists()
    # The view/like history must survive the local-file deletion.
    memory = metrics_tracker.load_viral_memory(tmp_path / "viral_memory.json")
    assert "clip_1" in memory


def test_update_viral_memory_never_deletes_an_unmatched_clip(tmp_path, monkeypatch):
    uploaded_dir = tmp_path / "uploaded_clips"
    uploaded_dir.mkdir()
    monkeypatch.setattr(metrics_tracker, "UPLOADED_CLIPS_DIR", uploaded_dir)
    monkeypatch.setattr(metrics_tracker, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(metrics_tracker, "SCRAPE_HEALTH_PATH", tmp_path / "health.json")

    (uploaded_dir / "clip_1.mp4").write_bytes(b"fake video")
    (uploaded_dir / "clip_1.json").write_text(json.dumps({"caption": "never matches anything"}), encoding="utf-8")

    monkeypatch.setattr(
        metrics_tracker, "fetch_content_list",
        lambda headless=True: [{"caption": "totally unrelated content", "views": 5, "likes": 1}],
    )

    metrics_tracker.update_viral_memory()
    metrics_tracker.update_viral_memory()

    assert (uploaded_dir / "clip_1.mp4").exists()
    assert (uploaded_dir / "clip_1.json").exists()


# --- default-path monkeypatch actually takes effect (late-binding regression guard) -------
# A plain `path: Path = VIRAL_MEMORY_PATH` default captures the value once at function
# definition time — monkeypatching metrics_tracker.VIRAL_MEMORY_PATH afterward would
# silently have no effect, and the function would keep writing to the real project file.
# This bit for real once already this session (streamers.py) before being caught and fixed.

def test_load_viral_memory_respects_monkeypatched_default_path(tmp_path, monkeypatch):
    path = tmp_path / "viral_memory.json"
    path.write_text('{"a": {"views": 1}}', encoding="utf-8")
    monkeypatch.setattr(metrics_tracker, "VIRAL_MEMORY_PATH", path)
    assert metrics_tracker.load_viral_memory() == {"a": {"views": 1}}


def test_save_viral_memory_respects_monkeypatched_default_path(tmp_path, monkeypatch):
    path = tmp_path / "viral_memory.json"
    monkeypatch.setattr(metrics_tracker, "VIRAL_MEMORY_PATH", path)
    metrics_tracker.save_viral_memory({"a": {"views": 1}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": {"views": 1}}


def test_scrape_health_respects_monkeypatched_default_path(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    monkeypatch.setattr(metrics_tracker, "SCRAPE_HEALTH_PATH", path)
    metrics_tracker._record_scrape_result(success=False)
    assert path.exists()
    assert metrics_tracker.load_scrape_health()["consecutive_failures"] == 1


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
