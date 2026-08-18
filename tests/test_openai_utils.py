import json
from datetime import datetime, timedelta, timezone

import pytest

import openai_utils


def test_budget_increments_and_allows_calls_under_the_limit(tmp_path):
    path = tmp_path / "budget.json"
    for expected in (1, 2, 3):
        assert openai_utils.check_and_increment_budget(max_calls_per_day=5, path=path) == expected


def test_budget_raises_once_the_daily_ceiling_is_hit(tmp_path):
    path = tmp_path / "budget.json"
    for _ in range(3):
        openai_utils.check_and_increment_budget(max_calls_per_day=3, path=path)

    with pytest.raises(openai_utils.DailyCallBudgetExceeded):
        openai_utils.check_and_increment_budget(max_calls_per_day=3, path=path)

    # A rejected call must not itself count against the budget.
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["calls"] == 3


def test_budget_resets_on_a_new_utc_day(tmp_path):
    path = tmp_path / "budget.json"
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    path.write_text(json.dumps({"date": yesterday, "calls": 999}), encoding="utf-8")

    # Yesterday's exhausted count must not carry over into today.
    assert openai_utils.check_and_increment_budget(max_calls_per_day=5, path=path) == 1


@pytest.fixture
def isolated_budget_path(tmp_path, monkeypatch):
    """call_with_retry() doesn't take a `path` argument — it goes through
    check_and_increment_budget()'s dynamic BUDGET_STATE_PATH lookup, so isolating these
    tests from the real project's openai_call_budget.json means monkeypatching the module
    global, not passing a parameter."""
    path = tmp_path / "budget.json"
    monkeypatch.setattr(openai_utils, "BUDGET_STATE_PATH", path)
    return path


def test_call_with_retry_retries_transient_errors_then_succeeds(monkeypatch, isolated_budget_path):
    from openai import APIConnectionError

    monkeypatch.setattr(openai_utils.time, "sleep", lambda _: None)  # don't actually wait in tests

    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise APIConnectionError(request=None)
        return "success"

    result = openai_utils.call_with_retry(flaky, description="test", max_retries=5, base_delay=0.01)

    assert result == "success"
    assert attempts["count"] == 3


def test_call_with_retry_gives_up_after_max_retries(monkeypatch, isolated_budget_path):
    from openai import APIConnectionError

    monkeypatch.setattr(openai_utils.time, "sleep", lambda _: None)

    def always_fails():
        raise APIConnectionError(request=None)

    with pytest.raises(APIConnectionError):
        openai_utils.call_with_retry(always_fails, description="test", max_retries=2, base_delay=0.01)


def test_call_with_retry_does_not_retry_non_retryable_errors(isolated_budget_path):
    calls = {"count": 0}

    def raises_value_error():
        calls["count"] += 1
        raise ValueError("not a retryable API error")

    with pytest.raises(ValueError):
        openai_utils.call_with_retry(raises_value_error, description="test", max_retries=5, base_delay=0.01)

    assert calls["count"] == 1


def test_call_with_retry_respects_daily_budget(isolated_budget_path):
    isolated_budget_path.write_text(
        json.dumps({"date": datetime.now(timezone.utc).date().isoformat(), "calls": 1}), encoding="utf-8"
    )

    with pytest.raises(openai_utils.DailyCallBudgetExceeded):
        openai_utils.call_with_retry(lambda: "should never run", description="test", max_calls_per_day=1)
