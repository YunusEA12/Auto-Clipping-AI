"""2026-08-20: upload.py gained a per-clip YouTube Shorts entry point (upload_clip()) for the
new multi-platform pipeline (upload_manager.py) — distinct from the pre-existing manual/batch
CLI (upload_video()/upload_all()), which stays untouched (still defaults to private)."""

import pytest
from googleapiclient.errors import HttpError

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


# --- get_stats_service() (2026-08-21: read-only YouTube metrics fetch for
# metrics_tracker.py, deliberately separate from get_authenticated_service() above so it can
# never fall into the interactive OAuth flow that would hang forever on a headless VPS) -------

# Captured at import time, before conftest.py's autouse _block_real_youtube_calls fixture
# replaces upload.get_stats_service with a raiser for every other test in the suite -- these
# tests genuinely need to exercise the real function body, so real_get_stats_service below
# restores it (a locally-requested fixture's monkeypatch.setattr() applies after the autouse
# fixture's, same pattern fake_youtube already relies on for get_authenticated_service).
_real_get_stats_service = upload.get_stats_service
_real_get_authenticated_service = upload.get_authenticated_service


@pytest.fixture
def real_get_stats_service(monkeypatch):
    monkeypatch.setattr(upload, "get_stats_service", _real_get_stats_service)


@pytest.fixture
def real_get_authenticated_service(monkeypatch):
    monkeypatch.setattr(upload, "get_authenticated_service", _real_get_authenticated_service)


class FakeCreds:
    def __init__(self, valid, expired=False, refresh_token=None, raise_on_refresh=False, scopes=None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refreshed = False
        self._raise_on_refresh = raise_on_refresh
        # Defaults to fully-scoped (matches real Credentials always having .scopes) — a real
        # google.oauth2.credentials.Credentials always has this attribute, so a fixture that
        # lacked it entirely didn't match reality; tests specifically about scope-mismatch
        # pass scopes=[...] explicitly instead.
        self.scopes = list(upload.SCOPES) if scopes is None else scopes

    def refresh(self, request):
        if self._raise_on_refresh:
            raise Exception("network error")
        self.refreshed = True
        self.valid = True

    def to_json(self):
        return "{}"


def test_get_stats_service_returns_none_when_no_token_file(tmp_path, monkeypatch, real_get_stats_service):
    monkeypatch.setattr(upload, "TOKEN_PATH", tmp_path / "missing_token.json")
    assert upload.get_stats_service() is None


def test_get_stats_service_returns_none_on_unreadable_token(tmp_path, monkeypatch, real_get_stats_service):
    token_path = tmp_path / "token.json"
    token_path.write_text("not valid json", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    assert upload.get_stats_service() is None


def test_get_stats_service_builds_client_for_already_valid_token(tmp_path, monkeypatch, real_get_stats_service):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    monkeypatch.setattr(
        upload.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: FakeCreds(valid=True)),
    )
    sentinel = object()
    monkeypatch.setattr(upload, "build", lambda *a, **k: sentinel)

    assert upload.get_stats_service() is sentinel


def test_get_stats_service_refreshes_expired_token_non_interactively(tmp_path, monkeypatch, real_get_stats_service):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    fake_creds = FakeCreds(valid=False, expired=True, refresh_token="rt")
    monkeypatch.setattr(
        upload.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: fake_creds),
    )
    monkeypatch.setattr(upload, "build", lambda *a, **k: object())

    result = upload.get_stats_service()

    assert result is not None
    assert fake_creds.refreshed is True
    assert token_path.read_text(encoding="utf-8") == "{}"  # re-saved after refresh


# --- scope-mismatch detection (2026-08-21, found in review after live-debugging this exact
# 403 earlier the same session: a stored token issued before SCOPES grew a new entry keeps
# whatever it was originally granted forever, and refresh() can't add a scope) --------------

def test_get_stats_service_returns_none_when_token_missing_required_scope(tmp_path, monkeypatch, real_get_stats_service):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    # Only youtube.upload, missing youtube.readonly — exactly the pre-fix token.json shape.
    stale_creds = FakeCreds(valid=True, scopes=["https://www.googleapis.com/auth/youtube.upload"])
    monkeypatch.setattr(
        upload.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: stale_creds),
    )
    monkeypatch.setattr(upload, "build", lambda *a, **k: object())

    assert upload.get_stats_service() is None


def test_get_authenticated_service_still_returns_a_client_when_scope_missing(tmp_path, monkeypatch, real_get_authenticated_service):
    # get_authenticated_service() (the real upload path) warns but doesn't fail outright on a
    # scope-insufficient token — forcing reauth here would hang on a headless VPS with no
    # browser, the exact problem get_stats_service() was split out to avoid.
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    client_secret_path = tmp_path / "client_secret.json"
    client_secret_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    monkeypatch.setattr(upload, "CLIENT_SECRET_PATH", client_secret_path)
    stale_creds = FakeCreds(valid=True, scopes=["https://www.googleapis.com/auth/youtube.upload"])
    monkeypatch.setattr(
        upload.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: stale_creds),
    )
    sentinel = object()
    monkeypatch.setattr(upload, "build", lambda *a, **k: sentinel)

    assert upload.get_authenticated_service() is sentinel


