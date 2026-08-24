import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

import streamers


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", profile="eliasn97", path=path)
    streamers.add_streamer("bob", "https://twitch.tv/bob", path=path)

    entries = streamers.load_streamers(path)
    assert [e["name"] for e in entries] == ["elias", "bob"]
    assert entries[0]["profile"] == "eliasn97"


def test_add_duplicate_name_rejected(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", path=path)
    with pytest.raises(ValueError):
        streamers.add_streamer("elias", "https://twitch.tv/other", path=path)


def test_remove_streamer(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", path=path)

    assert streamers.remove_streamer("elias", path=path) is True
    assert streamers.load_streamers(path) == []
    assert streamers.remove_streamer("elias", path=path) is False


# --- update_streamer (2026-08-21: add/remove existed, but no way to change auto_upload/
# publish/url/profile on an EXISTING entry without deleting and recreating it -- lost its
# position in the list and required re-typing everything for a quick remote toggle) --------

def test_update_streamer_changes_only_the_given_fields(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", profile="p1", auto_upload=True, path=path)

    assert streamers.update_streamer("elias", publish=True, path=path) is True

    entry = streamers.load_streamers(path)[0]
    assert entry["publish"] is True
    assert entry["url"] == "https://twitch.tv/elias"  # untouched
    assert entry["profile"] == "p1"                    # untouched
    assert entry["auto_upload"] is True                 # untouched


def test_update_streamer_can_change_multiple_fields_at_once(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", path=path)

    streamers.update_streamer(
        "elias", url="https://twitch.tv/new_elias", profile="p2", auto_upload=True, publish=True, path=path,
    )

    entry = streamers.load_streamers(path)[0]
    assert entry["url"] == "https://twitch.tv/new_elias"
    assert entry["profile"] == "p2"
    assert entry["auto_upload"] is True
    assert entry["publish"] is True


def test_update_streamer_returns_false_when_not_found(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", path=path)
    assert streamers.update_streamer("nobody", publish=True, path=path) is False


def test_update_streamer_preserves_list_position(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("a", "https://twitch.tv/a", path=path)
    streamers.add_streamer("b", "https://twitch.tv/b", path=path)
    streamers.add_streamer("c", "https://twitch.tv/c", path=path)

    streamers.update_streamer("b", publish=True, path=path)

    assert [e["name"] for e in streamers.load_streamers(path)] == ["a", "b", "c"]


def test_update_streamer_false_can_turn_off_publish(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", auto_upload=True, publish=True, path=path)

    # False must actually apply, not be treated as "not passed" (that's what None is for).
    streamers.update_streamer("elias", publish=False, path=path)

    assert streamers.load_streamers(path)[0]["publish"] is False


def test_load_missing_file_returns_empty_list(tmp_path):
    assert streamers.load_streamers(tmp_path / "nope.json") == []


def test_load_corrupted_file_returns_empty_list_not_crash(tmp_path):
    path = tmp_path / "streamers.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert streamers.load_streamers(path) == []


def test_load_skips_invalid_entries_but_keeps_valid_ones(tmp_path):
    path = tmp_path / "streamers.json"
    path.write_text(
        '[{"name": "good", "url": "https://twitch.tv/good"}, {"url": "missing_name_field"}]',
        encoding="utf-8",
    )
    entries = streamers.load_streamers(path)
    assert [e["name"] for e in entries] == ["good"]


def test_save_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", path=path)
    # The lock file itself (path + ".lock") is an expected, permanent sidecar of the
    # filelock-based race fix below — it's not a leftover atomic-write temp file, which is
    # what this test actually guards against.
    leftovers = [p for p in tmp_path.iterdir() if p != path and p.name != f"{path.name}.lock"]
    assert leftovers == []


# --- concurrent add_streamer race (found in review, 2026-08-18: add_streamer/remove_streamer
# were an unlocked read-modify-write — two concurrent callers, e.g. two dashboard browser
# tabs, could each read the same list and the later save would silently discard the
# earlier caller's addition) -----------------------------------------------------------

def test_concurrent_add_streamer_does_not_lose_an_update(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.save_streamers([], path)  # pre-create the file so both threads race on the same content

    names = [f"streamer_{i}" for i in range(8)]
    errors = []

    def add(name):
        try:
            streamers.add_streamer(name, f"https://twitch.tv/{name}", path=path)
        except Exception as e:  # pragma: no cover - only populated on real failure
            errors.append(e)

    threads = [threading.Thread(target=add, args=(name,)) for name in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    saved_names = {e["name"] for e in streamers.load_streamers(path)}
    assert saved_names == set(names)


# --- publish field (safety-model correction, 2026-08-18: TikTok has no draft-save action,
# so auto_upload and publish must be tracked as separate, independent choices) ------------

def test_publish_defaults_to_false():
    entry = streamers.StreamerEntry(name="x", url="https://twitch.tv/x")
    assert entry.publish is False


def test_add_streamer_persists_publish_flag(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", auto_upload=True, publish=True, path=path)
    entries = streamers.load_streamers(path)
    assert entries[0]["auto_upload"] is True
    assert entries[0]["publish"] is True


def test_add_streamer_publish_defaults_false_even_with_auto_upload(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.add_streamer("elias", "https://twitch.tv/elias", auto_upload=True, path=path)
    entries = streamers.load_streamers(path)
    assert entries[0]["auto_upload"] is True
    assert entries[0]["publish"] is False


# --- facecam_box / facecam_padding: a streamer's known, static facecam layout, used by
# process.py/vision.py as a high-confidence fallback when a given clip's own dynamic face
# detection is missing or low-confidence -----------------------------------------------------

def test_facecam_box_and_padding_default_to_none():
    entry = streamers.StreamerEntry(name="x", url="https://twitch.tv/x")
    assert entry.facecam_box is None
    assert entry.facecam_padding is None


def test_facecam_box_roundtrips_through_save_and_load(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.save_streamers([
        {"name": "elias", "url": "https://twitch.tv/elias", "facecam_box": [0.6, 0.0, 1.0, 0.35], "facecam_padding": 0.25},
    ], path=path)
    entry = streamers.load_streamers(path)[0]
    assert entry["facecam_box"] == [0.6, 0.0, 1.0, 0.35]
    assert entry["facecam_padding"] == 0.25


@pytest.mark.parametrize("bad_box", [
    [0.1, 0.1, 0.1],           # wrong length
    [1.0, 0.0, 0.5, 1.0],      # x1 >= x2
    [0.0, 1.0, 1.0, 0.5],      # y1 >= y2
    [-0.1, 0.0, 1.0, 1.0],     # x1 out of 0..1
    [0.0, 0.0, 1.5, 1.0],      # x2 out of 0..1
])
def test_facecam_box_rejects_invalid_coordinates(bad_box):
    with pytest.raises(Exception):
        streamers.StreamerEntry(name="x", url="https://twitch.tv/x", facecam_box=bad_box)


def test_facecam_box_accepts_full_frame_extremes():
    entry = streamers.StreamerEntry(name="x", url="https://twitch.tv/x", facecam_box=[0.0, 0.0, 1.0, 1.0])
    assert entry.facecam_box == [0.0, 0.0, 1.0, 1.0]


def test_facecam_padding_rejects_negative_value():
    with pytest.raises(Exception):
        streamers.StreamerEntry(name="x", url="https://twitch.tv/x", facecam_padding=-0.1)


def test_load_streamers_skips_entry_with_invalid_facecam_box(tmp_path):
    # load_streamers() already skips-and-logs any entry that fails StreamerEntry validation
    # (see its own try/except) -- a malformed hand-edited facecam_box shouldn't crash the
    # whole load, just drop that one entry.
    path = tmp_path / "streamers.json"
    path.write_text(json.dumps([
        {"name": "good", "url": "https://twitch.tv/good"},
        {"name": "bad", "url": "https://twitch.tv/bad", "facecam_box": [1.0, 0.0, 0.0, 1.0]},
    ]), encoding="utf-8")
    entries = streamers.load_streamers(path)
    assert [e["name"] for e in entries] == ["good"]


# --- load_streamers duplicate-name hardening (2026-08-19: add_streamer()'s own uniqueness
# check, plus the M-15 filelock, only guard the API path — a hand-edited streamers.json can
# still contain a literal duplicate name, the same class of bug that produced the dashboard's
# "papaplatte twice" symptom, even though in that specific case the real cause was a stale
# agent_state.json, not a duplicate here) --------------------------------------------------

def test_load_streamers_drops_duplicate_name_keeping_first(tmp_path):
    path = tmp_path / "streamers.json"
    path.write_text(json.dumps([
        {"name": "papaplatte", "url": "https://twitch.tv/papaplatte", "auto_upload": True},
        {"name": "papaplatte", "url": "https://twitch.tv/papaplatte-old-url", "auto_upload": False},
    ]), encoding="utf-8")

    entries = streamers.load_streamers(path)

    assert len(entries) == 1
    assert entries[0]["url"] == "https://twitch.tv/papaplatte"
    assert entries[0]["auto_upload"] is True


def test_load_streamers_keeps_distinct_names(tmp_path):
    path = tmp_path / "streamers.json"
    path.write_text(json.dumps([
        {"name": "a", "url": "https://twitch.tv/a"},
        {"name": "b", "url": "https://twitch.tv/b"},
    ]), encoding="utf-8")

    entries = streamers.load_streamers(path)
    assert [e["name"] for e in entries] == ["a", "b"]


# --- purge_stale_agent_states (2026-08-19: found live — a leftover flat agent_state.json
# from before --streamer-name was threaded through everywhere sat next to the real
# agent_state_papaplatte.json, both showing "papaplatte" in the dashboard at once, one of
# them over 9 hours stale) -------------------------------------------------------------

def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _write_state(path, target_streamer, hours_ago):
    path.write_text(json.dumps({
        "target_streamer": target_streamer, "last_updated": _iso(hours_ago),
    }), encoding="utf-8")


def _write_orchestrator_state(root, live_by_name):
    (root / "orchestrator_state.json").write_text(json.dumps({
        "streamers": {name: {"live": live} for name, live in live_by_name.items()},
    }), encoding="utf-8")


def test_purge_removes_orphaned_state_not_in_streamers_json(tmp_path):
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    orphan = tmp_path / "agent_state_removedstreamer.json"
    _write_state(orphan, "https://twitch.tv/removedstreamer", hours_ago=1)

    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path)

    assert "agent_state_removedstreamer.json" in deleted
    assert not orphan.exists()


def test_purge_removes_stale_state_for_a_still_configured_streamer(tmp_path):
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    stale = tmp_path / "agent_state_eliasn97.json"
    _write_state(stale, "https://twitch.tv/eliasn97", hours_ago=30)

    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path, max_age_hours=24)

    assert "agent_state_eliasn97.json" in deleted
    assert not stale.exists()


# --- offline-aware grace period (2026-08-19, requested live: "wenn agent länger offline ist
# dann soll er auch aus dem status raus gehen" — a streamer simply not streaming right now
# should clear from the dashboard promptly, but a streamer still supposed to be live that
# just stopped updating (a real crash/hang) should stay visible as an actionable problem for
# the full max_age_hours) --------------------------------------------------------------

def test_default_offline_grace_period_is_short_not_an_hour():
    # 2026-08-19: the original 1-hour default was itself the bug the account owner reported —
    # a card stayed in Agent Status for up to an hour after actually going offline. Pin the
    # default well under an hour so this can't silently regress back to that.
    assert streamers.AGENT_STATE_OFFLINE_GRACE_HOURS <= 0.25


def test_purge_uses_short_grace_period_for_a_streamer_currently_offline(tmp_path):
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    _write_orchestrator_state(tmp_path, {"eliasn97": False})
    stale = tmp_path / "agent_state_eliasn97.json"
    _write_state(stale, "https://twitch.tv/eliasn97", hours_ago=2)  # well under max_age_hours=24

    deleted = streamers.purge_stale_agent_states(
        streamers_path=streamers_path, root=tmp_path, max_age_hours=24, offline_grace_hours=1,
    )

    assert "agent_state_eliasn97.json" in deleted
    assert not stale.exists()


def test_purge_keeps_offline_streamer_within_its_grace_period(tmp_path):
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    _write_orchestrator_state(tmp_path, {"eliasn97": False})
    fresh = tmp_path / "agent_state_eliasn97.json"
    _write_state(fresh, "https://twitch.tv/eliasn97", hours_ago=0.5)  # under offline_grace_hours=1

    deleted = streamers.purge_stale_agent_states(
        streamers_path=streamers_path, root=tmp_path, max_age_hours=24, offline_grace_hours=1,
    )

    assert deleted == []
    assert fresh.exists()


def test_purge_uses_long_threshold_for_a_streamer_still_reported_live(tmp_path):
    # Still live but stuck/crashed — an actionable problem, must NOT vanish after just the
    # short offline grace period.
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    _write_orchestrator_state(tmp_path, {"eliasn97": True})
    stuck = tmp_path / "agent_state_eliasn97.json"
    _write_state(stuck, "https://twitch.tv/eliasn97", hours_ago=2)  # past offline_grace_hours=1

    deleted = streamers.purge_stale_agent_states(
        streamers_path=streamers_path, root=tmp_path, max_age_hours=24, offline_grace_hours=1,
    )

    assert deleted == []
    assert stuck.exists()


def test_purge_uses_long_threshold_when_orchestrator_state_has_no_opinion(tmp_path):
    # No orchestrator_state.json at all (e.g. orchestrator.py itself isn't running) — liveness
    # is unknown, never guessed at, so this must fall back to the safer, longer threshold.
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    unknown = tmp_path / "agent_state_eliasn97.json"
    _write_state(unknown, "https://twitch.tv/eliasn97", hours_ago=2)  # past offline_grace_hours=1

    deleted = streamers.purge_stale_agent_states(
        streamers_path=streamers_path, root=tmp_path, max_age_hours=24, offline_grace_hours=1,
    )

    assert deleted == []
    assert unknown.exists()


def test_purge_keeps_fresh_state_for_a_still_configured_streamer(tmp_path):
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    fresh = tmp_path / "agent_state_eliasn97.json"
    _write_state(fresh, "https://twitch.tv/eliasn97", hours_ago=1)

    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path, max_age_hours=24)

    assert deleted == []
    assert fresh.exists()


def test_purge_keeps_legacy_flat_file_when_it_is_the_only_state_file(tmp_path):
    # A genuinely standalone run (no --streamer-name, no orchestrator.py involved) — the
    # legacy file has no per-streamer sibling to be superseded by, so it's judged purely on
    # staleness like anything else.
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    legacy_fresh = tmp_path / "agent_state.json"
    _write_state(legacy_fresh, "https://www.twitch.tv/papaplatte", hours_ago=1)

    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path, max_age_hours=24)

    assert deleted == []
    assert legacy_fresh.exists()


def test_purge_removes_legacy_flat_file_the_instant_a_per_streamer_file_exists(tmp_path):
    # The exact real bug found live, 2026-08-19: the legacy file's mere coexistence with a
    # real per-streamer file is itself proof it's obsolete — a live system never writes both
    # at once — so this must be caught immediately, not only after max_age_hours passes.
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "papaplatte", "url": "https://twitch.tv/papaplatte"}], path=streamers_path)
    legacy = tmp_path / "agent_state.json"
    _write_state(legacy, "https://www.twitch.tv/papaplatte", hours_ago=1)  # fresh by age alone
    per_streamer = tmp_path / "agent_state_papaplatte.json"
    _write_state(per_streamer, "https://www.twitch.tv/papaplatte", hours_ago=1)  # also fresh

    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path, max_age_hours=24)

    assert deleted == ["agent_state.json"]
    assert not legacy.exists()
    assert per_streamer.exists()  # the real, current file is untouched


def test_purge_leaves_unparseable_timestamp_alone(tmp_path):
    # eliasn97 is a real, current streamer, so this file is never "orphaned" — isolates the
    # ambiguous-timestamp case: never guess on ambiguity, same principle as
    # metrics_tracker.prune_viral_memory.
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "eliasn97", "url": "https://twitch.tv/eliasn97"}], path=streamers_path)
    ambiguous = tmp_path / "agent_state_eliasn97.json"
    ambiguous.write_text(json.dumps({"target_streamer": "x", "last_updated": "not-a-real-timestamp"}), encoding="utf-8")

    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path)

    assert deleted == []
    assert ambiguous.exists()


