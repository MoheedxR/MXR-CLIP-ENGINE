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

log = logging.getLogger(__name__)

BATCH_SIZE = 12
MAX_CHARS_PER_CANDIDATE = 1200

SYSTEM_PROMPT = (
    "You are an expert short-form video editor who picks clips for TikTok, YouTube Shorts "
    "and Instagram Reels. You judge whether a transcript excerpt would make people stop "
    "scrolling, watch to the end, and share it. Penalise excerpts that need missing context, "
    "start mid-thought, have a weak opening, or are filler. Be strict: most excerpts are "
    "average. Respond with JSON only."
)


def _user_prompt(batch: list[Candidate]) -> str:
    items = []
    for c in batch:
        text = c.text[:MAX_CHARS_PER_CANDIDATE]
        items.append(f"[id={c.id}] ({c.duration:.0f}s) {text}")
    return (
        "Score each candidate clip. Return a JSON object of this exact shape:\n"
        '{"clips": [{"id": <int>, "hook": <0-10>, "standalone": <0-10>, '
        '"emotional_payoff": <0-10>, "title": "<max 60 chars, punchy, no false claims>", '
        '"reason": "<one sentence>"}]}\n\n'
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
