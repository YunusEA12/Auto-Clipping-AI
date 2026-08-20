"""2026-08-20: upload.py gained a per-clip YouTube Shorts entry point (upload_clip()) for the
new multi-platform pipeline (upload_manager.py) — distinct from the pre-existing manual/batch
CLI (upload_video()/upload_all()), which stays untouched (still defaults to private)."""

from pathlib import Path

import pytest

import upload


# --- _append_shorts_tag (pure function) ------------------------------------------------------

def test_append_shorts_tag_adds_tag_when_absent():
    assert upload._append_shorts_tag("Cool clip") == "Cool clip #shorts"


def test_append_shorts_tag_does_not_duplicate_when_already_present():
    assert upload._append_shorts_tag("Cool clip #shorts") == "Cool clip #shorts"


def test_append_shorts_tag_case_insensitive_duplicate_check():
    assert upload._append_shorts_tag("Cool clip #Shorts") == "Cool clip #Shorts"


def test_append_shorts_tag_respects_max_length_by_trimming_base_text():
    long_title = "x" * 120
    result = upload._append_shorts_tag(long_title, max_length=upload.TITLE_MAX_LENGTH)
    assert len(result) <= upload.TITLE_MAX_LENGTH
    assert result.endswith("#shorts")


def test_append_shorts_tag_no_truncation_when_under_max_length():
    result = upload._append_shorts_tag("Short title", max_length=upload.TITLE_MAX_LENGTH)
    assert result == "Short title #shorts"


def test_append_shorts_tag_existing_tag_still_truncated_to_max_length():
    long_title_with_tag = ("x" * 110) + " #shorts"
    result = upload._append_shorts_tag(long_title_with_tag, max_length=upload.TITLE_MAX_LENGTH)
    assert len(result) <= upload.TITLE_MAX_LENGTH


# --- upload_clip() body construction ----------------------------------------------------------

class FakeRequest:
    def __init__(self, video_id="abc123"):
        self._video_id = video_id
        self.next_chunk_calls = 0

    def next_chunk(self):
        self.next_chunk_calls += 1
        return None, {"id": self._video_id}


class FakeVideosResource:
    def __init__(self, captured):
        self._captured = captured

    def insert(self, part, body, media_body):
        self._captured["part"] = part
        self._captured["body"] = body
        self._captured["media_body"] = media_body
        return FakeRequest()


class FakeYouTubeClient:
    def __init__(self, captured):
        self._captured = captured

    def videos(self):
        return FakeVideosResource(self._captured)


@pytest.fixture
def fake_youtube(monkeypatch):
    captured = {}
    monkeypatch.setattr(upload, "get_authenticated_service", lambda: FakeYouTubeClient(captured))
    return captured


def test_upload_clip_appends_shorts_tag_to_title_and_truncates(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    long_title = "A" * 120

    video_id = upload.upload_clip(video, long_title, "desc")

    assert video_id == "abc123"
    title = fake_youtube["body"]["snippet"]["title"]
    assert len(title) <= upload.TITLE_MAX_LENGTH
    assert title.endswith("#shorts")


def test_upload_clip_appends_shorts_tag_to_description(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    upload.upload_clip(video, "Title", "A cool moment")

    assert "#shorts" in fake_youtube["body"]["snippet"]["description"]


def test_upload_clip_falls_back_to_title_when_description_empty(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    upload.upload_clip(video, "My Title", "")

    assert "My Title" in fake_youtube["body"]["snippet"]["description"]


def test_upload_clip_sets_public_privacy_by_default(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    upload.upload_clip(video, "Title", "desc")

    assert fake_youtube["body"]["status"]["privacyStatus"] == "public"


def test_upload_clip_respects_explicit_privacy_status(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    upload.upload_clip(video, "Title", "desc", privacy_status="private")

    assert fake_youtube["body"]["status"]["privacyStatus"] == "private"


def test_upload_clip_strips_leading_hash_from_tags(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    upload.upload_clip(video, "Title", "desc", tags=["#gaming", "#fyp"])

    tags = fake_youtube["body"]["snippet"]["tags"]
    assert "gaming" in tags
    assert "fyp" in tags
    assert not any(t.startswith("#") for t in tags)


def test_upload_clip_always_includes_shorts_in_tags_without_duplicating(fake_youtube, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    upload.upload_clip(video, "Title", "desc", tags=["#shorts", "gaming"])

    tags = fake_youtube["body"]["snippet"]["tags"]
    assert tags.count("shorts") == 1


# --- upload_all()/upload_video() untouched (2026-08-20: still the manual/batch CLI path,
# deliberately left at its existing private default) -------------------------------------------

def test_upload_video_still_defaults_to_private():
    assert upload.PRIVACY_STATUS == "private"