# --- load_streamers_diagnostics ------------------------------------------------------------

def test_diagnostics_missing_file_is_not_unreadable(tmp_path):
    # A missing file is a legitimately empty fleet (e.g. first run), not a read failure.
    path = tmp_path / "streamers.json"
    diag = streamers.load_streamers_diagnostics(path)
    assert diag == {"unreadable": False, "raw_count": 0, "loaded_count": 0}


def test_diagnostics_detects_unreadable_file(tmp_path):
    # Found live, 2026-08-23: streamers.json ended up owned by a different user than the
    # autoclip services expect, so every load_streamers() call failed with PermissionError,
    # caught internally, and returned [] -- indistinguishable from a real empty fleet without
    # this diagnostic. A directory in place of the file exercises the same OSError branch
    # load_streamers()/load_streamers_diagnostics() both catch, without this test depending on
    # running as a non-root user -- a real chmod 000 doesn't block root's own reads, and this
    # suite runs as root in some environments.
    path = tmp_path / "streamers.json"
    path.mkdir()
    diag = streamers.load_streamers_diagnostics(path)
    assert diag == {"unreadable": True, "raw_count": None, "loaded_count": 0}


def test_diagnostics_detects_corrupt_json(tmp_path):
    path = tmp_path / "streamers.json"
    path.write_text("{not valid json", encoding="utf-8")
    diag = streamers.load_streamers_diagnostics(path)
    assert diag == {"unreadable": True, "raw_count": None, "loaded_count": 0}


