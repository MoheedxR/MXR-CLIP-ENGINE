"""Optional LLM re-ranking of the shortlist.

The LLM reads each candidate transcript and rates its hook, whether it stands
alone without context, and its emotional payoff. It also suggests a title.
Works with any OpenAI-compatible chat completions endpoint.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from . import config
from .models import Candidate
from .scoring import select_diverse

log = logging.getLogger(__name__)

BATCH_SIZE = 12
MAX_CHARS_PER_CANDIDATE = 2500   # enough to see the whole clip, including its ending

SYSTEM_PROMPT = (
    "You are a senior short-form video editor for TikTok, YouTube Shorts and Instagram Reels. "
    "You only pick clips that a stranger scrolling their feed would stop for, watch to the end, "
    "and share. A strong clip: opens with a hook in the first 3 seconds (a bold claim, a question, "
    "a surprising fact, visible tension, or a direct 'you'); makes sense with no earlier context; "
    "has an arc that ends on a payoff, punchline, conclusion or cliffhanger rather than trailing off; "
    "covers one clear idea or story; and is concrete and specific rather than generic. "
    "Reject clips that are small talk, greetings, sponsor or housekeeping lines, rambling or filler, "
    "start mid-thought, depend on missing context, or end abruptly. Be strict: reject most candidates. "
    "Keep a clip only if you would post it yourself. Respond with JSON only."
)


def _user_prompt(batch: list[Candidate]) -> str:
    items = []
    for c in batch:
        text = c.text[:MAX_CHARS_PER_CANDIDATE]
        items.append(f"[id={c.id}] ({c.duration:.0f}s) {text}")
    return (
        "Review each candidate clip. Return a JSON object of this exact shape:\n"
        '{"clips": [{"id": <int>, "keep": <true or false>, "hook": <0-10>, "standalone": <0-10>, '
        '"emotional_payoff": <0-10>, "watch_rate": <0-100 integer>, '
        '"title": "<max 60 chars, punchy, no false claims>", '
        '"reason": "<one sentence: why it works, or what stops it working>"}]}\n\n'
        "keep is true only for clips you would post yourself. "
        "watch_rate is your honest estimate of the percentage of viewers still watching at the end. "
        "Use the full range. Rejected clips should usually score below 40, and only excellent clips "
        "above 70.\n\n"
        "Candidates:\n" + "\n\n".join(items)
    )


def parse_llm_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating code fences and chatter."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM reply")
    return json.loads(text[start:end + 1])


def _clamp(x, lo=0.0, hi=10.0) -> float:
    try:
        return float(min(hi, max(lo, float(x))))
    except (TypeError, ValueError):
        return 0.0


def apply_llm_scores(batch: list[Candidate], parsed: dict) -> None:
    by_id = {c.id: c for c in batch}
    for entry in parsed.get("clips", []):
        try:
            cid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if cid not in by_id:
            continue
        by_id[cid].llm = {
            "hook": _clamp(entry.get("hook")),
            "standalone": _clamp(entry.get("standalone")),
            "emotional_payoff": _clamp(entry.get("emotional_payoff")),
            "watch_rate": _clamp(entry.get("watch_rate"), 0.0, 100.0),
            "keep": entry.get("keep", True) is not False,
            "title": str(entry.get("title", "")).strip()[:80],
            "reason": str(entry.get("reason", "")).strip()[:300],
        }


def llm_enabled() -> bool:
    return bool(config.LLM_API_KEY)


def rerank(candidates: list[Candidate]) -> None:
    """Fill c.llm for each candidate. Raises on hard failure so the caller can fall back."""
    if not llm_enabled():
        raise RuntimeError("LLM_API_KEY is not set")
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    with httpx.Client(timeout=120) as client:
        for k in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[k:k + BATCH_SIZE]
            resp = client.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                headers=headers,
                json={
                    "model": config.LLM_MODEL,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": _user_prompt(batch)},
                    ],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            apply_llm_scores(batch, parse_llm_json(content))


def combined_score(c: Candidate) -> float:
    """Blend local heuristic (0-100) with LLM rating (0-100) when available."""
    if not c.llm:
        return c.heuristic
    llm_score = (c.llm["hook"] * 0.4 + c.llm["standalone"] * 0.3 + c.llm["emotional_payoff"] * 0.3) * 10
    return round(0.4 * c.heuristic + 0.6 * llm_score, 2)


def select_final(shortlist: list[Candidate], count: int) -> list[Candidate]:
    """Best non-overlapping picks. Moments the AI rejected are left out.

    If the AI reviewed the moments and rejected all of them, the caller decides
    what to do. This function only filters and orders.
    """
    kept = [c for c in shortlist if not c.llm or c.llm.get("keep", True)]
    kept.sort(key=lambda c: c.final, reverse=True)
    return select_diverse(kept, count)
