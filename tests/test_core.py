import numpy as np

from clipper.candidates import build_candidates
from clipper.llm import apply_llm_scores, combined_score, parse_llm_json
from clipper.media import write_srt
from clipper.models import Candidate, Segment
from clipper.scoring import score_candidate, select_diverse
from clipper.transcribe import group_words


def _units(n=60, seg_len=3.0, gap=0.2, text="and then we kept going on the topic"):
    out, t = [], 0.0
    for i in range(n):
        out.append(Segment(start=t, end=t + seg_len, text=text + ("." if i % 4 == 3 else "")))
        t += seg_len + gap
    return out


def test_group_words_splits_on_punctuation_and_pauses():
    words = [(0.0, 0.3, " Hello"), (0.3, 0.6, " there."), (0.6, 0.9, " Next"),
             (2.0, 2.3, " pause"), (2.3, 2.6, " here?")]
    units = group_words(words)
    assert [u.text for u in units] == ["Hello there.", "Next", "pause here?"]
    assert units[0].start == 0.0 and units[0].end == 0.6


def test_candidates_respect_length_bounds():
    units = _units(n=80)
    cands = build_candidates(units, 30, 120)
    assert cands, "expected candidates"
    for c in cands:
        assert 30 <= c.duration <= 120 + 1e-6
    assert len({c.id for c in cands}) == len(cands)


def test_candidates_empty_when_video_too_short():
    units = _units(n=5)  # ~17s total
    assert build_candidates(units, 30, 120) == []


def test_scoring_prefers_hooky_lively_complete_moments():
    units = _units(n=60, text="we talked about the weather and the traffic in town")
    # Inject a strong, complete moment around index 20-40
    units[20] = Segment(units[20].start, units[20].end,
                        "Why does nobody tell you this secret?")
    units[21] = Segment(units[21].start, units[21].end,
                        "It is the biggest mistake, and honestly it is crazy and terrible.")
    energy = np.zeros(2000)
    energy[int(units[20].start / 0.5): int(units[30].end / 0.5)] = 2.0   # loud section
    energy[int(units[40].start / 0.5): int(units[50].end / 0.5)] = -1.5  # quiet dead air

    cands = build_candidates(units, 30, 120)
    hot = next(c for c in cands if c.segment_range[0] == 20 and c.duration >= 40)
    dull = next(c for c in cands if c.segment_range[0] == 40 and c.duration >= 40)
    for c in (hot, dull):
        score_candidate(c, units, energy)
    assert hot.heuristic > dull.heuristic
    assert hot.features["hook"] > dull.features["hook"]
    assert dull.features["quiet_ratio"] > hot.features["quiet_ratio"]


def test_select_diverse_avoids_overlap():
    a = Candidate(0, 60, "a", heuristic=90)
    b = Candidate(10, 70, "b", heuristic=85)   # heavy overlap with a
    c = Candidate(80, 140, "c", heuristic=50)
    picked = select_diverse(sorted([a, b, c], key=lambda x: -x.heuristic), count=3)
    assert picked == [a, c]


def test_parse_llm_json_handles_fences_and_chatter():
    raw = 'Sure! ```json\n{"clips": [{"id": 3, "hook": 8}]}\n```'
    assert parse_llm_json(raw)["clips"][0]["id"] == 3


def test_llm_scores_apply_and_blend():
    c = Candidate(0, 60, "x", id=3, heuristic=50)
    apply_llm_scores([c], {"clips": [{"id": 3, "hook": 10, "standalone": 10,
                                      "emotional_payoff": 10, "title": "T" * 100,
                                      "reason": "r"}, {"id": 99, "hook": 1}]})
    assert c.llm["hook"] == 10 and len(c.llm["title"]) == 80
    assert combined_score(c) == round(0.4 * 50 + 0.6 * 100, 2)


def test_combined_score_falls_back_to_heuristic():
    c = Candidate(0, 60, "x", heuristic=42)
    assert combined_score(c) == 42


def test_srt_is_rebased_to_clip_start(tmp_path):
    segs = [Segment(9.0, 11.0, "before"), Segment(12.0, 14.5, "inside"),
            Segment(40.0, 42.0, "after")]
    out = write_srt(segs, window_start=10.0, window_end=20.0, out=tmp_path / "c.srt")
    text = out.read_text()
    assert "before" in text  # overlaps the window start
    assert "00:00:02,000 --> 00:00:04,500" in text
    assert "after" not in text