def test_missing_scopes_empty_when_all_scopes_present():
    creds = FakeCreds(valid=True)  # defaults to fully-scoped
    assert upload._missing_scopes(creds) == set()


def test_get_stats_service_returns_none_when_expired_without_refresh_token(tmp_path, monkeypatch, real_get_stats_service):
    # Must NOT fall through to the interactive flow.run_local_server() flow -- that would hang
    # forever on a headless VPS with no browser to complete it in.
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    fake_creds = FakeCreds(valid=False, expired=True, refresh_token=None)
    monkeypatch.setattr(
        upload.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: fake_creds),
    )

    assert upload.get_stats_service() is None


def test_get_stats_service_returns_none_when_refresh_raises(tmp_path, monkeypatch, real_get_stats_service):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(upload, "TOKEN_PATH", token_path)
    fake_creds = FakeCreds(valid=False, expired=True, refresh_token="rt", raise_on_refresh=True)
    monkeypatch.setattr(
        upload.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: fake_creds),
    )

    assert upload.get_stats_service() is None


# --- fetch_video_stats() (2026-08-21) ---------------------------------------------------------

def _fake_http_error(status=500):
    class Resp:
        reason = "Server Error"
    Resp.status = status
    return HttpError(Resp(), b"error content")


class _FakeVideosListRequest:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._response


class _FakeVideosResource:
    """Returns one canned response/error per call, in order."""
    def __init__(self, responses=None, errors=None):
        self._queue = list(responses or [])
        self._error_queue = list(errors or [])
        self.calls = []

    def list(self, part, id):
        self.calls.append({"part": part, "id": id})
        if self._error_queue:
            return _FakeVideosListRequest(error=self._error_queue.pop(0))
        return _FakeVideosListRequest(response=self._queue.pop(0))


class _FakeYouTubeClient:
    def __init__(self, responses=None, errors=None):
        self.resource = _FakeVideosResource(responses, errors)

    def videos(self):
        return self.resource


def test_fetch_video_stats_parses_view_and_like_counts():
    youtube = _FakeYouTubeClient(responses=[
        {"items": [{"id": "abc", "statistics": {"viewCount": "1234", "likeCount": "56"}}]}
    ])

    stats = upload.fetch_video_stats(youtube, ["abc"])

    assert stats == {"abc": {"views": 1234, "likes": 56}}


def test_fetch_video_stats_missing_like_count_is_none_not_zero():
    # A creator can hide their like count -- absent means "unknown", not "zero likes".
    youtube = _FakeYouTubeClient(responses=[{"items": [{"id": "abc", "statistics": {"viewCount": "10"}}]}])

    stats = upload.fetch_video_stats(youtube, ["abc"])

    assert stats["abc"]["likes"] is None


def test_fetch_video_stats_drops_ids_youtube_did_not_return():
    # A deleted video, or one the API otherwise can't return, just silently isn't in the result.
    youtube = _FakeYouTubeClient(responses=[{"items": []}])

    assert upload.fetch_video_stats(youtube, ["missing_id"]) == {}


def test_fetch_video_stats_deduplicates_ids():
    youtube = _FakeYouTubeClient(responses=[{"items": [{"id": "abc", "statistics": {"viewCount": "1"}}]}])

    upload.fetch_video_stats(youtube, ["abc", "abc"])

    assert youtube.resource.calls[0]["id"] == "abc"


def test_fetch_video_stats_batches_over_the_api_limit():
    ids = [f"id{i}" for i in range(upload.STATS_BATCH_SIZE + 5)]
    youtube = _FakeYouTubeClient(responses=[{"items": []}, {"items": []}])

    upload.fetch_video_stats(youtube, ids)

    assert len(youtube.resource.calls) == 2
    assert len(youtube.resource.calls[0]["id"].split(",")) == upload.STATS_BATCH_SIZE
    assert len(youtube.resource.calls[1]["id"].split(",")) == 5


def test_fetch_video_stats_continues_after_a_failed_batch():
    ids = [f"id{i}" for i in range(upload.STATS_BATCH_SIZE)] + ["ok_id"]
    youtube = _FakeYouTubeClient(
        responses=[{"items": [{"id": "ok_id", "statistics": {"viewCount": "5"}}]}],
        errors=[_fake_http_error()],
    )

    stats = upload.fetch_video_stats(youtube, ids)

    assert stats == {"ok_id": {"views": 5, "likes": None}}
    assert len(youtube.resource.calls) == 2
