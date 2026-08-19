import json
from datetime import datetime, timedelta, timezone

import pytest

import llm_utils


# --- secret redaction (L-06) -------------------------------------------------------------

def test_redacts_google_api_key():
    text = "auth failed with key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"
    redacted = llm_utils.redact_secrets(text)
    assert "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456" not in redacted
    assert "REDACTED" in redacted


def test_redacts_bearer_token():
    text = "request headers: Authorization: Bearer abcdef1234567890ABCDEF"
    redacted = llm_utils.redact_secrets(text)
    assert "abcdef1234567890ABCDEF" not in redacted
    assert "REDACTED" in redacted


def test_leaves_ordinary_text_untouched():
    text = "Rate limit exceeded, please try again in 20s"
    assert llm_utils.redact_secrets(text) == text


def test_budget_increments_and_allows_calls_under_the_limit(tmp_path):
    path = tmp_path / "budget.json"
    for expected in (1, 2, 3):
        assert llm_utils.check_and_increment_budget(max_calls_per_day=5, path=path) == expected


def test_budget_raises_once_the_daily_ceiling_is_hit(tmp_path):
    path = tmp_path / "budget.json"
    for _ in range(3):
        llm_utils.check_and_increment_budget(max_calls_per_day=3, path=path)

    with pytest.raises(llm_utils.DailyCallBudgetExceeded):
        llm_utils.check_and_increment_budget(max_calls_per_day=3, path=path)

    # A rejected call must not itself count against the budget.
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["calls"] == 3


def test_budget_resets_on_a_new_utc_day(tmp_path):
    path = tmp_path / "budget.json"
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    path.write_text(json.dumps({"date": yesterday, "calls": 999}), encoding="utf-8")

    # Yesterday's exhausted count must not carry over into today.
    assert llm_utils.check_and_increment_budget(max_calls_per_day=5, path=path) == 1


@pytest.fixture
def isolated_budget_path(tmp_path, monkeypatch):
    """call_with_retry() doesn't take a `path` argument — it goes through
    check_and_increment_budget()'s dynamic BUDGET_STATE_PATH lookup, so isolating these
    tests from the real project's gemini_call_budget.json means monkeypatching the module
    global, not passing a parameter."""
    path = tmp_path / "budget.json"
    monkeypatch.setattr(llm_utils, "BUDGET_STATE_PATH", path)
    return path


# --- retryability (2026-08-19: google-genai has no per-status-code exception type the way
# openai's SDK did -- ClientError covers every 4xx and ServerError every 5xx, so
# _is_retryable() has to inspect .code itself rather than a flat isinstance() tuple) ---------

def _server_error(code=503):
    from google.genai import errors as genai_errors
    return genai_errors.ServerError(code=code, response_json={"error": {"message": "server error"}})


def _client_error(code):
    from google.genai import errors as genai_errors
    return genai_errors.ClientError(code=code, response_json={"error": {"message": "client error"}})


def test_server_error_is_retryable():
    assert llm_utils._is_retryable(_server_error()) is True


def test_rate_limited_client_error_is_retryable():
    assert llm_utils._is_retryable(_client_error(429)) is True


def test_other_client_errors_are_not_retryable():
    assert llm_utils._is_retryable(_client_error(400)) is False
    assert llm_utils._is_retryable(_client_error(403)) is False


def test_timeout_error_is_retryable():
    assert llm_utils._is_retryable(TimeoutError("timed out")) is True


def test_call_with_retry_retries_transient_errors_then_succeeds(monkeypatch, isolated_budget_path):
    monkeypatch.setattr(llm_utils.time, "sleep", lambda _: None)  # don't actually wait in tests

    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _server_error()
        return "success"

    result = llm_utils.call_with_retry(flaky, description="test", max_retries=5, base_delay=0.01)

    assert result == "success"
    assert attempts["count"] == 3


def test_call_with_retry_gives_up_after_max_retries(monkeypatch, isolated_budget_path):
    from google.genai import errors as genai_errors

    monkeypatch.setattr(llm_utils.time, "sleep", lambda _: None)

    def always_fails():
        raise _server_error()

    with pytest.raises(genai_errors.ServerError):
        llm_utils.call_with_retry(always_fails, description="test", max_retries=2, base_delay=0.01)


def test_call_with_retry_does_not_retry_non_retryable_errors(isolated_budget_path):
    calls = {"count": 0}

    def raises_value_error():
        calls["count"] += 1
        raise ValueError("not a retryable API error")

    with pytest.raises(ValueError):
        llm_utils.call_with_retry(raises_value_error, description="test", max_retries=5, base_delay=0.01)

    assert calls["count"] == 1


def test_call_with_retry_does_not_retry_non_rate_limit_client_errors(isolated_budget_path):
    calls = {"count": 0}

    def raises_bad_request():
        calls["count"] += 1
        raise _client_error(400)

    from google.genai import errors as genai_errors
    with pytest.raises(genai_errors.ClientError):
        llm_utils.call_with_retry(raises_bad_request, description="test", max_retries=5, base_delay=0.01)

    assert calls["count"] == 1


def test_call_with_retry_respects_daily_budget(isolated_budget_path):
    isolated_budget_path.write_text(
        json.dumps({"date": datetime.now(timezone.utc).date().isoformat(), "calls": 1}), encoding="utf-8"
    )

    with pytest.raises(llm_utils.DailyCallBudgetExceeded):
        llm_utils.call_with_retry(lambda: "should never run", description="test", max_calls_per_day=1)
