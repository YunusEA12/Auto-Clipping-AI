import orchestrator


def test_backoff_increases_with_streak():
    delays = [orchestrator._crash_backoff_seconds(streak) for streak in (1, 2, 3, 4)]
    assert delays == sorted(delays)
    assert all(later > earlier for earlier, later in zip(delays, delays[1:]))


def test_backoff_starts_at_base_delay():
    assert orchestrator._crash_backoff_seconds(1) == orchestrator.CRASH_BACKOFF_BASE_SECONDS * 2


def test_backoff_is_capped():
    # A long crash-loop must not produce an ever-growing wait — the whole point is to bound
    # the damage, not to eventually stop restarting altogether.
    huge_streak = 500
    assert orchestrator._crash_backoff_seconds(huge_streak) == orchestrator.CRASH_BACKOFF_MAX_SECONDS
