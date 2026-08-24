"""2026-08-25: scripts/login_instagram.py resolves Instagram's account-chooser interstitial
(see upload_instagram_playwright._is_account_chooser_interstitial()'s own docstring for the
incident) with a human watching a real, visible browser — these tests cover the script's own
control flow (loading/saving cookies, the wait-for-resolution loop, the timeout path) against a
minimal fake Playwright page/context, not a real browser or a real Instagram session."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import login_instagram
import upload_instagram_playwright as igp


class FakePage:
    def __init__(self, urls, interstitial_flags):
        # Each call to goto()/the passage of "time" (poll iterations) advances through these
        # lists — url/interstitial state at the CURRENT poll iteration.
        self._urls = list(urls)
        self._interstitial_flags = list(interstitial_flags)
        self._index = 0

    def goto(self, url, wait_until=None):
        pass

    @property
    def url(self):
        return self._urls[min(self._index, len(self._urls) - 1)]

    def _advance(self):
        self._index += 1


class FakeContext:
    def __init__(self, cookies_to_return):
        self.added_cookies = None
        self._cookies_to_return = cookies_to_return

    def add_cookies(self, cookies):
        self.added_cookies = cookies

    def new_page(self):
        return self._page

    def cookies(self):
        return self._cookies_to_return


class FakeBrowser:
    def __init__(self, context):
        self._context = context
        self.closed = False

    def new_context(self, viewport=None):
        return self._context

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, browser):
        self._browser = browser

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def chromium(self):
        return self

    def launch(self, headless=None):
        self.launched_headless = headless
        return self._browser


def _install_fake_playwright(monkeypatch, page, context, browser):
    context._page = page
    monkeypatch.setattr(login_instagram, "sync_playwright", lambda: FakePlaywright(browser))


def _run_with_argv(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["login_instagram.py"] + args)
    return login_instagram.main()


def test_resolved_immediately_saves_cookies(tmp_path, monkeypatch):
    monkeypatch.setattr(igp, "COOKIES_PATH", tmp_path / "instagram_cookies.json")
    monkeypatch.setattr(igp, "load_cookies", lambda: [{"name": "sessionid", "value": "old"}])
    monkeypatch.setattr(igp, "_is_account_chooser_interstitial", lambda page: False)
    monkeypatch.setattr(igp, "_dismiss_blocking_overlays", lambda page: None)

    page = FakePage(urls=["https://www.instagram.com/"], interstitial_flags=[False])
    fresh_cookies = [{"name": "sessionid", "value": "new"}]
    context = FakeContext(cookies_to_return=fresh_cookies)
    browser = FakeBrowser(context)
    _install_fake_playwright(monkeypatch, page, context, browser)

    exit_code = _run_with_argv(monkeypatch, [])

    assert exit_code == 0
    assert context.added_cookies == [{"name": "sessionid", "value": "old"}]
    assert browser.closed is True
    assert igp.COOKIES_PATH.exists()
    import json
    assert json.loads(igp.COOKIES_PATH.read_text()) == fresh_cookies


def test_no_existing_cookies_starts_from_blank_session(tmp_path, monkeypatch):
    monkeypatch.setattr(igp, "COOKIES_PATH", tmp_path / "instagram_cookies.json")
    monkeypatch.setattr(igp, "load_cookies", lambda: None)
    monkeypatch.setattr(igp, "_is_account_chooser_interstitial", lambda page: False)
    monkeypatch.setattr(igp, "_dismiss_blocking_overlays", lambda page: None)

    page = FakePage(urls=["https://www.instagram.com/"], interstitial_flags=[False])
    context = FakeContext(cookies_to_return=[{"name": "sessionid", "value": "brand-new"}])
    browser = FakeBrowser(context)
    _install_fake_playwright(monkeypatch, page, context, browser)

    exit_code = _run_with_argv(monkeypatch, [])

    assert exit_code == 0
    assert context.added_cookies is None  # nothing to seed with


def test_timeout_returns_error_and_saves_nothing(tmp_path, monkeypatch):
    cookies_path = tmp_path / "instagram_cookies.json"
    monkeypatch.setattr(igp, "COOKIES_PATH", cookies_path)
    monkeypatch.setattr(igp, "load_cookies", lambda: None)
    # Never resolves -- always reports the interstitial is still up.
    monkeypatch.setattr(igp, "_is_account_chooser_interstitial", lambda page: True)
    monkeypatch.setattr(igp, "_dismiss_blocking_overlays", lambda page: None)
    monkeypatch.setattr(login_instagram.time, "sleep", lambda s: None)  # don't actually wait

    page = FakePage(urls=["https://www.instagram.com/"], interstitial_flags=[True])
    context = FakeContext(cookies_to_return=[{"name": "sessionid", "value": "x"}])
    browser = FakeBrowser(context)
    _install_fake_playwright(monkeypatch, page, context, browser)

    exit_code = _run_with_argv(monkeypatch, ["--timeout", "0"])

    assert exit_code == 1
    assert not cookies_path.exists()
    assert browser.closed is True  # still cleaned up despite the timeout


def test_login_page_url_blocks_resolution_until_left(tmp_path, monkeypatch):
    monkeypatch.setattr(igp, "COOKIES_PATH", tmp_path / "instagram_cookies.json")
    monkeypatch.setattr(igp, "load_cookies", lambda: None)
    monkeypatch.setattr(igp, "_is_account_chooser_interstitial", lambda page: False)
    monkeypatch.setattr(igp, "_dismiss_blocking_overlays", lambda page: None)
    monkeypatch.setattr(login_instagram.time, "sleep", lambda s: page._advance())

    # Starts on the login page (must NOT count as resolved even though the interstitial
    # check itself returns False) -- only counts once the URL actually moves off it.
    page = FakePage(
        urls=["https://www.instagram.com/accounts/login/", "https://www.instagram.com/"],
        interstitial_flags=[False, False],
    )
    context = FakeContext(cookies_to_return=[{"name": "sessionid", "value": "after-login"}])
    browser = FakeBrowser(context)
    _install_fake_playwright(monkeypatch, page, context, browser)

    exit_code = _run_with_argv(monkeypatch, ["--timeout", "10"])

    assert exit_code == 0