def test_diagnostics_counts_entries_skipped_as_invalid(tmp_path):
    path = tmp_path / "streamers.json"
    path.write_text(json.dumps([
        {"name": "papaplatte", "url": "https://twitch.tv/papaplatte"},
        {"name": "papaplatte", "url": "https://twitch.tv/papaplatte-dup"},  # duplicate name
        {"name": "bad", "url": "https://twitch.tv/bad", "facecam_box": [0.1]},  # invalid shape
    ]), encoding="utf-8")
    diag = streamers.load_streamers_diagnostics(path)
    assert diag == {"unreadable": False, "raw_count": 3, "loaded_count": 1}


def test_diagnostics_clean_file_reports_no_skips(tmp_path):
    path = tmp_path / "streamers.json"
    streamers.save_streamers([{"name": "papaplatte", "url": "https://twitch.tv/papaplatte"}], path=path)
    diag = streamers.load_streamers_diagnostics(path)
    assert diag == {"unreadable": False, "raw_count": 1, "loaded_count": 1}


def test_purge_never_raises_on_corrupt_json(tmp_path):
    streamers_path = tmp_path / "streamers.json"
    streamers.save_streamers([], path=streamers_path)  # no streamers at all -> "broken" is orphaned
    corrupt = tmp_path / "agent_state_broken.json"
    corrupt.write_text("{not valid json", encoding="utf-8")

    # Must not raise despite the corrupt JSON — and since "broken" matches no streamer in an
    # empty streamers.json, it's orphaned by the slug check alone (independent of content).
    deleted = streamers.purge_stale_agent_states(streamers_path=streamers_path, root=tmp_path)
    assert "agent_state_broken.json" in deleted
    assert not corrupt.exists()
