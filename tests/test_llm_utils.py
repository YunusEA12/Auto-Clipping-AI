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

    # A rejected call must not itself count against the budget. Attributed to the primary
    # model (2026-08-21: per-model tracking, see check_and_increment_budget's own docstring)
    # since no explicit model was given.
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["calls"] == {llm_utils.DEFAULT_MODEL_POOL[0]: 3}


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


# --- per-model budget isolation (2026-08-21: Google's free-tier quota is per-model-per-day,
# not per-project -- confirmed live the same day gemini-3.5-flash-lite hit RESOURCE_EXHAUSTED
# at exactly 500/day while gemini-3.5-flash, a completely different model, worked fine seconds
# later) -------------------------------------------------------------------------------------

def test_check_and_increment_budget_tracks_models_independently(tmp_path):
    path = tmp_path / "budget.json"
    for expected in (1, 2, 3):
        assert llm_utils.check_and_increment_budget("model-a", max_calls_per_day=3, path=path) == expected
    # model-a is now exhausted...
    with pytest.raises(llm_utils.DailyCallBudgetExceeded):
        llm_utils.check_and_increment_budget("model-a", max_calls_per_day=3, path=path)
    # ...but model-b's own counter is completely unaffected.
    assert llm_utils.check_and_increment_budget("model-b", max_calls_per_day=3, path=path) == 1


def test_old_flat_int_budget_file_migrates_to_primary_model(tmp_path):
    # A file written by the pre-2026-08-21 single-model code (or by check_and_increment_budget
    # with no model, which still writes under the primary/default key).
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps({"date": datetime.now(timezone.utc).date().isoformat(), "calls": 500}),
        encoding="utf-8",
    )
    state = llm_utils._load_budget_state(path)
    assert state["calls"] == {llm_utils.DEFAULT_MODEL_POOL[0]: 500}


# --- _is_daily_quota_exhausted ---------------------------------------------------------------

def test_daily_call_budget_exceeded_is_recognized_as_quota_exhaustion():
    assert llm_utils._is_daily_quota_exhausted(llm_utils.DailyCallBudgetExceeded("x")) is True


