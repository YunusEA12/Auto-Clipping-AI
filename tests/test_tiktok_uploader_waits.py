from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import tiktok_uploader


class FakeCaptionBox:
    """Mimics the subset of Playwright's Locator API _wait_for_caption_filled() uses."""

    def __init__(self, text_sequence):
        self._text_sequence = list(text_sequence)

    def text_content(self, timeout=None):
        if not self._text_sequence:
            return ""
        value = self._text_sequence.pop(0)
        if value is None:
            raise PlaywrightTimeoutError("no text yet")
        return value


class FakeFileInput:
    def __init__(self, present_for_calls):
        self._present_for_calls = present_for_calls
        self.calls = 0

    def count(self):
        self.calls += 1
        return 1 if self.calls <= self._present_for_calls else 0


class FakePage:
    """Mimics the subset of Playwright's Page API these waits use: a `.url` that can change
    between reads (simulating navigation) and `.wait_for_timeout()` as a no-op instead of a
    real sleep, so these tests run instantly."""

    def __init__(self, url_changes_after_reads=None):
        self._reads = 0
        self._url_changes_after_reads = url_changes_after_reads
        self.wait_calls = 0

    @property
    def url(self):
        self._reads += 1
        if self._url_changes_after_reads is not None and self._reads > self._url_changes_after_reads:
            return "https://www.tiktok.com/foryou"
        return tiktok_uploader.UPLOAD_URL

    def wait_for_timeout(self, ms):
        self.wait_calls += 1


# --- _wait_for_caption_filled (M-10) ------------------------------------------------------

def test_caption_wait_returns_as_soon_as_text_matches():
    page = FakePage()
    caption_box = FakeCaptionBox(["Cool clip! #fyp #viral"])
    tiktok_uploader._wait_for_caption_filled(page, caption_box, "Cool clip! #fyp #viral")
    assert page.wait_calls <= 1  # confirmed on (essentially) the first poll, not the full timeout


def test_caption_wait_falls_back_gracefully_when_never_confirmed():
    page = FakePage()
    caption_box = FakeCaptionBox([""] * 50)  # never matches, but never raises either
    # Must not raise — a best-effort wait degrades to "continue anyway", same pattern as
    # _wait_for_upload_complete's own fallback.
    tiktok_uploader._wait_for_caption_filled(page, caption_box, "Some caption text")
    expected_polls = tiktok_uploader.CAPTION_FILL_TIMEOUT_MS // tiktok_uploader.CAPTION_FILL_POLL_MS
    assert page.wait_calls == expected_polls


# --- _wait_for_post_confirmation (M-03) ---------------------------------------------------

def test_post_confirmation_true_on_redirect():
    page = FakePage(url_changes_after_reads=2)
    file_input = FakeFileInput(present_for_calls=1000)
    assert tiktok_uploader._wait_for_post_confirmation(page, file_input) is True


def test_post_confirmation_true_when_upload_form_disappears():
    page = FakePage(url_changes_after_reads=None)  # url never changes
    file_input = FakeFileInput(present_for_calls=1)  # disappears after the first check
    assert tiktok_uploader._wait_for_post_confirmation(page, file_input) is True


def test_post_confirmation_false_when_nothing_ever_signals_success():
    page = FakePage(url_changes_after_reads=None)
    file_input = FakeFileInput(present_for_calls=1000)
    assert tiktok_uploader._wait_for_post_confirmation(page, file_input) is False


# --- UploadOutcome shape (M-03) ------------------------------------------------------------

def test_upload_outcome_is_a_named_tuple_with_success_and_confirmed():
    outcome = tiktok_uploader.UploadOutcome(success=True, confirmed=False)
    assert outcome.success is True
    assert outcome.confirmed is False
    # Unpacking still works like a plain bool tuple, for any caller that only cares about [0]
    success, confirmed = outcome
    assert (success, confirmed) == (True, False)


# --- _wait_for_upload_complete (C-01: real success-icon signal, verified 2026-08-18) -------

class FakeIconLocator:
    def __init__(self, appears_after_calls):
        self._appears_after_calls = appears_after_calls
        self.calls = 0

    def count(self):
        self.calls += 1
        return 1 if self._appears_after_calls is not None and self.calls >= self._appears_after_calls else 0


