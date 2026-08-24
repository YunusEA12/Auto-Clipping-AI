"""2026-08-21: unit coverage for upload_instagram_playwright.py's page-manipulation helpers,
starting with _select_9_16_aspect_ratio() (named _select_original_aspect_ratio() until
2026-08-22; added the same day, in response to an account-owner report of Reels arriving
cropped top/bottom). No prior test file existed for this module at all — everything else here
is exercised only via --headed manual runs (see the module's own UNVERIFIED SELECTORS
docstring). These tests use a minimal fake Playwright page object rather than a real browser:
they verify this module's own control flow (which selectors get clicked, in what order, and
that a missing selector degrades gracefully instead of raising), not that the selector strings
themselves match Instagram's real live DOM — that part remains genuinely unverified until run
with --headed against a real session.

2026-08-22: switched its primary locator from an svg[aria-label="Original"] guess to a
text-based locator scoped to the Crop dialog, after 6/6 real runs failed to find that label —
a saved selector_audit/05b_* screenshot showed the real ratio picker rendering as plain-text
rows ("Original"/"1:1"/"9:16"/"16:9"), not svg-labeled icons. Same day, second change: the
TARGET itself moved from "Original" (ambiguous — defers to Instagram's own inferred ratio,
which caused both the original over-crop report and a later black-bars/letterboxing report on
the same already-correct 1080x1920 source) to the explicit "9:16" option. The old svg selector
is kept as a second-choice fallback. FakePage below models get_by_role()/get_by_text()
chaining (not just .locator()) so these tests cover both the new primary path and the
fallback."""

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import upload_instagram_playwright as igp

# Captured before any test's autouse fixtures run (tests/conftest.py's _block_real_instagram_calls
# patches igp.upload_video to raise, project-wide, so no test accidentally fires a real post) —
# tests below that genuinely need to exercise upload_video() itself restore this real reference
# locally rather than disabling the guard globally.
_real_upload_video = igp.upload_video


class _FakeLocator:
    def __init__(self, page, key):
        self._page = page
        self._key = key

    @property
    def first(self):
        return self

    def get_by_text(self, text, exact=False):
        return _FakeLocator(self._page, f'{self._key} >> text="{text}"')

    def click(self, timeout=None):
        if self._key in self._page.missing_selectors:
            raise PlaywrightTimeoutError(f"{self._key} not found")
        self._page.clicked.append(self._key)

    def count(self):
        return 0 if self._key in self._page.missing_selectors else 1


class FakePage:
    def __init__(self, missing_selectors=()):
        self.clicked = []
        self.missing_selectors = set(missing_selectors)

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def get_by_role(self, role, name=None):
        return _FakeLocator(self, f'role={role}[name="{name}"]')


CROP_BUTTON = 'svg[aria-label="Select crop"]'
RATIO_TEXT = 'role=dialog[name="Crop"] >> text="9:16"'
RATIO_SVG_FALLBACK = 'svg[aria-label="9:16"]'


def test_selects_9_16_via_text_locator_and_closes_crop_tool():
    page = FakePage()
    assert igp._select_9_16_aspect_ratio(page) is True
    assert page.clicked == [CROP_BUTTON, RATIO_TEXT, CROP_BUTTON]


def test_falls_back_to_svg_selector_when_text_locator_is_missing():
    page = FakePage(missing_selectors=[RATIO_TEXT])
    assert igp._select_9_16_aspect_ratio(page) is True
    assert page.clicked == [CROP_BUTTON, RATIO_SVG_FALLBACK, CROP_BUTTON]


def test_returns_false_without_clicking_anything_when_crop_button_is_missing():
    page = FakePage(missing_selectors=[CROP_BUTTON])
    assert igp._select_9_16_aspect_ratio(page) is False
    assert page.clicked == []


def test_still_attempts_to_close_crop_tool_when_both_ratio_locators_are_missing():
    # The crop popover was opened but neither "9:16" locator matched -- must not leave it
    # open over the wizard's "Next" button on the following step.
    page = FakePage(missing_selectors=[RATIO_TEXT, RATIO_SVG_FALLBACK])
    assert igp._select_9_16_aspect_ratio(page) is False
    assert page.clicked == [CROP_BUTTON, CROP_BUTTON]


# --- 2026-08-24: account-chooser interstitial detection ----------------------------------------
# See _is_account_chooser_interstitial()'s own docstring for the live incident this closes —
# every Instagram upload failed for 2+ hours because this page state (neither the real home
# feed nor an actual login redirect) had no detection at all.

USE_ANOTHER_PROFILE = '[aria-label="Use another profile"]'
CREATE_NEW_ACCOUNT = '[aria-label="Create new account"]'


def test_detects_the_interstitial_when_both_markers_present():
    page = FakePage()  # neither selector missing -> both .count() calls return 1
    assert igp._is_account_chooser_interstitial(page) is True


def test_does_not_flag_the_real_home_feed():
    page = FakePage(missing_selectors=[USE_ANOTHER_PROFILE, CREATE_NEW_ACCOUNT])
    assert igp._is_account_chooser_interstitial(page) is False


def test_requires_both_markers_not_just_one():
    # A page with only one of the two present isn't confidently the interstitial -- avoids a
    # false positive off a single coincidentally-matching element elsewhere on a real page.
    page = FakePage(missing_selectors=[CREATE_NEW_ACCOUNT])
    assert igp._is_account_chooser_interstitial(page) is False


def test_never_raises_if_the_page_locator_call_itself_fails():
    class _ExplodingPage:
        def locator(self, selector):
            raise RuntimeError("page closed mid-check")

    assert igp._is_account_chooser_interstitial(_ExplodingPage()) is False


def test_upload_video_returns_failed_outcome_when_browser_slot_times_out(tmp_path, monkeypatch):
    """Same fix, same reasoning, as tiktok_uploader's identical test — see there."""
    monkeypatch.setattr(igp, "upload_video", _real_upload_video)  # see module-level comment
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake video bytes")
    monkeypatch.setattr(igp, "load_cookies", lambda: [{"name": "sessionid", "value": "x"}])

    def _raise(*a, **k):
        raise TimeoutError("no browser slot freed up in time")
    monkeypatch.setattr(igp.browser_concurrency, "browser_slot", _raise)
    monkeypatch.setattr(
        igp, "sync_playwright", lambda: (_ for _ in ()).throw(AssertionError("must not be reached"))
    )

    outcome = igp.upload_video(video_path, "desc", publish=True)

    assert outcome == igp.UploadOutcome(success=False, confirmed=False)
