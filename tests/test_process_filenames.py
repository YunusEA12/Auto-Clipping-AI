import fnmatch

import process


def test_clip_output_filename_matches_its_own_glob_pattern():
    # H-06: process.render_clip() writes clip_<index>_<slug>.mp4 and train_loop.py
    # independently re-derives that pattern via glob to locate rendered files when no
    # explicit path dict was handed to it. Both now come from these two functions — this
    # test is the regression guard that keeps them from silently drifting apart again.
    for index in (1, 2, 42):
        for title in ("Krasser Moment!", "  weird///chars??", "Ünïcödé Titel", ""):
            filename = process.clip_output_filename(index, title)
            assert fnmatch.fnmatch(filename, process.clip_glob_pattern(index))


def test_clip_glob_pattern_does_not_match_a_different_index():
    filename = process.clip_output_filename(3, "Some Clip")
    assert not fnmatch.fnmatch(filename, process.clip_glob_pattern(4))


def test_clip_output_filename_is_slugified():
    filename = process.clip_output_filename(1, "Hello, World!")
    assert filename == "clip_1_Hello_World.mp4"


def test_empty_title_still_produces_a_valid_filename():
    filename = process.clip_output_filename(1, "")
    assert filename == "clip_1_clip.mp4"
