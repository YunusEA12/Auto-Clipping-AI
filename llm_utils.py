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

2026-08-21: added call_with_fallback() — Google's free-tier request quota is per MODEL per
day, not per project (confirmed live: gemini-3.5-flash-lite hit RESOURCE_EXHAUSTED at exactly
500/day while a completely separate model, gemini-3.5-flash, worked fine seconds later), so a
short-lived model whose own quota is exhausted for today doesn't have to take the whole fleet
down with it — DEFAULT_MODEL_POOL below is an ordered list of models to try, cheapest/fastest
first. See DEFAULT_MODEL_POOL's own comment for exactly which models were empirically verified
callable for this API key and which weren't (most of the "obvious" model names one might
reach for — gemini-1.5-*, gemini-2.5-* — are retired or 404 "no longer available to new
users" on this key; don't add them back without re-verifying live first).
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

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


# Google's real 429 body names the exact exhausted quota's id, e.g. (confirmed live,
# 2026-08-21) 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' — a substring distinct
# enough from a per-minute/per-second quota id (which would say PerMinute/PerSecond instead)
# to reliably tell "this model is done for the rest of the UTC day, retrying won't help"
# apart from a genuinely transient rate limit that backoff-retrying the SAME model can still
# recover from. Checked case-insensitively since Google doesn't document this as a stable
# contract, just an observed real response.
_DAILY_QUOTA_MARKER = "perday"


def _is_daily_quota_exhausted(e: Exception) -> bool:
    """True for our own local per-model DailyCallBudgetExceeded, or a real Gemini 429 whose
    body identifies a per-day quota. Used to skip call_with_retry()'s backoff-retry loop
    (proven pointless against this specific failure, 2026-08-21: a per-day quota does not
    clear within any realistic retry window) and, one layer up, to tell call_with_fallback()
    when to advance to the next model in the pool rather than treat this as a hard failure."""
    if isinstance(e, DailyCallBudgetExceeded):
        return True
    if isinstance(e, genai_errors.ClientError) and getattr(e, "code", None) == _RATE_LIMIT_STATUS:
        return _DAILY_QUOTA_MARKER in str(e).lower()
    return False


# Ordered fallback pool, cheapest/fastest first — empirically verified against the real API
# live, 2026-08-21 (client.models.generate_content(), not just the model catalog listing,
# which includes plenty of names that 404 the moment you actually try to call them):
#
#   WORKS today, genuinely distinct models (separate free-tier quota buckets each):
#     gemini-3.5-flash-lite   — current primary, cheapest/fastest, 500 req/day free tier
#     gemini-3.1-flash-lite   — older generation, still callable, presumably its own budget
#     gemini-3.5-flash        — non-lite: same generation, ~7-9x pricier per token (see
#                               analyze.py's MODEL comment) but real extra capacity when both
#                               flash-lite tiers above are exhausted
#     gemini-3.6-flash        — newest/priciest flash tier tried; last resort before giving up
#
#   Do NOT exist / do NOT work on this API key (all confirmed live, 2026-08-21 — re-verify
#   before ever adding back, don't restore from memory of an older investigation):
#     gemini-1.5-flash, gemini-1.5-flash-8b, gemini-1.5-pro  — hard 404 NOT_FOUND
#     gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-pro — 404 "no longer available to
#       new users" (the model catalog still LISTS these — client.models.get() succeeds — but
#       generate_content() 404s anyway; catalog presence alone is not enough to trust a name)
#     gemini-flash-lite-latest, gemini-pro-latest — real "latest" aliases, but both 429'd
#       immediately on first-ever call, meaning each shares a quota bucket with a model
#       already exhausted today — including either adds zero real fallback capacity
#
# No "-pro" tier made it into the pool: every pro-tier name tried (gemini-2.5-pro,
# gemini-pro-latest) either 404'd or was already quota-exhausted on first contact — this key
# does not appear to have working pro-tier access under its current billing tier at all.
DEFAULT_MODEL_POOL: List[str] = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]


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
# 2026-08-21, corrected same day: first raised 500 -> 1500 on the theory that 500 was a purely
# self-imposed spend guard (every call in the incident that prompted that change returned 200
# OK, so Gemini itself appeared not to be rate-limiting us). That theory was wrong: raising the
# ceiling let real traffic reach Gemini's actual free-tier quota for the first time, which
# immediately started 429ing with RESOURCE_EXHAUSTED — the API's own error names the exact
# limit: quotaId 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', quotaValue 500, for
# gemini-3.5-flash-lite (confirmed live, 2026-08-21, minutes after the 1500 ceiling shipped).
# 500 was never just a self-imposed number — it already matched Google's real hard daily cap
# for this model on the free tier. Above it, every call still burns a real network round-trip
# and call_with_retry()'s full retry ladder before failing anyway (it's a per-day quota, not
# transient — retrying seconds later can't help), instead of failing fast, for free, via this
# local counter. Restored to 500 to match the confirmed real ceiling. The only way to safely
# raise this is upgrading the Gemini API key off the free tier (a billing decision, not a code
# change) and setting GEMINI_DAILY_CALL_BUDGET (read dynamically, see max_calls_per_day below)
# to whatever the new plan's actual daily quota is.
DEFAULT_MAX_CALLS_PER_DAY = 500


