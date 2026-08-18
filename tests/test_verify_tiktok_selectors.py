import json

import pytest

import tiktok_uploader
import verify_tiktok_selectors as vts


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


@pytest.fixture
def isolated_cookies_path(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    monkeypatch.setattr(tiktok_uploader, "COOKIES_PATH", path)
    return path


def test_declines_by_default(monkeypatch, isolated_cookies_path):
    monkeypatch.setattr("builtins.input", lambda _: "")  # empty/default answer
    context = FakeContext([{"name": "sessionid", "domain": ".tiktok.com", "value": "x"}])
    vts._offer_to_save_session_cookies(context)
    assert not isolated_cookies_path.exists()


def test_declines_on_explicit_no(monkeypatch, isolated_cookies_path):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    context = FakeContext([{"name": "sessionid", "domain": ".tiktok.com", "value": "x"}])
    vts._offer_to_save_session_cookies(context)
    assert not isolated_cookies_path.exists()


def test_saves_only_tiktok_cookies_on_yes(monkeypatch, isolated_cookies_path):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    context = FakeContext([
        {"name": "sessionid", "domain": ".tiktok.com", "value": "abc"},
        {"name": "other", "domain": ".tiktok.com", "value": "def"},
        {"name": "unrelated", "domain": ".google.com", "value": "ghi"},
    ])
    vts._offer_to_save_session_cookies(context)

    saved = json.loads(isolated_cookies_path.read_text(encoding="utf-8"))
    assert {c["name"] for c in saved} == {"sessionid", "other"}


def test_does_not_save_without_a_sessionid_cookie(monkeypatch, isolated_cookies_path):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    context = FakeContext([{"name": "csrftoken", "domain": ".tiktok.com", "value": "x"}])
    vts._offer_to_save_session_cookies(context)
    assert not isolated_cookies_path.exists()


def test_manual_login_flag_requires_visible_browser():
    # argparse.error() calls sys.exit(2) — exercised via the real CLI (a subprocess) rather
    # than reimplementing argparse's own validation logic here.
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "verify_tiktok_selectors.py", "--manual-login", "--headless"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "--manual-login needs a visible browser" in result.stderr
