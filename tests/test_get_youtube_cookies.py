import pytest

import get_youtube_cookies


class _FakeRequiresAdminError(Exception):
    """See test_get_cookies.py's identical stand-in for why this exists."""


class _FakeCookie:
    def __init__(self, name, value="v", domain=".youtube.com", path="/", secure=True, expires=None):
        self.name = name
        self.value = value
        self.domain = domain
        self.path = path
        self.secure = secure
        self.expires = expires


def _loader_raising(exc):
    def _loader(domain_name):
        raise exc
    return _loader


def test_generic_failure_falls_through_to_generic_message(monkeypatch):
    monkeypatch.setattr(get_youtube_cookies, "BROWSER_LOADERS", [("chrome", _loader_raising(OSError("boom")))])
    with pytest.raises(RuntimeError, match="Could not find any google.com/youtube.com cookies"):
        get_youtube_cookies._load_google_cookiejars()


def test_admin_required_failure_gets_specific_message(monkeypatch):
    monkeypatch.setattr(get_youtube_cookies, "RequiresAdminError", _FakeRequiresAdminError)
    monkeypatch.setattr(
        get_youtube_cookies, "BROWSER_LOADERS",
        [("chrome", _loader_raising(_FakeRequiresAdminError("needs admin")))],
    )
    with pytest.raises(RuntimeError, match="admin rights"):
        get_youtube_cookies._load_google_cookiejars()


def test_second_browser_succeeds_after_first_needs_admin(monkeypatch):
    monkeypatch.setattr(get_youtube_cookies, "RequiresAdminError", _FakeRequiresAdminError)

    def working_loader(domain_name):
        return [_FakeCookie("SID")]

    monkeypatch.setattr(
        get_youtube_cookies, "BROWSER_LOADERS",
        [("chrome", _loader_raising(_FakeRequiresAdminError("needs admin"))), ("edge", working_loader)],
    )

    label, jars = get_youtube_cookies._load_google_cookiejars()
    assert label == "edge"
    assert sum(len(jar) for jar in jars) == 2  # one per DOMAIN_FRAGMENTS entry


# --- extract_youtube_studio_cookies: dedup + multi-candidate required-cookie check --------

def test_dedupes_cookies_seen_under_both_domain_fragments(monkeypatch):
    shared = _FakeCookie("SAPISID", domain=".google.com")
    monkeypatch.setattr(
        get_youtube_cookies, "_load_google_cookiejars",
        lambda browser=None: ("chrome", [[shared], [shared]]),  # same cookie from both queries
    )
    cookies = get_youtube_cookies.extract_youtube_studio_cookies()
    assert len(cookies) == 1


def test_accepts_any_single_required_cookie_name(monkeypatch):
    monkeypatch.setattr(
        get_youtube_cookies, "_load_google_cookiejars",
        lambda browser=None: ("chrome", [[_FakeCookie("__Secure-1PSID")], []]),
    )
    cookies = get_youtube_cookies.extract_youtube_studio_cookies()
    assert cookies[0]["name"] == "__Secure-1PSID"


def test_raises_when_no_required_cookie_present(monkeypatch):
    monkeypatch.setattr(
        get_youtube_cookies, "_load_google_cookiejars",
        lambda browser=None: ("chrome", [[_FakeCookie("unrelated_cookie")], []]),
    )
    with pytest.raises(RuntimeError, match="not logged into a Google account"):
        get_youtube_cookies.extract_youtube_studio_cookies()
