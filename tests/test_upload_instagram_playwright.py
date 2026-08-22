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
