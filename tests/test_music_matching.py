import json
import random

import music_matching
import process


def _make_music_dir(tmp_path, tags=None):
    d = tmp_path / "music"
    d.mkdir()
    for n in ("calm.mp3", "loud.mp3", "untagged.mp3"):
        (d / n).write_bytes(b"x")
    if tags is not None:
        (d / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    return d


TAGS = {
    "calm.mp3": {"energy": 2, "moods": ["chill", "ambient"]},
    "loud.mp3": {"energy": 9, "moods": ["intense", "energetic"]},
}


def test_load_track_tags_missing_and_corrupt(tmp_path):
    assert music_matching.load_track_tags(tmp_path) == {}
    (tmp_path / "tags.json").write_text("{not json", encoding="utf-8")
    assert music_matching.load_track_tags(tmp_path) == {}
    (tmp_path / "tags.json").write_text("[1]", encoding="utf-8")
    assert music_matching.load_track_tags(tmp_path) == {}


def test_desired_moods_from_hook_style_and_title():
    moods = music_matching.desired_moods({"hook_style": "in_media_res", "title": "Krasser Clutch am Ende"})
    assert {"energetic", "intense"} <= moods
    assert "upbeat" in music_matching.desired_moods({"title": "Das war so lustig"})
    assert music_matching.desired_moods(None) == set()


def test_score_prefers_matching_energy_and_mood():
    clip = {"energy_rating": 9, "hook_style": "in_media_res", "title": "Insane Clutch"}
    assert music_matching.score_track(TAGS["loud.mp3"], clip) > music_matching.score_track(TAGS["calm.mp3"], clip)
    assert music_matching.score_track(None, clip) == music_matching.NEUTRAL_SCORE
    assert music_matching.score_track(TAGS["calm.mp3"], None) == music_matching.NEUTRAL_SCORE


def test_pick_follows_clip_energy(tmp_path, monkeypatch):
    d = _make_music_dir(tmp_path, TAGS)
    monkeypatch.setattr(process, "MUSIC_DIR", d)
    monkeypatch.setattr(process.optimization_engine, "preferred_music_track", lambda: None)
    random.seed(1)
    loud_clip = {"energy_rating": 10, "hook_style": "shocking_visual", "title": "Wahnsinn"}
    picks = [process.pick_background_track(loud_clip).name for _ in range(300)]
    assert picks.count("loud.mp3") > picks.count("calm.mp3") * 2
    assert "calm.mp3" in picks  # exploration weight never reaches zero


def test_pick_without_clip_or_tags_still_works(tmp_path, monkeypatch):
    d = _make_music_dir(tmp_path)
    monkeypatch.setattr(process, "MUSIC_DIR", d)
    monkeypatch.setattr(process.optimization_engine, "preferred_music_track", lambda: None)
    assert process.pick_background_track().name in {"calm.mp3", "loud.mp3", "untagged.mp3"}


def test_preferred_track_still_wins_outside_exploration(tmp_path, monkeypatch):
    d = _make_music_dir(tmp_path, TAGS)
    monkeypatch.setattr(process, "MUSIC_DIR", d)
    monkeypatch.setattr(process.optimization_engine, "preferred_music_track", lambda: "calm.mp3")
    monkeypatch.setattr(process.random, "random", lambda: 0.99)
    loud_clip = {"energy_rating": 10, "hook_style": "shocking_visual"}
    assert process.pick_background_track(loud_clip).name == "calm.mp3"


def test_shipped_tags_match_shipped_tracks():
    tags = music_matching.load_track_tags(process.MUSIC_DIR)
    tracks = {p.name for p in process.MUSIC_DIR.iterdir() if p.suffix == ".mp3"}
    assert set(tags) == tracks
