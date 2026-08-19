"""2026-08-19: adding background music from TikTok's own royalty-free Commercial Music
Library ("Unlimited" tab — verified live against the real upload editor, track titles/artists
are plainly stock content, not chart music) as an in-app step during upload, instead of the
account owner adding it manually afterward. Selectors verified live via a real (never-
published) test-clip upload — see tiktok_uploader.py's SOUND_* constants for the details."""

import random

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import tiktok_uploader


class FakeClickable:
    def __init__(self, present=True):
        self.present = present
        self.clicked = False

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        if not self.present:
            raise PlaywrightTimeoutError("not found")
        self.clicked = True

    def fill(self, value, timeout=None):
        if not self.present:
            raise PlaywrightTimeoutError("not found")
        self.value = value

    def wait_for(self, state=None, timeout=None):
        if not self.present:
            raise PlaywrightTimeoutError("never appeared")

    def get_attribute(self, name):
        return getattr(self, "_attr", None)

    def text_content(self, timeout=None):
        return getattr(self, "_text", "")


class FakeIconLocator:
    def __init__(self, ready_after_calls=1):
        self._calls = 0
        self._ready_after_calls = ready_after_calls

    @property
    def first(self):
        return self

    def get_attribute(self, name):
        self._calls += 1
        return "Loading" if self._calls < self._ready_after_calls else "MusicNote"


class FakeRow:
    def __init__(self, title, icon_after_calls=1):
        self._title = title
        self.add_button = FakeClickable()
        self._icon = FakeIconLocator(icon_after_calls)
        self.page = None

    def locator(self, selector):
        if selector == tiktok_uploader.SOUND_TRACK_TITLE_SELECTOR:
            box = FakeClickable()
            box._text = self._title
            return box
        if selector == tiktok_uploader.SOUND_TRACK_ADD_BUTTON_SELECTOR:
            return self.add_button
        if selector == tiktok_uploader.SOUND_TRACK_ADD_ICON_SELECTOR:
            return self._icon
        raise AssertionError(f"unexpected row selector: {selector!r}")

    def wait_for(self, state=None, timeout=None):
        pass  # a real, present row always "appears" immediately


class FakeRowsLocator:
    def __init__(self, rows):
        self._rows = rows

    @property
    def first(self):
        return self._rows[0] if self._rows else FakeClickable(present=False)

    def count(self):
        return len(self._rows)

    def nth(self, i):
        return self._rows[i]


class FakeSoundPage:
    """Mimics the subset of Playwright's Page API _add_background_sound() and its helpers
    use. `sounds_button_present`/`tab_present`/`rows` control where the best-effort chain
    degrades; `close_button_present` controls whether _close_sound_panel can find its target."""

    def __init__(
        self, sounds_button_present=True, got_it_present=False, tab_present=True,
        rows=None, close_button_present=True, volume_input_present=True,
    ):
        self.sounds_button = FakeClickable(sounds_button_present)
        self.got_it_button = FakeClickable(got_it_present)
        self.tab = FakeClickable(tab_present)
        self.rows = FakeRowsLocator(rows or [])
        for row in (rows or []):
            row.page = self
        self.close_button = FakeClickable(close_button_present)
        self.volume_input = FakeClickable(volume_input_present)
        self.wait_calls = 0
        self.keys_pressed = []

    def locator(self, selector):
        if selector == tiktok_uploader.SOUND_PANEL_BUTTON_SELECTOR:
            return self.sounds_button
        if selector == tiktok_uploader.SOUND_TRACK_ROW_SELECTOR:
            return self.rows
        if selector == tiktok_uploader.SOUND_PANEL_CLOSE_BUTTON_SELECTOR:
            return self.close_button
        if selector == tiktok_uploader.SOUND_VOLUME_INPUT_SELECTOR:
            return self.volume_input
        raise AssertionError(f"unexpected page selector: {selector!r}")

    def get_by_role(self, role, name=None):
        if role == "button" and name == "Got it":
            return self.got_it_button
        if role == "tab" and name == tiktok_uploader.SOUND_LIBRARY_TAB_LABEL:
            return self.tab
        raise AssertionError(f"unexpected role lookup: {role!r} name={name!r}")

    def wait_for_timeout(self, ms):
        self.wait_calls += 1

    @property
    def keyboard(self):
        page = self

        class _Keyboard:
            def press(self, key):
                page.keys_pressed.append(key)

        return _Keyboard()


