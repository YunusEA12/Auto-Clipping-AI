"""2026-08-19: adding background music from TikTok's own royalty-free Commercial Music
Library ("Unlimited" tab — verified live against the real upload editor, track titles/artists
are plainly stock content, not chart music) as an in-app step during upload, instead of the
account owner adding it manually afterward. Selectors verified live via a real (never-
published) test-clip upload — see tiktok_uploader.py's SOUND_* constants for the details.

2026-08-19, later the same day: switched from browsing/randomizing among whatever the
"Unlimited" tab happened to list first, to searching for one of a fixed, explicitly approved
song pool (SOUND_TRACK_POOL) via the Sounds panel's own search box, plus a permanent
blacklist (BLACKLISTED_TRACK_TITLES). Verified live that all this still composes correctly
with the volume-reduction and video-editor-exit fixes from earlier the same day."""

import random

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import tiktok_uploader


@pytest.fixture(autouse=True)
def _reset_last_track_title(monkeypatch):
    # _last_track_title is module-level state (see its own docstring: it must genuinely
    # persist across consecutive in-process uploads) -- reset it before every test so one
    # test's successful pick can't leak into another's and make an unrelated shuffle/choice
    # mock nondeterministic.
    monkeypatch.setattr(tiktok_uploader, "_last_track_title", None)


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
    def __init__(self, title, icon_after_calls=1, title_read_raises=False):
        self._title = title
        self._title_read_raises = title_read_raises
        self.add_button = FakeClickable()
        self._icon = FakeIconLocator(icon_after_calls)
        self.page = None

    def locator(self, selector):
        if selector == tiktok_uploader.SOUND_TRACK_TITLE_SELECTOR:
            if self._title_read_raises:
                raise Exception("boom")
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


class FakeSearchBox(FakeClickable):
    """The Sounds panel's own search input — distinct from the page's unrelated "Search
    locations" input (see SOUND_SEARCH_INPUT_PLACEHOLDER's own comment), reached via
    get_by_placeholder rather than .locator()."""

    def __init__(self, page, present=True):
        super().__init__(present)
        self._page = page
        self.queries_typed = []

    def type(self, text, delay=None):
        if not self.present:
            raise PlaywrightTimeoutError("not found")
        self.queries_typed.append(text)
        self._page._pending_query = text


class FakeSoundPage:
    """Mimics the subset of Playwright's Page API _add_background_sound() and its helpers
    use. `sounds_button_present`/`tab_present`/`search_results` control where the best-effort
    chain degrades; `close_button_present` controls whether _close_sound_panel can find its
    target.

    `search_results` maps query string -> list[FakeRow], modeling what the Sounds panel would
    show after that exact query is searched and Enter is pressed (an unlisted query yields no
    results, matching a real "nothing found" search)."""

    def __init__(
        self, sounds_button_present=True, got_it_present=False, tab_present=True,
        search_results=None, search_box_present=True,
        close_button_present=True, volume_input_present=True, save_button_present=True,
    ):
        self.sounds_button = FakeClickable(sounds_button_present)
        self.got_it_button = FakeClickable(got_it_present)
        self.tab = FakeClickable(tab_present)
        self.search_results = search_results or {}
        self.search_box = FakeSearchBox(self, present=search_box_present)
        self._pending_query = None
        self.current_rows = FakeRowsLocator([])
        self.close_button = FakeClickable(close_button_present)
        self.volume_input = FakeClickable(volume_input_present)
        self.save_button = FakeClickable(save_button_present)
        self.wait_calls = 0
        self.keys_pressed = []

    def locator(self, selector):
        if selector == tiktok_uploader.SOUND_PANEL_BUTTON_SELECTOR:
            return self.sounds_button
        if selector == tiktok_uploader.SOUND_TRACK_ROW_SELECTOR:
            return self.current_rows
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
        if role == "button" and name == "Save":
            return self.save_button
        raise AssertionError(f"unexpected role lookup: {role!r} name={name!r}")

    def get_by_placeholder(self, text):
        if text == tiktok_uploader.SOUND_SEARCH_INPUT_PLACEHOLDER:
            return self.search_box
        raise AssertionError(f"unexpected placeholder lookup: {text!r}")

    def wait_for_timeout(self, ms):
        self.wait_calls += 1

    @property
    def keyboard(self):
        page = self

        class _Keyboard:
            def press(self, key):
                page.keys_pressed.append(key)
                if key == "Enter" and page._pending_query is not None:
                    rows = page.search_results.get(page._pending_query, [])
                    page.current_rows = FakeRowsLocator(rows)
                    for row in rows:
                        row.page = page

        return _Keyboard()


