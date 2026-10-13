"""The AI can reject weak moments, and rejected moments never reach the final picks."""
from clipper.llm import (MAX_CHARS_PER_CANDIDATE, _user_prompt, apply_llm_scores,
                         select_final, SYSTEM_PROMPT)
from clipper.models import Candidate


def _cand(cid, heuristic, text="some words here"):
    return Candidate(start=cid * 100.0, end=cid * 100.0 + 60, text=text, id=cid, heuristic=heuristic)


def test_keep_false_is_parsed_and_true_is_default():
    a, b, c = _cand(1, 50), _cand(2, 50), _cand(3, 50)
    apply_llm_scores([a, b, c], {"clips": [
        {"id": 1, "keep": False, "hook": 2},
        {"id": 2, "keep": True, "hook": 9},
        {"id": 3, "hook": 8},   # no keep field: treated as keep
    ]})
    assert a.llm["keep"] is False
    assert b.llm["keep"] is True
    assert c.llm["keep"] is True


def test_rejected_moments_are_not_picked_even_with_high_local_score():
    rejected = _cand(1, 95)
    good = _cand(2, 40)
    apply_llm_scores([rejected, good], {"clips": [
        {"id": 1, "keep": False, "hook": 1, "standalone": 1, "emotional_payoff": 1},
        {"id": 2, "keep": True, "hook": 9, "standalone": 9, "emotional_payoff": 9},
    ]})
    from clipper.llm import combined_score
    for c in (rejected, good):
        c.final = combined_score(c)
    picked = select_final([rejected, good], count=2)
    assert picked == [good]


def test_no_llm_review_keeps_local_ordering():
    a, b = _cand(1, 80), _cand(2, 60)
    a.final, b.final = 80, 60
    assert select_final([b, a], count=2) == [a, b]


def test_prompt_asks_for_keep_and_sends_the_whole_clip():
    long_text = "word " * 400   # about 2000 characters
    prompt = _user_prompt([_cand(7, 50, text=long_text)])
    assert '"keep"' in prompt
    assert "[id=7]" in prompt
    assert len(long_text) <= MAX_CHARS_PER_CANDIDATE
    assert len(prompt) > 1900


def test_system_prompt_states_the_selection_rules():
    for phrase in ("hook", "payoff", "Reject", "Keep a clip only if"):
        assert phrase in SYSTEM_PROMPT
