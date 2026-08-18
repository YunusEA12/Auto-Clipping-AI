import threading

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
