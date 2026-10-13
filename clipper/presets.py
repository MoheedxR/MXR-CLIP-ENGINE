"""Output presets: platforms, quality levels, caption styles and rating labels.

Platform caption margins are design defaults that keep captions clear of the
bottom UI (captions, buttons, username). Tune them if a platform changes.
"""
from __future__ import annotations

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
    if params.caption_style not in CAPTION_STYLES:
        raise ValueError(f"unknown caption style '{params.caption_style}'")
    if params.caption_position not in CAPTION_POSITIONS:
        raise ValueError(f"unknown caption position '{params.caption_position}'")
    if params.caption_color not in ACCENT_COLORS:
        raise ValueError(f"unknown caption colour '{params.caption_color}'")
