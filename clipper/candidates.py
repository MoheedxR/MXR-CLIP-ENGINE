"""Generate every window of whole speech units whose length is within bounds."""
from __future__ import annotations

import math

from .models import Candidate, Segment

MAX_START_POINTS = 3000  # caps work for very long videos


def _starts_cleanly(units: list[Segment], i: int) -> bool:
    """A clip should open on a new thought: a capitalised unit that follows a sentence end
    or a clear pause. Starting mid-sentence makes viewers bounce."""
    cur = units[i].text.lstrip()
    looks_like_start = cur[:1].isupper() or cur[:1].isdigit() or cur[:1] in "\"“'‘"
    if i == 0:
        return looks_like_start
    prev = units[i - 1]
    gap = units[i].start - prev.end
    prev_ends_sentence = prev.text.rstrip()[-1:] in ".?!…"
    return looks_like_start and (prev_ends_sentence or gap >= 1.2)


def _ends_cleanly(unit: Segment) -> bool:
    return unit.text.rstrip()[-1:] in ".?!…\"”')"


def build_candidates(units: list[Segment], min_s: float, max_s: float) -> list[Candidate]:
    n = len(units)
    if n == 0:
        return []
    stride = max(1, math.ceil(n / MAX_START_POINTS))
    out: list[Candidate] = []
    for i in range(0, n, stride):
        for j in range(i, n):
            dur = units[j].end - units[i].start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            text = " ".join(u.text for u in units[i:j + 1])
            out.append(Candidate(
                start=units[i].start,
                end=units[j].end,
                text=text,
                segment_range=(i, j),
                starts_cleanly=_starts_cleanly(units, i),
                ends_cleanly=_ends_cleanly(units[j]),
            ))
    for k, c in enumerate(out):
        c.id = k
    return out
