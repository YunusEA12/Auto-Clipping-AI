"""Unit coverage for browser_concurrency.py's cross-process browser-slot semaphore (2026-08-24
incident remediation — see the module's own docstring for why this exists)."""

import threading
import time

import pytest

import browser_concurrency


@pytest.fixture(autouse=True)
def _isolated_slot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_concurrency, "_SLOT_DIR", tmp_path / "browser_slots")
    monkeypatch.setattr(browser_concurrency, "_SLOT_POLL_SECONDS", 0.02)


def test_single_caller_acquires_and_releases_a_slot(monkeypatch):
    monkeypatch.setattr(browser_concurrency, "BROWSER_MAX_CONCURRENCY", 2)
    with browser_concurrency.browser_slot():
        pass  # no exception, no deadlock — the slot was free


def test_up_to_max_concurrency_callers_can_hold_slots_at_once(monkeypatch):
    monkeypatch.setattr(browser_concurrency, "BROWSER_MAX_CONCURRENCY", 2)
    with browser_concurrency.browser_slot():
        with browser_concurrency.browser_slot():
            pass  # both slots held simultaneously by the same process, no blocking


def test_a_third_caller_blocks_until_a_slot_frees_up(monkeypatch):
    monkeypatch.setattr(browser_concurrency, "BROWSER_MAX_CONCURRENCY", 1)
    acquired_third = threading.Event()

    with browser_concurrency.browser_slot():
        def _try_acquire():
            with browser_concurrency.browser_slot():
                acquired_third.set()

        t = threading.Thread(target=_try_acquire)
        t.start()
        time.sleep(0.1)
        assert not acquired_third.is_set()  # still blocked — the only slot is held above

    t.join(timeout=2)
    assert acquired_third.is_set()  # released on exit from the `with` above — now it got in


def test_timeout_raises_instead_of_waiting_forever(monkeypatch):
    monkeypatch.setattr(browser_concurrency, "BROWSER_MAX_CONCURRENCY", 1)
    with browser_concurrency.browser_slot():
        with pytest.raises(TimeoutError):
            with browser_concurrency.browser_slot(timeout=0.1):
                pass


def test_slot_is_released_even_if_the_body_raises(monkeypatch):
    monkeypatch.setattr(browser_concurrency, "BROWSER_MAX_CONCURRENCY", 1)
    with pytest.raises(ValueError):
        with browser_concurrency.browser_slot():
            raise ValueError("simulated upload failure")

    with browser_concurrency.browser_slot(timeout=0.5):
        pass  # would time out if the slot above hadn't actually been released
