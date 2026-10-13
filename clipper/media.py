"""ffmpeg / ffprobe helpers. All media-related work lives here."""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np

from .models import Segment

ENERGY_STEP = 0.5  # seconds per loudness frame


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise MediaError(f"{cmd[0]} failed: {tail}")
    return proc


def probe_duration(path: Path) -> float:
    proc = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(path)])
    return float(json.loads(proc.stdout)["format"]["duration"])


def probe_video(path: Path) -> dict:
    """Width, height and frame rate of the first video stream."""
    proc = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,avg_frame_rate", "-of", "json", str(path)])
    stream = json.loads(proc.stdout)["streams"][0]
    num, _, den = stream.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else float(num or 0)
    return {"width": int(stream["width"]), "height": int(stream["height"]), "fps": round(fps, 2)}


def extract_audio(src: Path, wav_out: Path) -> Path:
    """Mono 16 kHz PCM WAV: what Whisper and the loudness analysis both need."""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
          "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_out)])
    return wav_out


def read_wav_float32(wav_path: Path) -> np.ndarray:
    """Load our 16 kHz mono PCM WAV as float32 samples in [-1, 1]."""
    with wave.open(str(wav_path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def energy_profile(wav_path: Path, step: float = ENERGY_STEP) -> np.ndarray:
    """RMS loudness per `step` seconds."""
    samples = read_wav_float32(wav_path)
    frame = int(16000 * step)
    n = len(samples) // frame
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    frames = samples[: n * frame].reshape(n, frame)
    return np.sqrt(np.mean(frames ** 2, axis=1)).astype(np.float32)


def normalise_energy(profile: np.ndarray) -> np.ndarray:
    """Z-score the loudness curve, so 0 = this video's average loudness."""
    std = float(profile.std()) or 1.0
    return (profile - float(profile.mean())) / std


def extract_thumbnail(src: Path, at: float, out: Path) -> Path:
    _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
          "-frames:v", "1", "-vf", "scale=540:-2", str(out)])
    return out


def _fmt_srt_time(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[Segment], window_start: float, window_end: float, out: Path) -> Path:
    """Plain caption file for one clip, with times re-based to the clip's start."""
    lines: list[str] = []
    idx = 1
    for seg in segments:
        if seg.end <= window_start or seg.start >= window_end:
            continue
        s = max(seg.start - window_start, 0.0)
        e = min(seg.end, window_end) - window_start
        lines += [str(idx), f"{_fmt_srt_time(s)} --> {_fmt_srt_time(e)}", seg.text.strip(), ""]
        idx += 1
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def cut_clip(src: Path, out: Path, start: float, end: float, *, width: int, height: int,
             fps: int, crf: int, preset: str, subtitles: Path | None = None,
             ratio: tuple[int, int] = (9, 16)) -> Path:
    """Render [start, end] as an MP4, centre-cropped to the aspect ratio.

    subtitles is an ASS file, burned in by libass. It is referenced by filename
    and ffmpeg runs from its folder, which avoids path-escaping problems.
    """
    rw, rh = ratio
    r = rw / rh
    filters = [
        f"crop=w='min(iw,ih*{r:.6f})':h='min(ih,iw/{r:.6f})'",
        f"scale={width}:{height}:flags=lanczos",
        f"fps={fps}",
    ]
    if subtitles is not None:
        filters.append(f"subtitles={subtitles.name}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src.resolve()),
           "-t", f"{end - start:.3f}", "-vf", ",".join(filters),
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out.name]
    _run(cmd, cwd=str(out.parent.resolve()))
    return out


def export_mp3(src: Path, out: Path, start: float, end: float) -> Path:
    """Audio-only MP3 for the same time range."""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src.resolve()),
          "-t", f"{end - start:.3f}", "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(out.resolve())])
    return out
