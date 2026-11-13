import pytest

import get_cookies


class _FakeRequiresAdminError(Exception):
    """Stand-in for shadowcopy.exceptions.RequiresAdminError, used when that package isn't
    importable in this environment (non-Windows test runners) — patched in via
    get_cookies.RequiresAdminError so the specific-message branch is still exercised."""


def _loader_raising(exc):
    def _loader(domain_name):
        raise exc
    return _loader


def test_generic_failure_falls_through_to_generic_message(monkeypatch):
    monkeypatch.setattr(get_cookies, "BROWSER_LOADERS", [("chrome", _loader_raising(OSError("boom")))])
    with pytest.raises(RuntimeError, match="Could not find any tiktok.com cookies"):
        get_cookies._load_tiktok_cookiejar()


def test_admin_required_failure_gets_specific_message(monkeypatch):
    monkeypatch.setattr(get_cookies, "RequiresAdminError", _FakeRequiresAdminError)
    monkeypatch.setattr(
        get_cookies, "BROWSER_LOADERS", [("chrome", _loader_raising(_FakeRequiresAdminError("needs admin")))]
    )
    with pytest.raises(RuntimeError, match="admin rights"):
        get_cookies._load_tiktok_cookiejar()


def test_admin_required_message_names_the_browser(monkeypatch):
    monkeypatch.setattr(get_cookies, "RequiresAdminError", _FakeRequiresAdminError)
    monkeypatch.setattr(
        get_cookies, "BROWSER_LOADERS", [("chrome", _loader_raising(_FakeRequiresAdminError("needs admin")))]
    )
    with pytest.raises(RuntimeError, match="chrome"):
        get_cookies._load_tiktok_cookiejar()


def test_second_browser_succeeds_after_first_needs_admin(monkeypatch):
    # _load_tiktok_cookiejar() only ever calls len() on what a loader returns — a real
    # http.cookiejar.CookieJar isn't needed to exercise the fallback-to-next-browser path.
    monkeypatch.setattr(get_cookies, "RequiresAdminError", _FakeRequiresAdminError)

    def working_loader(domain_name):
        return ["fake_cookie"]

    monkeypatch.setattr(
        get_cookies, "BROWSER_LOADERS",
        [("chrome", _loader_raising(_FakeRequiresAdminError("needs admin"))), ("edge", working_loader)],
    )

    label, result_jar = get_cookies._load_tiktok_cookiejar()
    assert label == "edge"
    assert len(result_jar) == 1


def test_app_bound_encryption_points_at_the_manual_login(monkeypatch):
    """Chrome 127+ ties the cookie key to the browser binary, so no amount of closing the
    browser or elevating fixes it — the message has to send the user somewhere that works."""
    monkeypatch.setattr(
        get_cookies, "BROWSER_LOADERS",
        [("chrome", _loader_raising(Exception("Unable to get key for cookie decryption")))],
    )
    with pytest.raises(RuntimeError, match="--manual-login"):
        get_cookies._load_tiktok_cookiejar()


def test_app_bound_encryption_rules_out_the_admin_workaround(monkeypatch):
    """The other failure branch tells the user to elevate, which here would fail in exactly
    the same way — so this message has to say outright that it won't help, rather than
    leaving them to try it."""
    monkeypatch.setattr(
        get_cookies, "BROWSER_LOADERS",
        [("chrome", _loader_raising(Exception("Failed to decrypt cookie value")))],
    )
    with pytest.raises(RuntimeError) as excinfo:
        get_cookies._load_tiktok_cookiejar()
    assert "admin rights does not help" in str(excinfo.value)


def test_app_bound_failure_still_tries_the_next_browser(monkeypatch):
    monkeypatch.setattr(
        get_cookies, "BROWSER_LOADERS",
        [
            ("chrome", _loader_raising(Exception("Unable to get key for cookie decryption"))),
            ("firefox", lambda domain_name: ["a cookie"]),
        ],
    )
    label, jar = get_cookies._load_tiktok_cookiejar()
    assert label == "firefox"
    assert len(jar) == 1
