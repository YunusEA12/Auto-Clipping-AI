"""2026-08-18, H-14: auto_pilot.update_agent_state() used to always write the single shared
agent_state.json — orchestrator.py running two streamers concurrently meant two subprocesses
read-modify-writing the same file, each one's partial update dict silently keeping whatever
fields the OTHER streamer's last write left there. --streamer-name now makes each subprocess
target its own agent_state_<slug>.json instead, via a module-level path override (not a
threaded parameter, since update_agent_state() already has ~10 call sites using **updates
only) — these tests cover the override mechanism directly."""

import json

import auto_pilot


def test_resolves_to_shared_path_by_default(monkeypatch):
    monkeypatch.setattr(auto_pilot, "_agent_state_path_override", None)
    assert auto_pilot._resolve_agent_state_path() == auto_pilot.AGENT_STATE_PATH


def test_resolves_to_override_when_set(monkeypatch, tmp_path):
    override = tmp_path / "agent_state_eliasn97.json"
    monkeypatch.setattr(auto_pilot, "_agent_state_path_override", override)
    assert auto_pilot._resolve_agent_state_path() == override


def test_update_agent_state_writes_to_the_override_path(monkeypatch, tmp_path):
    override = tmp_path / "agent_state_eliasn97.json"
    monkeypatch.setattr(auto_pilot, "_agent_state_path_override", override)

    auto_pilot.update_agent_state(current_action="testing")

    assert override.exists()
    saved = json.loads(override.read_text(encoding="utf-8"))
    assert saved["current_action"] == "testing"


def test_two_overrides_never_write_to_each_others_file(monkeypatch, tmp_path):
    # The exact scenario this fix closes: two "streamers" (simulated by swapping the
    # override between calls, standing in for two separate concurrent processes each with
    # their own override set once at startup) must never see or clobber each other's state.
    path_a = tmp_path / "agent_state_a.json"
    path_b = tmp_path / "agent_state_b.json"

    monkeypatch.setattr(auto_pilot, "_agent_state_path_override", path_a)
    auto_pilot.update_agent_state(target_streamer="a", clips_kept_total=3)

    monkeypatch.setattr(auto_pilot, "_agent_state_path_override", path_b)
    auto_pilot.update_agent_state(target_streamer="b", clips_kept_total=7)

    state_a = json.loads(path_a.read_text(encoding="utf-8"))
    state_b = json.loads(path_b.read_text(encoding="utf-8"))
    assert state_a["target_streamer"] == "a" and state_a["clips_kept_total"] == 3
    assert state_b["target_streamer"] == "b" and state_b["clips_kept_total"] == 7