# --- _add_background_sound: happy path (search-based, 2026-08-19) --------------------------

def test_adds_the_first_pool_track_whose_search_succeeds(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)  # keep SOUND_TRACK_POOL's own order
    row = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]})

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Dexter - The blood theme"
    assert page.sounds_button.clicked is True
    assert page.tab.clicked is True
    assert row.add_button.clicked is True
    assert page.close_button.clicked is True
    assert "Enter" in page.keys_pressed


def test_dismisses_phone_mode_tooltip_when_present(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]}, got_it_present=True)

    tiktok_uploader._add_background_sound(page)

    assert page.got_it_button.clicked is True


def test_adjusts_volume_after_adding(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]})

    tiktok_uploader._add_background_sound(page)

    assert page.volume_input.value == tiktok_uploader.SOUND_VOLUME_DB
    assert "Tab" in page.keys_pressed


# --- fixed pool + fallback (2026-08-19: not every pool title is guaranteed to exist in
# TikTok's library at any given moment -- "A Long Way Home" currently returns zero real
# results -- so a search coming up empty must fall through to the next pool entry) -----------

def test_falls_through_to_the_next_pool_entry_when_the_first_search_is_empty(monkeypatch):
    order = ["A Long Way Home", "Veridis Quo", "Dexter - The blood theme"]
    monkeypatch.setattr(random, "shuffle", lambda seq: seq.sort(key=order.index))
    row = FakeRow("Veridis Quo")
    page = FakeSoundPage(search_results={
        "A Long Way Home": [],  # no results for this one, as confirmed live
        "Veridis Quo": [row],
    })

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Veridis Quo"


def test_returns_none_when_every_pool_entry_s_search_is_empty(monkeypatch):
    page = FakeSoundPage(search_results={})  # every query yields no rows (unlisted -> [])
    assert tiktok_uploader._add_background_sound(page) is None
    assert page.close_button.clicked is True


# --- permanent blacklist (2026-08-19, explicit account-owner call) --------------------------

def test_blacklisted_track_is_skipped_in_favor_of_the_next_result(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    blacklisted = FakeRow("Countless")
    real_match = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [blacklisted, real_match]})

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Dexter - The blood theme"
    assert blacklisted.add_button.clicked is False


def test_blacklist_match_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    blacklisted = FakeRow("COUNTLESS")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [blacklisted]})

    assert tiktok_uploader._add_background_sound(page) is None


def test_row_whose_title_read_fails_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    boom_row = FakeRow("Dexter - The blood theme", title_read_raises=True)
    real_match = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [boom_row, real_match]})

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Dexter - The blood theme"
    assert boom_row.add_button.clicked is False
    assert real_match.add_button.clicked is True


# --- duplicate-avoidance: _ordered_search_queries (2026-08-19: same intent as the earlier
# pool-browsing version -- avoid two consecutive clips carrying the same track when a
# different pool entry is also available) ----------------------------------------------------

def test_ordered_search_queries_deprioritizes_the_last_added_track(monkeypatch):
    monkeypatch.setattr(tiktok_uploader, "_last_track_title", "Veridis Quo")
    monkeypatch.setattr(random, "shuffle", lambda seq: None)  # keep SOUND_TRACK_POOL's own order

    queries = tiktok_uploader._ordered_search_queries()

    assert queries[-1] == "Veridis Quo"
    assert set(queries) == set(tiktok_uploader.SOUND_TRACK_POOL)


