import train_loop


def _verdict(title, score=5):
    return train_loop.ClipVerdict(clip_title=title, reward_score=score, reasoning="x")


# --- filter_verdicts_to_known_clips (M-04) -----------------------------------------------

def test_keeps_verdicts_matching_real_clips():
    clips = [{"title": "A"}, {"title": "B"}]
    batch = train_loop.CriticBatch(verdicts=[_verdict("A"), _verdict("B")])
    result = train_loop.filter_verdicts_to_known_clips(batch, clips)
    assert [v.clip_title for v in result.verdicts] == ["A", "B"]


def test_drops_hallucinated_verdict_title():
    clips = [{"title": "A"}]
    batch = train_loop.CriticBatch(verdicts=[_verdict("A"), _verdict("Nonexistent Clip")])
    result = train_loop.filter_verdicts_to_known_clips(batch, clips)
    assert [v.clip_title for v in result.verdicts] == ["A"]


def test_drops_duplicate_verdict_keeping_first():
    clips = [{"title": "A"}]
    batch = train_loop.CriticBatch(verdicts=[_verdict("A", score=3), _verdict("A", score=9)])
    result = train_loop.filter_verdicts_to_known_clips(batch, clips)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].reward_score == 3


def test_empty_batch_stays_empty():
    clips = [{"title": "A"}]
    result = train_loop.filter_verdicts_to_known_clips(train_loop.CriticBatch(verdicts=[]), clips)
    assert result.verdicts == []


# --- ai_guidelines.txt rule capping (L-04) -----------------------------------------------

def test_cap_rules_leaves_short_list_untouched():
    rules = ["a", "b", "c"]
    assert train_loop._cap_rules(rules, max_count=10) == rules


def test_cap_rules_keeps_most_recent_when_over_limit():
    rules = [f"rule-{i}" for i in range(10)]
    capped = train_loop._cap_rules(rules, max_count=3)
    assert capped == ["rule-7", "rule-8", "rule-9"]


def test_cap_rules_exact_boundary():
    rules = ["a", "b", "c"]
    assert train_loop._cap_rules(rules, max_count=3) == rules
