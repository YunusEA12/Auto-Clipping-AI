"""Unit coverage for upload_ledger.py's own primitives, independent of any specific platform
caller — see upload_manager.py's own tests (test_upload_manager.py) for how these get used in
practice by _upload_to_tiktok()/_upload_to_youtube()/_upload_to_instagram()."""

import pytest

import upload_ledger

# LEDGER_PATH isolation is handled globally by tests/conftest.py's autouse
# _isolated_upload_ledger fixture — every test here already gets its own tmp_path-scoped
# upload_ledger.json with no per-file setup needed.


@pytest.fixture
def real_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"some real bytes")
    return path


def test_compute_content_hash_is_stable_for_the_same_bytes(tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"identical content")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"identical content")

    assert upload_ledger.compute_content_hash(a) == upload_ledger.compute_content_hash(b)


def test_compute_content_hash_differs_for_different_bytes(tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"content one")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"content two")

    assert upload_ledger.compute_content_hash(a) != upload_ledger.compute_content_hash(b)


def test_is_done_false_for_never_seen_hash():
    assert upload_ledger.is_done("nonexistent-hash", "youtube") is False


def test_mark_done_then_is_done_true():
    upload_ledger.mark_done("hash1", "youtube", video_id="abc")
    assert upload_ledger.is_done("hash1", "youtube") is True
    assert upload_ledger.get_entry("hash1", "youtube")["video_id"] == "abc"


def test_mark_failed_is_not_done():
    upload_ledger.mark_failed("hash1", "youtube", detail="quota exceeded")
    assert upload_ledger.is_done("hash1", "youtube") is False
    assert upload_ledger.get_entry("hash1", "youtube")["status"] == "failed"


def test_mark_unresolved_is_not_done():
    upload_ledger.mark_unresolved("hash1", "instagram", detail="clicked but unconfirmed")
    assert upload_ledger.is_done("hash1", "instagram") is False
    assert upload_ledger.get_entry("hash1", "instagram")["status"] == "pending"


def test_platforms_are_tracked_independently_per_hash():
    upload_ledger.mark_done("hash1", "youtube", video_id="abc")
    upload_ledger.mark_failed("hash1", "instagram", detail="nope")

    assert upload_ledger.is_done("hash1", "youtube") is True
    assert upload_ledger.is_done("hash1", "instagram") is False


def test_try_mark_pending_succeeds_when_nothing_recorded_yet():
    assert upload_ledger.try_mark_pending("hash1", "youtube") is True
    assert upload_ledger.get_entry("hash1", "youtube")["status"] == "pending"


def test_try_mark_pending_blocks_a_fresh_concurrent_attempt():
    assert upload_ledger.try_mark_pending("hash1", "youtube") is True
    # A second call for the exact same (hash, platform) shortly after — simulates a second
    # worker or a retry racing the first attempt's still-unresolved pending marker.
    assert upload_ledger.try_mark_pending("hash1", "youtube") is False


def test_try_mark_pending_allows_retry_after_a_stale_pending_entry():
    stale_time = upload_ledger.datetime.now(upload_ledger.timezone.utc) - upload_ledger.timedelta(
        minutes=upload_ledger.PENDING_STALE_MINUTES + 1
    )
    upload_ledger._save_ledger({"hash1": {"youtube": {"status": "pending", "updated_at": stale_time.isoformat()}}})

    assert upload_ledger.try_mark_pending("hash1", "youtube") is True


def test_try_mark_pending_allows_a_fresh_attempt_after_a_done_entry():
    # try_mark_pending() itself doesn't refuse to re-reserve a "done" entry (callers are
    # expected to check is_done() first and short-circuit before ever reaching here) — this
    # just documents that it isn't the thing enforcing that; is_done() is.
    upload_ledger.mark_done("hash1", "youtube", video_id="abc")
    assert upload_ledger.try_mark_pending("hash1", "youtube") is True


def test_try_mark_pending_allows_a_fresh_attempt_after_a_failed_entry():
    upload_ledger.mark_failed("hash1", "youtube", detail="network blip")
    assert upload_ledger.try_mark_pending("hash1", "youtube") is True


def test_lock_acquisition_failure_fails_open(monkeypatch):
    """If the ledger's file lock can't be acquired (extreme contention, a stuck lock file),
    try_mark_pending() must fail OPEN — proceed without a reservation — rather than silently
    blocking every future upload forever just because one lock attempt timed out."""
    def raise_timeout(*a, **k):
        raise upload_ledger.Timeout("simulated lock contention")
    monkeypatch.setattr(upload_ledger, "_ledger_lock", lambda: _RaisingLock(raise_timeout))

    assert upload_ledger.try_mark_pending("hash1", "youtube") is True


class _RaisingLock:
    def __init__(self, raiser):
        self._raiser = raiser

    def __enter__(self):
        self._raiser()

    def __exit__(self, *args):
        return False
