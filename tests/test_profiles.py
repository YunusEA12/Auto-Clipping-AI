import json

import pytest

import profiles


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    return tmp_path


def _write_profile(directory, name, data):
    (directory / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_load_profile_valid(profiles_dir):
    _write_profile(profiles_dir, "streamer_a", {"name": "streamer_a"})
    profile = profiles.load_profile("streamer_a")
    assert profile.name == "streamer_a"
    assert profile.energy_threshold == 7  # default


def test_load_profile_missing_file_raises_file_not_found(profiles_dir):
    with pytest.raises(FileNotFoundError):
        profiles.load_profile("does_not_exist")


def test_load_profile_malformed_json_raises_profile_corrupt(profiles_dir):
    (profiles_dir / "broken.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(profiles.ProfileCorruptError):
        profiles.load_profile("broken")


def test_load_profile_schema_violation_raises_profile_corrupt(profiles_dir):
    # energy_threshold must be 1-10 per the Pydantic model
    _write_profile(profiles_dir, "bad_schema", {"name": "x", "energy_threshold": 99})
    with pytest.raises(profiles.ProfileCorruptError):
        profiles.load_profile("bad_schema")


def test_fallback_never_crashes_on_missing_profile(profiles_dir):
    _write_profile(profiles_dir, profiles.DEFAULT_FALLBACK_PROFILE, {"name": profiles.DEFAULT_FALLBACK_PROFILE})
    profile = profiles.load_profile_or_fallback("nonexistent")
    assert profile.name == profiles.DEFAULT_FALLBACK_PROFILE


def test_fallback_never_crashes_on_corrupt_requested_profile(profiles_dir):
    # This is the exact C-03 scenario: the requested profile file exists but is malformed
    # JSON. Before the fix, load_profile_or_fallback only caught FileNotFoundError, so this
    # crashed auto_pilot.py's main() at startup, outside any try/except.
    (profiles_dir / "corrupted.json").write_text("{oops", encoding="utf-8")
    _write_profile(profiles_dir, profiles.DEFAULT_FALLBACK_PROFILE, {"name": profiles.DEFAULT_FALLBACK_PROFILE})

    profile = profiles.load_profile_or_fallback("corrupted")
    assert profile.name == profiles.DEFAULT_FALLBACK_PROFILE


def test_fallback_falls_through_to_any_available_profile_if_fallback_also_broken(profiles_dir):
    (profiles_dir / "corrupted.json").write_text("{oops", encoding="utf-8")
    (profiles_dir / f"{profiles.DEFAULT_FALLBACK_PROFILE}.json").write_text("{also broken", encoding="utf-8")
    _write_profile(profiles_dir, "healthy_profile", {"name": "healthy_profile"})

    profile = profiles.load_profile_or_fallback("corrupted")
    assert profile.name == "healthy_profile"


def test_fallback_raises_only_when_nothing_at_all_is_usable(profiles_dir):
    (profiles_dir / "corrupted.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        profiles.load_profile_or_fallback("corrupted")
