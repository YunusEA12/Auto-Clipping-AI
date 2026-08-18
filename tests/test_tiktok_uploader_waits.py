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
