import streamers as streamers_module

import optimization_engine
import orchestrator


# --- build_auto_pilot_cmd's publish threading (safety-model correction, 2026-08-18: TikTok
# has no draft-save action, so --publish must only ever be passed through when the
# streamer's own config explicitly opts into it, not whenever auto_upload is on) ----------

def test_cmd_never_includes_publish_when_auto_upload_is_off():
    cmd = orchestrator.build_auto_pilot_cmd({"name": "x", "url": "https://twitch.tv/x", "auto_upload": False, "publish": True})
    assert "--auto-upload" not in cmd
    assert "--publish" not in cmd


def test_cmd_omits_publish_when_auto_upload_on_but_publish_off():
    cmd = orchestrator.build_auto_pilot_cmd({"name": "x", "url": "https://twitch.tv/x", "auto_upload": True, "publish": False})
    assert "--auto-upload" in cmd
    assert "--publish" not in cmd


def test_cmd_includes_publish_only_when_both_flags_set():
    cmd = orchestrator.build_auto_pilot_cmd({"name": "x", "url": "https://twitch.tv/x", "auto_upload": True, "publish": True})
    assert "--auto-upload" in cmd
    assert "--publish" in cmd


def test_cmd_omits_publish_when_missing_from_entry_entirely():
    # Backward compatibility: an older streamers.json written before the `publish` field
    # existed must not silently start publishing live.
    cmd = orchestrator.build_auto_pilot_cmd({"name": "x", "url": "https://twitch.tv/x", "auto_upload": True})
    assert "--publish" not in cmd


# --- --streamer-name always threaded through (2026-08-18: H-14 — without it, two streamers
# live at once share agent_state.json, output/, and recording chunk filenames) -------------

def test_cmd_always_includes_streamer_name():
    cmd = orchestrator.build_auto_pilot_cmd({"name": "eliasn97", "url": "https://twitch.tv/eliasn97"})
    assert "--streamer-name" in cmd
    assert cmd[cmd.index("--streamer-name") + 1] == "eliasn97"


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
    # run_orchestrator() also calls optimization_engine.run_daily_report() every iteration
    # (2026-08-21) — without this, a real run here would read/write the actual project's
    # viral_memory.json/optimization_state.json instead of staying confined to tmp_path.
    monkeypatch.setattr(optimization_engine, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", tmp_path / "optimization_state.json")

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