def _resolve_max_calls_per_day(max_calls_per_day: Optional[int]) -> int:
    """An explicit call-site argument always wins. Otherwise GEMINI_DAILY_CALL_BUDGET, read
    dynamically (not at module-import time) so it reflects whatever .env load_dotenv() already
    ran by the time an actual Gemini call happens — every entry point here calls load_dotenv()
    well after this module is first imported, so reading os.environ at import time would only
    ever see a truly-exported shell var, never a plain .env entry. Falls back to
    DEFAULT_MAX_CALLS_PER_DAY. An unparseable env value is logged and ignored rather than
    crashing the pipeline over a typo'd config value."""
    if max_calls_per_day is not None:
        return max_calls_per_day
    raw = os.environ.get("GEMINI_DAILY_CALL_BUDGET")
    if raw is None:
        return DEFAULT_MAX_CALLS_PER_DAY
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "GEMINI_DAILY_CALL_BUDGET=%r is not a valid integer — falling back to the default (%d).",
            raw, DEFAULT_MAX_CALLS_PER_DAY,
        )
        return DEFAULT_MAX_CALLS_PER_DAY


class DailyCallBudgetExceeded(Exception):
    """Raised instead of making a Gemini call once today's call ceiling for this model is hit.
    Callers (auto_pilot.py's cycle loop) treat this like any other cycle failure — it goes to
    the error-cooldown path and is retried next cycle, at which point it will keep failing
    fast (no API call made) until the daily counter resets. call_with_fallback() catches this
    one layer up to advance to the next model in the pool instead of failing the cycle."""


class AllModelsExhausted(Exception):
    """Raised by call_with_fallback() when every model in the pool is quota-exhausted for
    today (each one's own local daily budget, or a real Gemini per-day RESOURCE_EXHAUSTED
    429). Callers treat this exactly like DailyCallBudgetExceeded used to be treated alone —
    goes to the error-cooldown path and is retried next cycle, where every model keeps failing
    fast until tomorrow's UTC reset re-opens the pool starting from the primary model again."""


# Used as the per-model dict key when a caller doesn't specify a model (check_and_increment_
# budget()'s own pre-fallback callers, and its existing tests) — the primary/default model,
# not an arbitrary sentinel, so a legacy caller's usage is attributed to the same model it was
# always actually calling.
def _implicit_model_key() -> str:
    return DEFAULT_MODEL_POOL[0]


def _load_budget_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # Migrate the old single-model shape (`"calls": <int>`, from before per-model tracking
    # existed, 2026-08-21) into the new `"calls": {model: count}` shape, transparently, so an
    # already-accumulated real count isn't lost or silently reset the first time this runs
    # under the new code. Attributed to the primary model, since the old code only ever
    # tracked one model total — that model's own real usage IS that old flat number.
    calls = state.get("calls")
    if isinstance(calls, int):
        state["calls"] = {_implicit_model_key(): calls}
    return state