def test_real_per_day_quota_429_is_recognized():
    from google.genai import errors as genai_errors
    e = genai_errors.ClientError(code=429, response_json={"error": {
        "message": "Quota exceeded for quota metric 'Video Uploads' and limit "
                   "'Video Uploads per day' ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    }})
    assert llm_utils._is_daily_quota_exhausted(e) is True


def test_generic_429_without_a_perday_marker_is_not_treated_as_daily_exhaustion():
    # A genuinely transient rate limit (e.g. per-minute) must still go through the normal
    # backoff-retry path on the same model, not immediately fail over.
    assert llm_utils._is_daily_quota_exhausted(_client_error(429)) is False


def test_non_429_client_error_is_not_daily_quota_exhaustion():
    assert llm_utils._is_daily_quota_exhausted(_client_error(400)) is False


def test_call_with_retry_does_not_retry_a_real_daily_quota_429(monkeypatch, isolated_budget_path):
    from google.genai import errors as genai_errors

    slept = []
    monkeypatch.setattr(llm_utils.time, "sleep", lambda s: slept.append(s))
    calls = {"count": 0}

    def hits_daily_quota():
        calls["count"] += 1
        raise genai_errors.ClientError(code=429, response_json={"error": {
            "message": "RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        }})

    from google.genai import errors as genai_errors_mod
    with pytest.raises(genai_errors_mod.ClientError):
        llm_utils.call_with_retry(hits_daily_quota, description="test", max_retries=5, base_delay=0.01)

    assert calls["count"] == 1  # no retries burned against a per-day quota
    assert slept == []


# --- call_with_fallback -----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_sleep_in_fallback_tests(monkeypatch):
    monkeypatch.setattr(llm_utils.time, "sleep", lambda _s: None)


def test_call_with_fallback_uses_the_primary_model_when_it_works(isolated_budget_path):
    calls = []

    def make_request(model):
        calls.append(model)
        return f"response from {model}"

    result = llm_utils.call_with_fallback(
        make_request, description="test", model_pool=["model-a", "model-b"],
    )

    assert result == "response from model-a"
    assert calls == ["model-a"]


def test_call_with_fallback_advances_on_real_daily_quota_429(isolated_budget_path):
    from google.genai import errors as genai_errors

    calls = []

    def make_request(model):
        calls.append(model)
        if model == "model-a":
            raise genai_errors.ClientError(code=429, response_json={"error": {
                "message": "RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"
            }})
        return f"response from {model}"

    result = llm_utils.call_with_fallback(
        make_request, description="test", model_pool=["model-a", "model-b"],
    )

    assert result == "response from model-b"
    assert calls == ["model-a", "model-b"]


def test_call_with_fallback_advances_on_local_daily_budget_exhaustion(isolated_budget_path):
    isolated_budget_path.write_text(
        json.dumps({"date": datetime.now(timezone.utc).date().isoformat(), "calls": {"model-a": 5}}),
        encoding="utf-8",
    )
    calls = []

    def make_request(model):
        calls.append(model)
        return f"response from {model}"

    result = llm_utils.call_with_fallback(
        make_request, description="test", model_pool=["model-a", "model-b"], max_calls_per_day=5,
    )

    assert result == "response from model-b"
    assert calls == ["model-b"]  # model-a's local budget check failed before any real call


def test_call_with_fallback_raises_all_models_exhausted_when_every_model_is_out(isolated_budget_path):
    from google.genai import errors as genai_errors

    def always_exhausted(model):
        raise genai_errors.ClientError(code=429, response_json={"error": {
            "message": "RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        }})

    with pytest.raises(llm_utils.AllModelsExhausted):
        llm_utils.call_with_fallback(
            always_exhausted, description="test", model_pool=["model-a", "model-b"],
        )


def test_call_with_fallback_propagates_non_quota_errors_unchanged(isolated_budget_path):
    calls = []

    def make_request(model):
        calls.append(model)
        raise ValueError("a real bug, not a quota problem")

    with pytest.raises(ValueError):
        llm_utils.call_with_fallback(
            make_request, description="test", model_pool=["model-a", "model-b"],
        )

    assert calls == ["model-a"]  # never masked into a failover -- this is a real bug


def test_call_with_fallback_is_sticky_across_separate_calls(isolated_budget_path):
    from google.genai import errors as genai_errors

    def make_request(model):
        if model == "model-a":
            raise genai_errors.ClientError(code=429, response_json={"error": {
                "message": "RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"
            }})
        return f"response from {model}"

    llm_utils.call_with_fallback(make_request, description="first", model_pool=["model-a", "model-b"])

    # A second, independent call (simulating a different streamer process reading the same
    # shared state file) must start directly at model-b -- not re-probe the already-known-
    # exhausted model-a with another wasted API call.
    calls = []

    def make_request_2(model):
        calls.append(model)
        return f"response from {model}"

    result = llm_utils.call_with_fallback(
        make_request_2, description="second", model_pool=["model-a", "model-b"],
    )
    assert calls == ["model-b"]
    assert result == "response from model-b"


def test_call_with_fallback_resets_to_primary_on_a_new_utc_day(isolated_budget_path):
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    isolated_budget_path.write_text(
        json.dumps({"date": yesterday, "calls": {}, "active_model": "model-b"}), encoding="utf-8",
    )
    calls = []

    def make_request(model):
        calls.append(model)
        return f"response from {model}"

    result = llm_utils.call_with_fallback(
        make_request, description="test", model_pool=["model-a", "model-b"],
    )
    assert calls == ["model-a"]  # yesterday's sticky choice does not carry over
    assert result == "response from model-a"