class FakeTextLocator:
    """Stands in for page.locator("text=/\\d{1,3}\\s*%/") — never has progress text in
    these tests, since they're specifically exercising the icon-based signal."""

    @property
    def first(self):
        return self

    def text_content(self, timeout=None):
        raise PlaywrightTimeoutError("no progress text in this test")


class FakeUploadPage:
    def __init__(self, icon_appears_after_calls):
        self._icon = FakeIconLocator(icon_appears_after_calls)
        self.wait_calls = 0

    def locator(self, selector):
        if selector == "[data-e2e='upload_status_container'] [data-icon='CheckCircleFill']":
            return self._icon
        return FakeTextLocator()

    def wait_for_timeout(self, ms):
        self.wait_calls += 1


def test_upload_complete_returns_true_and_stops_as_soon_as_icon_appears():
    page = FakeUploadPage(icon_appears_after_calls=2)
    assert tiktok_uploader._wait_for_upload_complete(page) is True
    # Confirmed quickly, not by exhausting the full UPLOAD_BAR_TIMEOUT_MS budget.
    max_possible_polls = tiktok_uploader.UPLOAD_BAR_TIMEOUT_MS // tiktok_uploader.UPLOAD_BAR_POLL_MS
    assert page.wait_calls < max_possible_polls


def test_upload_complete_falls_back_to_settle_delay_when_icon_never_appears():
    page = FakeUploadPage(icon_appears_after_calls=None)
    assert tiktok_uploader._wait_for_upload_complete(page) is False


# --- _dismiss_blocking_overlays (real bug caught by an authorized live test run,
# 2026-08-18: a cookie-consent banner and a "New editing features added" onboarding
# tooltip both intercept clicks on elements underneath them) ------------------------------

class FakeOverlayButton:
    def __init__(self, present):
        self._present = present
        self.clicked = False

    def click(self, timeout=None):
        if not self._present:
            raise PlaywrightTimeoutError("button not found")
        self.clicked = True


class FakeOverlayPage:
    def __init__(self, cookie_banner_present, tooltip_present):
        self.decline_button = FakeOverlayButton(cookie_banner_present)
        self.got_it_button = FakeOverlayButton(tooltip_present)

    def get_by_role(self, role, name=None):
        assert role == "button"
        if name == "Decline optional cookies":
            return self.decline_button
        if name == "Got it":
            return self.got_it_button
        raise AssertionError(f"unexpected role lookup: {role!r} name={name!r}")


def test_dismiss_overlays_clicks_both_when_both_present():
    page = FakeOverlayPage(cookie_banner_present=True, tooltip_present=True)
    tiktok_uploader._dismiss_blocking_overlays(page)
    assert page.decline_button.clicked is True
    assert page.got_it_button.clicked is True


def test_dismiss_overlays_never_raises_when_neither_present():
    page = FakeOverlayPage(cookie_banner_present=False, tooltip_present=False)
    tiktok_uploader._dismiss_blocking_overlays(page)  # must not raise
    assert page.decline_button.clicked is False
    assert page.got_it_button.clicked is False


def test_dismiss_overlays_handles_only_cookie_banner_present():
    page = FakeOverlayPage(cookie_banner_present=True, tooltip_present=False)
    tiktok_uploader._dismiss_blocking_overlays(page)
    assert page.decline_button.clicked is True
    assert page.got_it_button.clicked is False


def test_dismiss_overlays_handles_only_tooltip_present():
    page = FakeOverlayPage(cookie_banner_present=False, tooltip_present=True)
    tiktok_uploader._dismiss_blocking_overlays(page)
    assert page.decline_button.clicked is False
    assert page.got_it_button.clicked is True


# --- publish=False is now a hard no-op (safety-model correction, 2026-08-18: an abandoned
# upload is NOT saved as a draft by TikTok — confirmed by the account owner directly checking
# TikTok Studio and the mobile app, disproving the earlier assumption this code shipped
# with) — upload_video()/try_upload_clip() must never open a browser without publish=True.

def test_upload_video_without_publish_never_touches_playwright(tmp_path, monkeypatch):
    launch_calls = []

    class ExplodingPlaywright:
        def __enter__(self):
            launch_calls.append(1)
            raise AssertionError("sync_playwright() must never be entered when publish=False")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(tiktok_uploader, "sync_playwright", lambda: ExplodingPlaywright())

    outcome = tiktok_uploader.upload_video(tmp_path / "does_not_exist.mp4", "desc", publish=False)

    assert outcome == tiktok_uploader.UploadOutcome(success=False, confirmed=False)
    assert launch_calls == []


