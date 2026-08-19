"""Shared reliability wrapper for every Gemini call in the pipeline (analyze.py's clip
selection, train_loop.py's critic).

Two things every direct `client.models.generate_content(...)` call in this codebase used to
lack entirely:

1. Retry/backoff on transient failures (rate limits, timeouts, transient 5xx) — a 429 used
   to propagate straight up and kill the whole cycle instead of just waiting a moment and
   trying again.
2. A daily call ceiling — nothing anywhere stopped a crash-restart loop (see orchestrator.py's
   crash-loop backoff) or simply an unusually active 24/7 fleet from running up an unbounded
   API bill before a human noticed. This is a call-count budget, not a dollar budget — exact
   per-call cost depends on model/token-count and changes over time, so a call-count ceiling
   is the honest, low-maintenance proxy rather than a fragile hardcoded price table.

2026-08-19: migrated from OpenAI to Gemini (google-genai) — renamed from openai_utils.py so
the module name doesn't keep pointing at a provider this codebase no longer calls.
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)

T = TypeVar("T")

# google-genai raises ClientError for 4xx and ServerError for 5xx (both subclass APIError,
# with a `.code` int attribute holding the HTTP status) — unlike the old openai SDK, there's
# no separate exception type per status code, so a flat isinstance() tuple can't tell a
# retryable 429 (rate limit) apart from a non-retryable 400/403/404 the way
# RateLimitError-vs-everything-else used to. _is_retryable() checks the actual status instead.
_RETRYABLE_SERVER_ERROR = genai_errors.ServerError
_RATE_LIMIT_STATUS = 429


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, _RETRYABLE_SERVER_ERROR):
        return True
    if isinstance(e, genai_errors.ClientError):
        return getattr(e, "code", None) == _RATE_LIMIT_STATUS
    # A transport-level timeout isn't a genai_errors.APIError at all — it surfaces as the
    # underlying httpx/requests timeout exception, which every such library roots at
    # TimeoutError (Python's own builtin since 3.11's socket/asyncio unification).
    return isinstance(e, TimeoutError)


# Google API keys (AIza...) and Bearer auth headers, in case the SDK's exception string ever
# happens to echo one back (e.g. a malformed-auth error including the request's own headers)
# — every `except Exception as e` near an API client in this codebase logs `e` directly, so
# redact at the one place all of them ultimately route through instead of auditing every call
# site by hand.
_SECRET_PATTERN = re.compile(r"AIza[A-Za-z0-9_-]{35}|Bearer\s+[A-Za-z0-9._-]{10,}")


def redact_secrets(text: str) -> str:
    return _SECRET_PATTERN.sub("***REDACTED***", text)

DEFAULT_MAX_RETRIES = 4
DEFAULT_BASE_DELAY_SECONDS = 2.0
DEFAULT_MAX_DELAY_SECONDS = 60.0

BUDGET_STATE_PATH = Path("gemini_call_budget.json")
# Generous but finite — enough headroom for a multi-streamer 24/7 fleet doing normal cycles,
# low enough that a hot crash-restart loop gets cut off well before it becomes a real bill.
DEFAULT_MAX_CALLS_PER_DAY = 500


class DailyCallBudgetExceeded(Exception):
    """Raised instead of making a Gemini call once today's call ceiling is hit. Callers
    (auto_pilot.py's cycle loop) treat this like any other cycle failure — it goes to the
    error-cooldown path and is retried next cycle, at which point it will keep failing fast
    (no API call made) until the daily counter resets."""


def _load_budget_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def check_and_increment_budget(
    max_calls_per_day: int = DEFAULT_MAX_CALLS_PER_DAY, path: Path = None
) -> int:
    """Increments today's call counter and returns the new count. Raises
    DailyCallBudgetExceeded (without incrementing) if the ceiling for today was already hit.
    Uses UTC calendar days and a plain atomic JSON file — a lost increment under a genuine
    race between two processes is an acceptable, low-stakes edge case for a soft safety net,
    not a hard billing ledger.

    `path` defaults to the module-level BUDGET_STATE_PATH, looked up dynamically (not bound
    as a default-argument value) specifically so tests can monkeypatch
    llm_utils.BUDGET_STATE_PATH and have it actually take effect — a plain
    `path: Path = BUDGET_STATE_PATH` default would capture the value once at function
    definition time and silently ignore any later monkeypatch."""
    import atomic_io  # local import: avoids a hard circular-import dependency at module load

    if path is None:
        path = BUDGET_STATE_PATH

    today = datetime.now(timezone.utc).date().isoformat()
    state = _load_budget_state(path)

    if state.get("date") != today:
        state = {"date": today, "calls": 0}

    if state["calls"] >= max_calls_per_day:
        raise DailyCallBudgetExceeded(
            f"Daily Gemini call budget ({max_calls_per_day}) reached for {today} — "
            f"no further calls will be made until the next UTC day."
        )

    state["calls"] += 1
    try:
        atomic_io.atomic_write_json(path, state)
    except OSError as e:
        logger.warning("Could not persist %s: %s", path, e)

    return state["calls"]


def call_with_retry(
    fn: Callable[[], T],
    description: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
    max_calls_per_day: int = DEFAULT_MAX_CALLS_PER_DAY,
) -> T:
    """Runs `fn()` (a zero-arg closure making one Gemini call), enforcing the daily call
    budget first and retrying transient failures (rate limits, timeouts, 5xx) with
    exponential backoff + jitter. Non-retryable errors (bad request, auth, etc.) propagate
    immediately on the first attempt, unchanged."""
    check_and_increment_budget(max_calls_per_day)

    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e):
                raise
            attempt += 1
            if attempt > max_retries:
                logger.error(
                    "%s: giving up after %d retr%s (%s: %s)",
                    description, max_retries, "y" if max_retries == 1 else "ies",
                    type(e).__name__, redact_secrets(str(e)),
                )
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) * (1 + random.random() * 0.25)
            logger.warning(
                "%s: retryable error (%s: %s) — attempt %d/%d, waiting %.1fs",
                description, type(e).__name__, redact_secrets(str(e)), attempt, max_retries, delay,
            )
            time.sleep(delay)
