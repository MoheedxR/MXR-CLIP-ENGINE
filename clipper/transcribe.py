"""Local speech-to-text with faster-whisper, grouped into sentence-like units."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from . import config
from .media import read_wav_float32
from .models import Segment

log = logging.getLogger(__name__)

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
                cpu_threads=config.WHISPER_THREADS,
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
                words = [(float(s), float(e), t.strip()) for s, e, t in buf if t.strip()]
                units.append(Segment(start=buf[0][0], end=buf[-1][1], text=text, words=words))
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


def warm_up() -> None:
    """Load the speech model in the background, so the first job doesn't wait for it."""
    try:
        _get_model()
    except Exception:  # a failed warm-up is retried, with its error, when a job needs it
        log.exception("Speech model warm-up failed")


def _collect(segments, duration: float, on_progress) -> list[tuple[float, float, str]]:
    words: list[tuple[float, float, str]] = []
    for seg in segments:
        for w in seg.words or []:
            words.append((float(w.start), float(w.end), w.word))
        if on_progress and duration > 0:
            on_progress(min(1.0, seg.end / duration))
    return words


def transcribe(audio_path: Path, duration: float,
               on_progress: Callable[[float], None] | None = None) -> list[Segment]:
    model = _get_model()
    # Pass samples rather than a path: avoids depending on PyAV's file decoder API.
    audio = read_wav_float32(audio_path)
    words: list[tuple[float, float, str]] | None = None
    if config.WHISPER_BATCH_SIZE > 1:
        try:
            from faster_whisper import BatchedInferencePipeline
            batched = BatchedInferencePipeline(model=model)
            segments, _info = batched.transcribe(
                audio,
                vad_filter=True,
                beam_size=config.WHISPER_BEAM_SIZE,
                batch_size=config.WHISPER_BATCH_SIZE,
                word_timestamps=True,
            )
            words = _collect(segments, duration, on_progress)
        except Exception:  # older faster-whisper or an unexpected error: use the simple decoder
            log.exception("Batched transcription failed, using the sequential decoder")
            words = None
    if words is None:
        segments, _info = model.transcribe(
            audio,
            vad_filter=True,
            beam_size=config.WHISPER_BEAM_SIZE,
            word_timestamps=True,
        )
        words = _collect(segments, duration, on_progress)
    return group_words(words)