def check_and_increment_budget(
    model: Optional[str] = None, max_calls_per_day: Optional[int] = None, path: Path = None,
) -> int:
    """Increments today's call counter FOR `model` and returns its new count. Raises
    DailyCallBudgetExceeded (without incrementing) if that model's ceiling for today was
    already hit — a different model's own counter is unaffected, since Google's free-tier
    quota is per-model-per-day, not per-project (confirmed live, 2026-08-21 — see
    DEFAULT_MODEL_POOL's own comment). `model=None` (the default) attributes usage to the
    primary/default model — the only behavior that existed before per-model tracking did.
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
    model = model or _implicit_model_key()
    max_calls_per_day = _resolve_max_calls_per_day(max_calls_per_day)

    today = datetime.now(timezone.utc).date().isoformat()
    state = _load_budget_state(path)

    if state.get("date") != today:
        # A new UTC day also resets which model is "active" for call_with_fallback() — see
        # its own docstring — by simply not carrying forward the old active_model value here.
        state = {"date": today, "calls": {}}

    calls = state.setdefault("calls", {})
    current = calls.get(model, 0)
    if current >= max_calls_per_day:
        raise DailyCallBudgetExceeded(
            f"Daily Gemini call budget ({max_calls_per_day}) reached for {model} on {today} — "
            f"no further {model} calls will be made until the next UTC day."
        )

    calls[model] = current + 1
    try:
        atomic_io.atomic_write_json(path, state)
    except OSError as e:
        logger.warning("Could not persist %s: %s", path, e)

    return calls[model]


def _load_active_model_index(pool: List[str], path: Path = None) -> int:
    """Which pool index call_with_fallback() should START at for its next call — sticky
    across the whole fleet via the shared budget-state file, so once model A is found
    exhausted, every OTHER streamer process's next call starts directly at model B instead of
    each one independently re-discovering A's exhaustion with its own wasted API round-trip.
    Resets to 0 (the primary model) at UTC day rollover, same as the call counters
    themselves."""
    if path is None:
        path = BUDGET_STATE_PATH
    today = datetime.now(timezone.utc).date().isoformat()
    state = _load_budget_state(path)
    if state.get("date") != today:
        return 0
    active_model = state.get("active_model")
    if active_model in pool:
        return pool.index(active_model)
    return 0


def _save_active_model_index(idx: int, pool: List[str], path: Path = None) -> None:
    import atomic_io

    if path is None:
        path = BUDGET_STATE_PATH
    today = datetime.now(timezone.utc).date().isoformat()
    state = _load_budget_state(path)
    if state.get("date") != today:
        state = {"date": today, "calls": {}}
    state["active_model"] = pool[idx]
    try:
        atomic_io.atomic_write_json(path, state)
    except OSError as e:
        logger.warning("Could not persist active model to %s: %s", path, e)


def call_with_retry(
    fn: Callable[[], T],
    description: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
    max_calls_per_day: Optional[int] = None,
    model: Optional[str] = None,
) -> T:
    """Runs `fn()` (a zero-arg closure making one Gemini call), enforcing the daily call
    budget first and retrying transient failures (rate limits, timeouts, 5xx) with
    exponential backoff + jitter. Non-retryable errors (bad request, auth, etc.) propagate
    immediately on the first attempt, unchanged. A confirmed per-day quota exhaustion (see
    _is_daily_quota_exhausted) also propagates immediately rather than being retried — proven
    pointless live, 2026-08-21: it does not clear within any realistic retry window, so
    burning the backoff ladder against it only delays call_with_fallback() from moving on to
    the next model.

    `model`, when given, scopes the daily budget check to that model specifically (see
    check_and_increment_budget's own docstring) — used by call_with_fallback() below. Plain
    callers that don't pass it keep attributing usage to the primary/default model, unchanged
    from before per-model tracking existed."""
    check_and_increment_budget(model, max_calls_per_day)

    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e):
                raise
            if _is_daily_quota_exhausted(e):
                logger.warning(
                    "%s: %s appears to be a per-day quota exhaustion, not a transient error — "
                    "not retrying on this model (%s: %s).",
                    description, type(e).__name__, type(e).__name__, redact_secrets(str(e)),
                )
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


def call_with_fallback(
    make_request: Callable[[str], T],
    description: str,
    model_pool: Optional[List[str]] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
    max_calls_per_day: Optional[int] = None,
) -> T:
    """Like call_with_retry, but on quota exhaustion for the current model — its own local
    daily budget, or a real Gemini per-day RESOURCE_EXHAUSTED 429 — fails over to the next
    model in `model_pool` (default DEFAULT_MODEL_POOL) immediately instead of giving up the
    whole call. `make_request(model)` should perform exactly one Gemini call against the given
    model name and return/raise like any call_with_retry `fn`.

    Sticky across calls (2026-08-21, see _load_active_model_index's own docstring): once a
    fallback model is selected, later calls today — including from OTHER streamer processes,
    via the shared budget-state file — start directly from that model instead of each one
    re-discovering an already-exhausted primary with its own wasted API round-trip. Resets to
    the primary model at UTC day rollover.

    Raises AllModelsExhausted if every model in the pool is quota-exhausted for today. Any
    other error (a genuinely non-retryable failure, or a retryable one that exhausted its own
    backoff retries WITHOUT being quota-related) propagates unchanged — this only ever
    intercepts quota exhaustion, never masks a real bug."""
    pool = model_pool or DEFAULT_MODEL_POOL
    if not pool:
        raise ValueError("model_pool must not be empty")

    start_idx = _load_active_model_index(pool)
    last_error: Optional[Exception] = None

    for idx in range(start_idx, len(pool)):
        model = pool[idx]
        try:
            result = call_with_retry(
                lambda: make_request(model), f"{description}[{model}]",
                max_retries=max_retries, base_delay=base_delay, max_delay=max_delay,
                max_calls_per_day=max_calls_per_day, model=model,
            )
        except Exception as e:
            if not _is_daily_quota_exhausted(e):
                raise
            last_error = e
            next_model = pool[idx + 1] if idx + 1 < len(pool) else None
            logger.warning(
                "%s: %s is quota-exhausted for today — failing over to %s.",
                description, model, next_model or "no remaining model in the pool",
            )
            continue

        if idx != start_idx:
            _save_active_model_index(idx, pool)
        return result

    raise AllModelsExhausted(
        f"{description}: every model in the fallback pool is quota-exhausted for today: {pool}"
    ) from last_error
