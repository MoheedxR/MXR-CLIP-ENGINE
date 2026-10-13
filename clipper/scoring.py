"""Local, free engagement heuristics.

These are proxies for what tends to hold attention in short-form video:
a strong opening hook, emotionally charged language, lively delivery, a
complete thought, a sensible length, and no dead air. They do not measure
real audience response. Weights are in one place so they can be tuned later
against real analytics.
"""
from __future__ import annotations

import re

import numpy as np

from .media import ENERGY_STEP
from .models import Candidate, Segment

HOOK_WORDS = {
    "you", "your", "you're", "why", "how", "never", "stop", "secret", "secrets",
    "mistake", "mistakes", "wrong", "nobody", "everyone", "truth", "actually",
    "biggest", "worst", "best", "real", "free", "money", "honestly", "here's",
    "heres", "what", "don't", "dont", "no one", "little known", "hidden",
    "warning", "lie", "lies", "crazy", "insane", "shocking", "unbelievable",
}

AROUSAL_WORDS = {
    "love", "hate", "amazing", "terrible", "awesome", "awful", "incredible",
    "crazy", "insane", "shocking", "wild", "scared", "afraid", "angry", "furious",
    "excited", "obsessed", "fail", "failed", "failure", "win", "won", "broke",
    "broken", "perfect", "disaster", "genius", "stupid", "brutal", "funny",
    "hilarious", "dangerous", "fear", "pain", "happy", "sad", "proud", "shame",
    "unbelievable", "ridiculous", "massive", "huge", "secret", "truth", "lie",
    "money", "rich", "million", "billion",
}

# (weight, name). Positives sum to 1.0.
WEIGHTS = {
    "hook": 0.28,
    "emotion": 0.12,
    "energy": 0.18,
    "pace": 0.10,
    "completeness": 0.14,
    "length": 0.10,
    "closure": 0.08,
}

PACE_TARGET_WPS = 2.6     # comfortable speaking rate for short-form
HOOK_WINDOW_S = 5.0       # first seconds count as the hook
QUIET_Z = -0.7            # below this z-score counts as quiet
MAX_INTERNAL_GAP = 2.0    # seconds of silence before it hurts the score

_WORD_RE = re.compile(r"[a-z']+")


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def score_candidate(c: Candidate, units: list[Segment], energy_z: np.ndarray) -> None:
    """Fill c.heuristic (0-100) and c.features in place."""
    i, j = c.segment_range
    window = units[i:j + 1]
    dur = max(c.duration, 1e-6)

    # Hook: what the first ~5 seconds look like
    hook_units = [u for u in window if u.start < c.start + HOOK_WINDOW_S] or window[:1]
    hook_text = " ".join(u.text for u in hook_units)
    hook_lower = hook_text.lower()
    hook_tokens = _WORD_RE.findall(hook_lower)
    hook = 0.0
    if "?" in hook_text:
        hook += 0.30
    if re.search(r"\d", hook_text):
        hook += 0.15
    hook += min(0.35, 0.12 * sum(1 for t in hook_tokens if t in HOOK_WORDS))
    if len(window[0].text.split()) <= 9:
        hook += 0.20  # punchy opening line
    hook = _clip01(hook)

    # Emotional language density
    tokens = _WORD_RE.findall(c.text.lower())
    n_words = max(len(tokens), 1)
    arousal_hits = sum(1 for t in tokens if t in AROUSAL_WORDS)
    emotion = _clip01(arousal_hits / n_words * 100 / 4.0)  # ~4 charged words per 100 = max

    # Delivery energy from loudness: how loud on average, and how much it varies
    lo = int(c.start / ENERGY_STEP)
    hi = int(np.ceil(c.end / ENERGY_STEP))
    z = energy_z[lo:hi] if hi > lo else np.zeros(1)
    mean_z = float(z.mean()) if z.size else 0.0
    dyn = float(z.std()) if z.size else 0.0
    quiet_ratio = float((z < QUIET_Z).mean()) if z.size else 0.0
    energy = _clip01(0.5 + 0.25 * mean_z) * 0.6 + _clip01(dyn / 0.9) * 0.4

    # Pace: close to a lively but intelligible speaking rate
    wps = n_words / dur
    pace = _clip01(1.0 - abs(wps - PACE_TARGET_WPS) / 2.2)

    # Completeness: starts and ends on a thought
    completeness = 0.5 * float(c.starts_cleanly) + 0.5 * float(c.ends_cleanly)

    # Length: sweet spot 45-90s, acceptable 30-120s
    if dur < 45:
        length = _clip01((dur - 30) / 15)
    elif dur <= 90:
        length = 1.0
    else:
        length = _clip01(1 - (dur - 90) / 30) * 0.8 + 0.2

    # Closure: a question that gets answered inside the clip
    closure = 1.0 if ("?" in c.text and c.text.rstrip()[-1:] in ".!") else 0.6

    # Dead-air penalties
    max_gap = 0.0
    for a, b in zip(window, window[1:]):
        max_gap = max(max_gap, b.start - a.end)
    gap_pen = min(0.3, 0.1 * max(0.0, max_gap - MAX_INTERNAL_GAP))
    quiet_pen = min(0.5, 0.8 * max(0.0, quiet_ratio - 0.25))

    parts = {
        "hook": hook, "emotion": emotion, "energy": energy, "pace": pace,
        "completeness": completeness, "length": length, "closure": closure,
    }
    positive = sum(WEIGHTS[k] * v for k, v in parts.items())
    total = _clip01(positive - gap_pen - quiet_pen)

    c.heuristic = round(100 * total, 2)
    c.features = {k: round(v, 3) for k, v in parts.items()}
    c.features.update({
        "quiet_ratio": round(quiet_ratio, 3),
        "max_gap_s": round(max_gap, 2),
        "words_per_second": round(wps, 2),
    })


def select_diverse(ranked: list[Candidate], count: int, max_overlap: float = 0.25) -> list[Candidate]:
    """Greedy pick in rank order, skipping anything that substantially overlaps a pick."""
    chosen: list[Candidate] = []
    for c in ranked:
        if all(c.overlap_ratio(x) <= max_overlap for x in chosen):
            chosen.append(c)
            if len(chosen) >= count:
                break
    return chosen
