"""Re-cut a finished project's clips in another shape, from the video already downloaded.

Nothing is downloaded again. Each clip keeps the same start and end, the same title and
score, and gets a new file in the chosen aspect ratio (and captions, if the project had them).
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .captions import build_ass
from .media import MediaError, cut_clip, export_mp3, extract_thumbnail
from .models import Segment
from .pipeline import Params
from .presets import PLATFORMS, parse_aspect, resolve_output

# Shapes offered in the UI. Anything else can be typed as W:H.
ASPECT_CHOICES = [
    {"id": "9:16", "label": "Vertical", "hint": "TikTok, Shorts, Reels"},
    {"id": "4:5", "label": "Portrait", "hint": "Feed posts"},
    {"id": "1:1", "label": "Square", "hint": "Feed and grids"},
    {"id": "16:9", "label": "Landscape", "hint": "YouTube, X"},
]


def find_source(job_dir: Path) -> Path:
    """The full downloaded video for a project."""
    for p in sorted(job_dir.glob("source.*")):
        if p.suffix not in {".part", ".ytdl"}:
            return p
    raise MediaError("The downloaded video is no longer in this project folder.")


def source_ready(job_dir: Path) -> bool:
    try:
        find_source(job_dir)
        return True
    except MediaError:
        return False


def render_variant(job_dir: Path, clips: list[dict], params: Params, variant_id: str,
                   aspect: str, on_clip: Callable[[dict], None]) -> list[dict]:
    """Cut every clip again in the new aspect ratio. Calls on_clip as each one is ready."""
    w, h = parse_aspect(aspect)
    aspect_text = f"{w}:{h}"
    spec_params = replace(params, aspect_mode="custom", aspect=aspect_text)
    spec = resolve_output(spec_params)
    platform = PLATFORMS[params.platform]
    src = find_source(job_dir)
    out_dir = job_dir / "variants" / variant_id
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs_root = job_dir.parent

    units: list[Segment] = []
    if params.captions:
        transcript = job_dir / "transcript.json"
        if transcript.exists():
            units = [Segment(**u) for u in json.loads(transcript.read_text())]

    def rel(p: Path | None) -> str | None:
        return str(p.relative_to(jobs_root)).replace("\\", "/") if p else None

    results: list[dict] = []
    for index, clip in enumerate(clips, start=1):
        stem = f"clip_{index:02d}"
        mp4 = out_dir / f"{stem}.mp4"
        ass = None
        if params.captions and units:
            ass = out_dir / f"{stem}.ass"
            ass.write_text(build_ass(
                units, clip["start"], clip["end"], width=spec["width"], height=spec["height"],
                style=params.caption_style, position=params.caption_position,
                uppercase=params.caption_uppercase, accent=params.caption_color,
                bottom_margin=round(platform["caption_bottom"] * spec["height"] / 1920)),
                encoding="utf-8")
        cut_kwargs = dict(width=spec["width"], height=spec["height"], fps=spec["fps"],
                          crf=spec["crf"], preset=spec["preset"], ratio=spec["aspect"])
        try:
            cut_clip(src, mp4, clip["start"], clip["end"], subtitles=ass, **cut_kwargs)
        except MediaError:
            if ass is None:
                raise
            cut_clip(src, mp4, clip["start"], clip["end"], subtitles=None, **cut_kwargs)
            ass = None
        mp3 = None
        if spec["mp3"]:
            mp3 = export_mp3(src, out_dir / f"{stem}.mp3", clip["start"], clip["end"])
        thumb = extract_thumbnail(src, clip["start"] + min(2.0, clip["duration"] / 2),
                                  out_dir / f"{stem}.jpg")
        record = dict(clip)
        record.update({
            "id": f"{variant_id}-{stem}",
            "output": spec["description"],
            "files": {"video": rel(mp4), "thumbnail": rel(thumb), "audio": rel(mp3),
                      "captions": rel(ass)},
        })
        results.append(record)
        on_clip(record)
    return results