def test_ordered_search_queries_no_reorder_when_no_prior_track(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    queries = tiktok_uploader._ordered_search_queries()
    assert queries == tiktok_uploader.SOUND_TRACK_POOL


def test_second_upload_avoids_repeating_the_first_upload_s_track(monkeypatch):
    monkeypatch.setattr(tiktok_uploader, "_last_track_title", "Dexter - The blood theme")
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Veridis Quo")
    page = FakeSoundPage(search_results={
        "Dexter - The blood theme": [FakeRow("Dexter - The blood theme")],
        "Veridis Quo": [row],
    })

    title = tiktok_uploader._add_background_sound(page)

    assert title != "Dexter - The blood theme"


def test_add_background_sound_remembers_the_track_it_added(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]})

    tiktok_uploader._add_background_sound(page)

    assert tiktok_uploader._last_track_title == "Dexter - The blood theme"


# --- _add_background_sound: best-effort degradation -----------------------------------------

def test_returns_none_when_sounds_panel_cannot_open():
    page = FakeSoundPage(sounds_button_present=False)
    assert tiktok_uploader._add_background_sound(page) is None


def test_returns_none_when_library_tab_missing(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    page = FakeSoundPage(tab_present=False)
    assert tiktok_uploader._add_background_sound(page) is None
    assert page.close_button.clicked is True  # still cleans up the open panel


def test_returns_none_when_search_box_missing(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    page = FakeSoundPage(search_box_present=False)
    assert tiktok_uploader._add_background_sound(page) is None
    assert page.close_button.clicked is True


def test_returns_none_when_add_click_fails(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    row.add_button.present = False
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]})
    assert tiktok_uploader._add_background_sound(page) is None


def test_volume_adjustment_failure_does_not_lose_the_added_track(monkeypatch):
    # A track already applied by the add-click stays applied even if the (non-critical)
    # volume tweak afterward can't find its input.
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]}, volume_input_present=False)

    title = tiktok_uploader._add_background_sound(page)

    assert title == "Dexter - The blood theme"


# --- _exit_video_editor (2026-08-19: found live — the caption-fill step started failing with
# a Playwright "intercepts pointer events" error after adding a sound, traced to the inline
# video editor never being explicitly exited via its own Save button; closing the Sounds
# side-panel only closes THAT sub-panel, not the whole editor overlay sitting on top of the
# normal upload page underneath it) -----------------------------------------------------------

def test_exit_video_editor_clicks_save():
    page = FakeSoundPage()
    tiktok_uploader._exit_video_editor(page)
    assert page.save_button.clicked is True


def test_exit_video_editor_never_raises_when_save_missing():
    page = FakeSoundPage(save_button_present=False)
    tiktok_uploader._exit_video_editor(page)  # must not raise


def test_add_background_sound_always_exits_editor_on_success(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]})
    tiktok_uploader._add_background_sound(page)
    assert page.save_button.clicked is True


def test_add_background_sound_exits_editor_even_when_sounds_panel_never_opened():
    # The editor is opened as a side effect of clicking the Sounds button in the first place
    # — even the earliest possible failure (the button itself missing) must still exit it.
    page = FakeSoundPage(sounds_button_present=False)
    tiktok_uploader._add_background_sound(page)
    assert page.save_button.clicked is True


def test_add_background_sound_exits_editor_even_when_library_tab_missing(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    page = FakeSoundPage(tab_present=False)
    tiktok_uploader._add_background_sound(page)
    assert page.save_button.clicked is True


def test_add_background_sound_exits_editor_even_when_add_click_fails(monkeypatch):
    monkeypatch.setattr(random, "shuffle", lambda seq: None)
    row = FakeRow("Dexter - The blood theme")
    row.add_button.present = False
    page = FakeSoundPage(search_results={"Dexter - The blood theme": [row]})
    tiktok_uploader._add_background_sound(page)
    assert page.save_button.clicked is True


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
