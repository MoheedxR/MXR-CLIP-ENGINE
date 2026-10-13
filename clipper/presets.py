"""Output presets: platforms, quality levels, caption styles and rating labels.

Platform caption margins are design defaults that keep captions clear of the
bottom UI (captions, buttons, username). Tune them if a platform changes.
"""
from __future__ import annotations

import re

PLATFORMS: dict[str, dict] = {
    "tiktok": {
        "label": "TikTok",
        "caption_bottom": 360,   # px from bottom on a 1920px-tall frame
        "hint": "9:16 · captions lifted above TikTok's bottom UI",
    },
    "shorts": {
        "label": "YouTube Shorts",
        "caption_bottom": 300,
        "hint": "9:16 · captions lifted above Shorts' title bar",
    },
    "reels": {
        "label": "Instagram Reels",
        "caption_bottom": 330,
        "hint": "9:16 · captions lifted above Reels' caption and buttons",
    },
}

QUALITY: dict[str, dict] = {
    "quick": {
        "label": "Quick MP4 + MP3",
        "width": 1080, "height": 1920, "fps": 30,
        "crf": 23, "preset": "veryfast",
        "max_source": 1080, "mp3": True,
        "hint": "Fastest render. Also saves an MP3 of each clip's audio.",
    },
    "4k30": {
        "label": "4K · 30 fps",
        "width": 2160, "height": 3840, "fps": 30,
        "crf": 18, "preset": "medium",
        "max_source": 2160, "mp3": False,
        "hint": "2160×3840. Slowest. Best with a 4K source.",
    },
    "fps60": {
        "label": "60 fps",
        "width": 1080, "height": 1920, "fps": 60,
        "crf": 20, "preset": "fast",
        "max_source": 1080, "mp3": False,
        "hint": "Smoother motion. Needs a 60 fps source for real gains.",
    },
    "fps120": {
        "label": "120 fps",
        "width": 1080, "height": 1920, "fps": 120,
        "crf": 20, "preset": "fast",
        "max_source": 1080, "mp3": False,
        "hint": "Very smooth. Needs a 120 fps source; slower to render.",
    },
}

# ---- Custom output settings (used when a setting is switched to "Custom") ----
# Resolution is the SHORT side in pixels: 1080 means 1080x1920 at 9:16,
# 1080x1080 at 1:1 and 1920x1080 at 16:9.
RESOLUTIONS: dict[int, dict] = {
    720: {"label": "720p"},
    1080: {"label": "1080p"},
    1440: {"label": "1440p"},
    2160: {"label": "4K"},
}
FPS_OPTIONS: tuple[int, ...] = (24, 25, 30, 50, 60, 120)
ASPECT_PRESETS: tuple[str, ...] = ("9:16", "4:5", "1:1", "16:9")
DEFAULT_ASPECT = "9:16"
MODES: tuple[str, ...] = ("default", "custom")
ASPECT_MIN_RATIO, ASPECT_MAX_RATIO = 0.4, 2.5


def parse_aspect(text: str) -> tuple[int, int]:
    """Parse 'W:H' (also accepts '/' or 'x'). Returns the two integers."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:/x]\s*(\d{1,2})\s*", str(text or ""))
    if not m:
        raise ValueError("aspect ratio must look like 9:16 or 4:5")
    w, h = int(m[1]), int(m[2])
    if w < 1 or h < 1:
        raise ValueError("both sides of the aspect ratio must be at least 1")
    if not ASPECT_MIN_RATIO <= w / h <= ASPECT_MAX_RATIO:
        raise ValueError("aspect ratio must be between 2:5 and 5:2")
    return w, h


def encode_settings(short: int, fps: int) -> tuple[int, str]:
    """Return (crf, x264 preset) for an output size and frame rate."""
    if short >= 2160:
        return 18, "medium"
    if short >= 1440 or fps > 30:
        return 20, "fast"
    return 23, "veryfast"


def resolve_output(params) -> dict:
    """Turn the form settings into one concrete output spec.

    Default settings reproduce the QUALITY presets exactly. Custom settings
    override resolution, frame rate and aspect ratio independently.
    """
    base = QUALITY[params.quality]
    if params.quality_mode == "custom":
        short, mp3, base_fps = params.resolution, True, 30
    else:
        short, mp3, base_fps = min(base["width"], base["height"]), base["mp3"], base["fps"]
    fps = params.fps if params.fps_mode == "custom" else base_fps
    aw, ah = parse_aspect(params.aspect) if params.aspect_mode == "custom" else (9, 16)
    ratio = aw / ah
    if ratio <= 1:
        width, height = short, round(short / ratio)
    else:
        height, width = short, round(short * ratio)
    width -= width % 2   # even sizes for yuv420p
    height -= height % 2
    crf, preset = encode_settings(short, fps)
    max_source = 2160 if short >= 2160 else 1440 if short >= 1440 else 1080
    return {
        "width": width, "height": height, "fps": fps, "crf": crf, "preset": preset,
        "max_source": max_source, "mp3": mp3, "aspect": (aw, ah), "short": short,
        "description": f"{width}×{height} · {fps} fps · {aw}:{ah}",
    }


CAPTION_STYLES: dict[str, dict] = {
    "highlight": {"label": "Word highlight", "hint": "Each word lights up as it's spoken"},
    "classic": {"label": "Classic", "hint": "White text, black outline"},
    "box": {"label": "Boxed", "hint": "White text on a dark bar"},
    "neon": {"label": "Neon", "hint": "White text with a coloured glow edge"},
    "minimal": {"label": "Minimal", "hint": "Small and clean"},
}

CAPTION_POSITIONS = ("top", "middle", "bottom")

ACCENT_COLORS: dict[str, str] = {
    "yellow": "#FFD400",
    "cyan": "#22D3EE",
    "pink": "#FF4D8D",
    "lime": "#A3E635",
    "white": "#FFFFFF",
}

# Score bands for the "worst to best" watch-rate scale (0-100 estimate)
RATING_BANDS = [(35, "Weak"), (55, "Fair"), (72, "Good"), (101, "Excellent")]


def rating_label(watch_rate: float | None) -> str | None:
    if watch_rate is None:
        return None
    for limit, label in RATING_BANDS:
        if watch_rate < limit:
            return label
    return "Excellent"


def validate(params) -> None:
    """Raise ValueError for unknown preset ids. Used by the API and pipeline."""
    if params.platform not in PLATFORMS:
        raise ValueError(f"unknown platform '{params.platform}'")
    if params.quality not in QUALITY:
        raise ValueError(f"unknown quality '{params.quality}'")
    if getattr(params, "quality_mode", "default") not in MODES:
        raise ValueError("quality mode must be default or custom")
    if getattr(params, "fps_mode", "default") not in MODES:
        raise ValueError("frame rate mode must be default or custom")
    if getattr(params, "aspect_mode", "default") not in MODES:
        raise ValueError("aspect ratio mode must be default or custom")
    if params.quality_mode == "custom" and params.resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution '{params.resolution}'")
    if params.fps_mode == "custom" and params.fps not in FPS_OPTIONS:
        raise ValueError(f"unknown frame rate '{params.fps}'")
    if params.aspect_mode == "custom":
        parse_aspect(params.aspect)
    if params.caption_style not in CAPTION_STYLES:
        raise ValueError(f"unknown caption style '{params.caption_style}'")
    if params.caption_position not in CAPTION_POSITIONS:
        raise ValueError(f"unknown caption position '{params.caption_position}'")
    if params.caption_color not in ACCENT_COLORS:
        raise ValueError(f"unknown caption colour '{params.caption_color}'")
