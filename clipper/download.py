"""Fetch a video from a URL (YouTube, Vimeo, and anything else yt-dlp supports)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import config

ProgressFn = Callable[[float, str], None]  # (fraction 0-1, human message)


def download(url: str, out_dir: Path, on_progress: ProgressFn | None = None,
             max_height: int | None = None, prefer_fps: bool = False,
             rate_limit: int | None = None) -> tuple[Path, str]:
    """Download the best MP4-compatible stream up to max_height.

    prefer_fps asks yt-dlp to favour higher frame-rate streams when several exist.
    Returns (video_path, title).
    """
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    h = max_height or config.MAX_DOWNLOAD_HEIGHT

    def hook(d: dict) -> None:
        if on_progress is None:
            return
        status = d.get("status")
        if status == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                frac = min(1.0, done / total)
                on_progress(frac, f"Downloading · {int(frac * 100)}%")
            else:
                on_progress(0.0, "Downloading…")
        elif status == "finished":
            on_progress(1.0, "Download complete, merging streams")

    opts = {
        "format": f"bv*[height<={h}]+ba/b[height<={h}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "progress_hooks": [hook],
    }
    if prefer_fps:
        opts["format_sort"] = ["fps", "res", "br"]
    if rate_limit:
        opts["ratelimit"] = rate_limit  # bytes per second
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    title = (info or {}).get("title") or "Untitled"

    mp4 = out_dir / "source.mp4"
    if mp4.exists():
        return mp4, title
    matches = [p for p in out_dir.glob("source.*") if p.suffix not in {".part", ".ytdl"}]
    if not matches:
        raise RuntimeError("download finished but no video file was found")
    return matches[0], title


def download_audio(url: str, out_dir: Path, rate_limit: int | None = None) -> tuple[Path, str]:
    """Fetch only the audio track, which is much smaller and quicker than the video.

    Returns (audio_path, title). The file can be any container FFmpeg can read.
    """
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "audio_src.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    if rate_limit:
        opts["ratelimit"] = rate_limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    title = (info or {}).get("title") or "Untitled"
    matches = [p for p in out_dir.glob("audio_src.*") if p.suffix not in {".part", ".ytdl"}]
    if not matches:
        raise RuntimeError("audio download finished but no audio file was found")
    return matches[0], title