# --- _add_background_sound: happy path -----------------------------------------------------

def test_adds_a_track_and_returns_its_title(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    row = FakeRow("Tenfold Love", icon_after_calls=1)
    page = FakeSoundPage(rows=[row])

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Tenfold Love"
    assert page.sounds_button.clicked is True
    assert page.tab.clicked is True
    assert row.add_button.clicked is True
    assert page.close_button.clicked is True


def test_dismisses_phone_mode_tooltip_when_present(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    row = FakeRow("Brand New")
    page = FakeSoundPage(rows=[row], got_it_present=True)

    tiktok_uploader._add_background_sound(page)

    assert page.got_it_button.clicked is True


def test_picks_randomly_among_the_candidate_pool(monkeypatch):
    rows = [FakeRow(f"Track {i}") for i in range(tiktok_uploader.SOUND_CANDIDATE_POOL + 5)]
    page = FakeSoundPage(rows=rows)
    monkeypatch.setattr(random, "randrange", lambda n: 3)

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Track 3"
    # Never asked to choose beyond the candidate pool size, even though more rows exist.
    calls = []
    monkeypatch.setattr(random, "randrange", lambda n: calls.append(n) or 0)
    tiktok_uploader._add_background_sound(FakeSoundPage(rows=rows))
    assert calls == [tiktok_uploader.SOUND_CANDIDATE_POOL]


def test_adjusts_volume_after_adding(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    row = FakeRow("Future")
    page = FakeSoundPage(rows=[row])

    tiktok_uploader._add_background_sound(page)

    assert page.volume_input.value == tiktok_uploader.SOUND_VOLUME_DB
    assert "Tab" in page.keys_pressed


# --- _add_background_sound: best-effort degradation -----------------------------------------

def test_returns_none_when_sounds_panel_cannot_open():
    page = FakeSoundPage(sounds_button_present=False)
    assert tiktok_uploader._add_background_sound(page) is None


def test_returns_none_when_library_tab_missing(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    page = FakeSoundPage(tab_present=False)
    assert tiktok_uploader._add_background_sound(page) is None
    assert page.close_button.clicked is True  # still cleans up the open panel


def test_returns_none_when_no_tracks_found(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    page = FakeSoundPage(rows=[])
    assert tiktok_uploader._add_background_sound(page) is None


def test_returns_none_when_add_click_fails(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    row = FakeRow("Keys to the City")
    row.add_button.present = False
    page = FakeSoundPage(rows=[row])
    assert tiktok_uploader._add_background_sound(page) is None


def test_volume_adjustment_failure_does_not_lose_the_added_track(monkeypatch):
    # A track already applied by the add-click stays applied even if the (non-critical)
    # volume tweak afterward can't find its input.
    monkeypatch.setattr(random, "randrange", lambda n: 0)
    row = FakeRow("Morning Light Etude")
    page = FakeSoundPage(rows=[row], volume_input_present=False)

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Morning Light Etude"


# --- upload_video wiring ---------------------------------------------------------------------

def test_upload_video_skips_sound_step_when_disabled(monkeypatch, tmp_path):
    # add_background_sound=False must mean _add_background_sound is never even called.
    called = []
    monkeypatch.setattr(tiktok_uploader, "_add_background_sound", lambda page: called.append(1))
    monkeypatch.setattr(
        tiktok_uploader, "sync_playwright", lambda: (_ for _ in ()).throw(AssertionError("no playwright needed"))
    )
    outcome = tiktok_uploader.upload_video(tmp_path / "nope.mp4", "desc", publish=False, add_background_sound=False)
    assert outcome.success is False
    assert called == []  # publish=False never reaches the sound step either way


def test_try_upload_clip_passes_through_add_background_sound_flag():
    import inspect
    source = inspect.getsource(tiktok_uploader.try_upload_clip)
    assert "add_background_sound=add_background_sound" in source
