import streamers as streamers_module

import orchestrator


def test_run_orchestrator_default_streamers_path_respects_monkeypatch(tmp_path, monkeypatch):
    # run_orchestrator(streamers_path=None, ...) must resolve to the monkeypatched
    # streamers.STREAMERS_PATH, not a value bound at function-definition time — proven here
    # by a streamer that only exists in the monkeypatched file actually being seen and
    # snapshotted during the run (never touching the real project's streamers.json).
    fake_streamers_path = tmp_path / "streamers.json"
    fake_state_path = tmp_path / "orchestrator_state.json"
    streamers_module.save_streamers(
        [{"name": "only_in_fake_file", "url": "https://twitch.tv/x", "profile": "", "auto_upload": False}],
        path=fake_streamers_path,
    )
    monkeypatch.setattr(streamers_module, "STREAMERS_PATH", fake_streamers_path)
    monkeypatch.setattr(orchestrator, "ORCHESTRATOR_STATE_PATH", fake_state_path)
    monkeypatch.setattr(orchestrator, "is_stream_live", lambda url: False)

    seen_snapshots = []
    original_write = orchestrator.write_orchestrator_state

    def spy(streamers_status, poll_interval, status="running"):
        seen_snapshots.append(dict(streamers_status))
        original_write(streamers_status, poll_interval, status)

    monkeypatch.setattr(orchestrator, "write_orchestrator_state", spy)

    orchestrator.run_orchestrator(max_iterations=1, poll_interval=0)

    assert any("only_in_fake_file" in snapshot for snapshot in seen_snapshots)


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
