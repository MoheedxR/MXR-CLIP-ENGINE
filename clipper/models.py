from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Segment:
    """A sentence-like unit of speech with timestamps (seconds)."""
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    """A possible clip: a contiguous window made of whole speech units."""
    start: float
    end: float
    text: str
    segment_range: tuple[int, int] = (0, 0)   # inclusive indices into the unit list
    starts_cleanly: bool = True
    ends_cleanly: bool = True
    id: int = 0
    heuristic: float = 0.0          # 0-100, local signals only
    features: dict = field(default_factory=dict)
    llm: dict | None = None         # hook / standalone / emotional_payoff / title / reason
    final: float = 0.0              # 0-100, blended score used for ranking

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlap_ratio(self, other: "Candidate") -> float:
        inter = min(self.end, other.end) - max(self.start, other.start)
        if inter <= 0:
            return 0.0
        return inter / min(self.duration, other.duration)