def test_upload_video_without_publish_does_not_require_the_file_to_exist(tmp_path, monkeypatch):
    # The no-op check must come before the file-existence check — publish=False means "do
    # nothing" regardless of what else might also be wrong.
    monkeypatch.setattr(
        tiktok_uploader, "sync_playwright", lambda: (_ for _ in ()).throw(AssertionError("must not be called"))
    )
    outcome = tiktok_uploader.upload_video(tmp_path / "nope.mp4", "desc", publish=False)
    assert outcome.success is False


def test_try_upload_clip_without_publish_is_also_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tiktok_uploader, "sync_playwright", lambda: (_ for _ in ()).throw(AssertionError("must not be called"))
    )
    outcome = tiktok_uploader.try_upload_clip(tmp_path / "nope.mp4", "desc")
    assert outcome == tiktok_uploader.UploadOutcome(success=False, confirmed=False)


def test_cli_without_publish_exits_zero_and_does_not_upload(monkeypatch, tmp_path, capsys):
    import sys
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video content")
    monkeypatch.setattr(sys, "argv", ["tiktok_uploader.py", str(video), "--description", "test"])
    monkeypatch.setattr(
        tiktok_uploader, "upload_video", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not upload"))
    )

    with __import__("pytest").raises(SystemExit) as exc_info:
        tiktok_uploader.main()

    assert exc_info.value.code == 0
    assert "Nothing to do without --publish" in capsys.readouterr().out


# --- post-click diagnostic pause (headed-only, never leaks into unattended runs) ----------

class FakeSnapshotPage:
    def __init__(self, fail=False):
        self._fail = fail

    def screenshot(self, path=None, full_page=None):
        if self._fail:
            raise RuntimeError("boom")

    def content(self):
        return "<html></html>"


def test_save_post_click_snapshot_never_raises_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tiktok_uploader, "POST_CLICK_SNAPSHOT_DIR", tmp_path / "selector_audit")
    tiktok_uploader._save_post_click_snapshot(FakeSnapshotPage(fail=True), "test")  # must not raise


def test_save_post_click_snapshot_writes_files(tmp_path, monkeypatch):
    snapshot_dir = tmp_path / "selector_audit"
    monkeypatch.setattr(tiktok_uploader, "POST_CLICK_SNAPSHOT_DIR", snapshot_dir)
    tiktok_uploader._save_post_click_snapshot(FakeSnapshotPage(), "test")
    assert list(snapshot_dir.glob("test_*.html"))


def test_try_upload_clip_always_forces_headless_regardless_of_diagnostic_feature():
    # The safety property the new headed-only diagnostic pause depends on: every automated
    # caller (auto_pilot.py, app.py, stream_watcher.py) goes through try_upload_clip(), which
    # must always force headless=True — otherwise the 15s post-click pause could silently
    # leak into an unattended 24/7 run.
    import inspect
    source = inspect.getsource(tiktok_uploader.try_upload_clip)
    assert "headless=True" in source


# --- _confirm_publish_despite_pending_review (found via the headed diagnostic pause,
# 2026-08-18: clicking post_video_button doesn't always publish directly — if TikTok's
# content review isn't finished yet, a "Weiter und veröffentlichen?" modal appears instead,
# and the actual publish only happens once "Jetzt veröffentlichen" is clicked) -------------

class FakeConfirmButton:
    def __init__(self, present):
        self._present = present
        self.clicked = False

    def click(self, timeout=None):
        if not self._present:
            raise PlaywrightTimeoutError("dialog not present")
        self.clicked = True


class FakeConfirmPage:
    def __init__(self, dialog_present):
        self.confirm_button = FakeConfirmButton(dialog_present)

    def get_by_role(self, role, name=None):
        assert role == "button"
        assert name == "Jetzt veröffentlichen"
        return self.confirm_button


def test_confirms_pending_review_dialog_when_present():
    page = FakeConfirmPage(dialog_present=True)
    assert tiktok_uploader._confirm_publish_despite_pending_review(page) is True
    assert page.confirm_button.clicked is True


def test_no_op_when_review_already_finished():
    page = FakeConfirmPage(dialog_present=False)
    assert tiktok_uploader._confirm_publish_despite_pending_review(page) is False
    assert page.confirm_button.clicked is False
