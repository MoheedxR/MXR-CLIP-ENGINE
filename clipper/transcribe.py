"""Local speech-to-text with faster-whisper, grouped into sentence-like units."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from . import config
from .media import read_wav_float32
from .models import Segment

_model = None
_model_lock = threading.Lock()

# A unit is cut at sentence punctuation, at a pause, or when it gets long.
PAUSE_BREAK = 0.6      # seconds of silence that end a unit
MAX_UNIT = 12.0        # soft cap on unit length, seconds


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(
                config.WHISPER_MODEL,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE_TYPE,
            )
        return _model


def is_model_loaded() -> bool:
    return _model is not None


def group_words(words: list[tuple[float, float, str]]) -> list[Segment]:
    """Turn (start, end, token) words into sentence-like Segments.

    Pure function so it can be unit-tested without Whisper.
    """
    units: list[Segment] = []
    buf: list[tuple[float, float, str]] = []

    def flush():
        if buf:
            text = "".join(w[2] for w in buf).strip()
            if text:
                units.append(Segment(start=buf[0][0], end=buf[-1][1], text=text))
            buf.clear()

    for s, e, tok in words:
        if buf:
            gap = s - buf[-1][1]
            span = e - buf[0][0]
            if gap >= PAUSE_BREAK or span >= MAX_UNIT:
                flush()
        buf.append((s, e, tok))
        if tok.strip().endswith((".", "?", "!", "…")):
            flush()
    flush()
    return units


def transcribe(audio_path: Path, duration: float,
               on_progress: Callable[[float], None] | None = None) -> list[Segment]:
    model = _get_model()
    # Pass samples rather than a path: avoids depending on PyAV's file decoder API.
    audio = read_wav_float32(audio_path)
    raw_segments, _info = model.transcribe(
        audio,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )
    words: list[tuple[float, float, str]] = []
    for seg in raw_segments:
        for w in seg.words or []:
            words.append((float(w.start), float(w.end), w.word))
        if on_progress and duration > 0:
            on_progress(min(1.0, seg.end / duration))
    return group_words(words)
