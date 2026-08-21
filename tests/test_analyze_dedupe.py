import analyze


def _clip(title, start=0.0, end=10.0):
    return analyze.Clip(
        start_time=start, end_time=end, title=title, hook_explanation="x", hook_style="other",
        viral_score=5, energy_rating=5, description="x", hashtags=["#fyp"],
    )


def test_unique_titles_are_untouched():
    clips = [_clip("A"), _clip("B"), _clip("C")]
    result = analyze.ensure_unique_titles(clips)
    assert [c.title for c in result] == ["A", "B", "C"]


def test_duplicate_titles_get_renamed_uniquely():
    # C-04: two clips sharing a title used to silently collide everywhere title is used as
    # a join key (rendered filename, critic verdict, purge/upload lookup) — the second
    # clip's render/verdict would overwrite or orphan the first's.
    clips = [_clip("Krasser Moment"), _clip("Krasser Moment"), _clip("Anderer Clip")]
    result = analyze.ensure_unique_titles(clips)
    titles = [c.title for c in result]
    assert len(titles) == len(set(titles))  # every title is now unique
    assert titles[0] == "Krasser Moment"
    assert titles[1] == "Krasser Moment (2)"
    assert titles[2] == "Anderer Clip"


def test_triple_duplicate_titles_all_get_unique_suffixes():
    clips = [_clip("Same"), _clip("Same"), _clip("Same")]
    titles = [c.title for c in analyze.ensure_unique_titles(clips)]
    assert titles == ["Same", "Same (2)", "Same (3)"]


def test_dedupe_does_not_collide_with_a_pre_existing_numbered_title():
    # If the LLM already produced "Clip (2)" as an original title, a naive rename of a
    # duplicate "Clip" must not silently collide with it.
    clips = [_clip("Clip"), _clip("Clip (2)"), _clip("Clip")]
    titles = [c.title for c in analyze.ensure_unique_titles(clips)]
    assert len(titles) == len(set(titles))
    assert titles[0] == "Clip"
    assert titles[1] == "Clip (2)"
