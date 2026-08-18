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
    leftovers = [p for p in tmp_path.iterdir() if p != path]
    assert leftovers == []
