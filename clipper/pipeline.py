"""End-to-end: source video -> ranked, cut, captioned short clips.

Progress and activity are reported through a Reporter, so the same pipeline
works from the web UI, a test, or a script.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, llm
from .candidates import build_candidates
from .download import download
from .media import (MediaError, cut_clip, energy_profile, extract_audio, extract_thumbnail,
                    normalise_energy, probe_duration, write_srt)
from .models import Candidate, Segment
from .scoring import score_candidate, select_diverse
from .transcribe import transcribe

log = logging.getLogger(__name__)


@dataclass
class Params:
    count: int = 5
    min_seconds: float = config.MIN_CLIP_SECONDS
    max_seconds: float = config.MAX_CLIP_SECONDS
    vertical: bool = True
    captions: bool = False
    use_llm: bool = True


class Reporter:
    """Receives progress from the pipeline. Subclass to route it somewhere."""

    def stage(self, stage: str, progress: float, message: str) -> None: ...
    def log(self, message: str, level: str = "info") -> None: ...
    def planned(self, count: int) -> None: ...
    def clip(self, record: dict) -> None: ...


class NullReporter(Reporter):
    pass


def _fmt(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run_pipeline(job_dir: Path, params: Params, *, source_url: str | None = None,
                 upload: Path | None = None, upload_name: str | None = None,
                 reporter: Reporter | None = None) -> dict:
    """Run the full pipeline inside job_dir and return a result dict."""
    rep = reporter or NullReporter()
    warnings: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)
        rep.log(msg, "warn")

    job_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    # 1. Source video -------------------------------------------------------
    if source_url:
        rep.stage("downloading", 0.0, "Connecting to the video source")
        rep.log(f"Fetching {source_url}")

        def dl_progress(frac: float, text: str) -> None:
            rep.stage("downloading", 0.15 * frac, text)

        src, title = download(source_url, job_dir, on_progress=dl_progress)
        rep.log(f'Downloaded "{title}"', "success")
    else:
        rep.stage("downloading", 0.05, "Preparing your file")
        title = Path(upload_name).stem if upload_name else (upload.stem if upload else "Untitled")
        src = job_dir / ("source" + upload.suffix.lower())
        if upload.resolve() != src.resolve():
            shutil.move(str(upload), src)
        rep.log(f'Received "{upload_name or upload.name}"', "success")

    duration = probe_duration(src)
    rep.log(f"Source length {_fmt(duration)}")

    # 2. Audio and loudness -------------------------------------------------
    rep.stage("audio", 0.16, "Extracting audio track")
    wav = extract_audio(src, job_dir / "audio.wav")
    energy_z = normalise_energy(energy_profile(wav))
    rep.log("Extracted mono 16 kHz audio and mapped the loudness curve")

    # 3. Transcript (cached so re-runs are cheap) ---------------------------
    transcript_path = job_dir / "transcript.json"
    if transcript_path.exists():
        units = [Segment(**u) for u in json.loads(transcript_path.read_text())]
        rep.log("Reusing the transcript from a previous run")
    else:
        rep.stage("transcribing", 0.18, "Loading speech model (first run downloads it)")
        rep.log(f"Transcribing speech with Whisper '{config.WHISPER_MODEL}'")

        def t_progress(frac: float) -> None:
            pct = int(frac * 100)
            rep.stage("transcribing", 0.18 + 0.37 * frac, f"Transcribing speech · {pct}%")

        units = transcribe(wav, duration, on_progress=t_progress)
        transcript_path.write_text(json.dumps([asdict(u) for u in units], ensure_ascii=False))
        rep.log(f"Transcribed {len(units):,} sentences", "success")
    if not units:
        raise MediaError("No speech was detected in this video, so no clips can be made.")

    # 4. Candidate windows + local scoring ----------------------------------
    rep.stage("scoring", 0.55, "Searching for strong moments")
    cands = build_candidates(units, params.min_seconds, params.max_seconds)
    if not cands:
        raise MediaError("The video is too short for the requested clip length range.")
    rep.log(f"Built {len(cands):,} candidate clips, {int(params.min_seconds)}–{int(params.max_seconds)} s, "
            "each starting and ending on whole sentences")
    for c in cands:
        score_candidate(c, units, energy_z)
    ranked = sorted(cands, key=lambda c: c.heuristic, reverse=True)
    rep.log(f"Scored every candidate on hook, energy, pace, completeness and length")

    # 5. Shortlist, then optional LLM re-rank -------------------------------
    shortlist = select_diverse(ranked, max(params.count * 3, 12))
    rep.log(f"Shortlisted {len(shortlist)} moments that don't overlap", "success")
    for c in shortlist[:3]:
        snippet = " ".join(c.text.split())[:90]
        rep.log(f'{c.heuristic:4.1f}  {_fmt(c.start)}  "{snippet}…"', "detail")

    if params.use_llm and llm.llm_enabled():
        rep.stage("ranking", 0.62, "AI is reviewing the shortlist")
        rep.log(f"Sending {len(shortlist)} moments to {config.LLM_MODEL} for hook and clarity review")
        try:
            llm.rerank(shortlist)
            rep.log("AI review complete. Titles and reasons added", "success")
        except Exception as exc:  # network / auth / bad JSON: fall back gracefully
            log.exception("LLM re-rank failed")
            warn(f"AI review failed, using local scores only: {exc}")
            for c in shortlist:
                c.llm = None
    elif params.use_llm:
        rep.log("AI ranking is off (no API key set). Using local scores")

    for c in shortlist:
        c.final = llm.combined_score(c)
    final = select_diverse(sorted(shortlist, key=lambda c: c.final, reverse=True), params.count)
    if len(final) < params.count:
        warn(f"Found {len(final)} strong moment{'s' if len(final) != 1 else ''} in this video, "
             f"fewer than the {params.count} you asked for. Longer videos usually yield more.")
    rep.log(f"Selected {len(final)} clip{'s' if len(final) != 1 else ''} to render")
    rep.planned(len(final))

    # 6. Cut clips, one at a time, so each appears as soon as it's ready -----
    clips: list[dict] = []
    n = max(len(final), 1)
    for rank, c in enumerate(final, start=1):
        rep.stage("clipping", 0.7 + 0.3 * (rank - 1) / n, f"Rendering clip {rank} of {len(final)}")
        rep.log(f"Rendering clip {rank}: {_fmt(c.start)} → {_fmt(c.end)} ({c.duration:.0f} s)")
        stem = f"clip_{rank:02d}"
        out_mp4 = clips_dir / f"{stem}.mp4"
        srt = None
        if params.captions:
            srt = write_srt(units, c.start, c.end, clips_dir / f"{stem}.srt")
        try:
            cut_clip(src, out_mp4, c.start, c.end, vertical=params.vertical, subtitles=srt)
        except MediaError as exc:
            if srt is None:
                raise
            warn(f"{stem}: captions could not be burned in, exported without them")
            cut_clip(src, out_mp4, c.start, c.end, vertical=params.vertical, subtitles=None)
        thumb = extract_thumbnail(src, c.start + min(2.0, c.duration / 2), clips_dir / f"{stem}.jpg")
        record = _clip_record(rank, stem, c, out_mp4, srt, thumb, units, job_dir)
        clips.append(record)
        rep.clip(record)
        rep.log(f"Clip {rank} ready · score {record['scores']['final']:.0f} · \"{record['title']}\"", "success")

    rep.stage("done", 1.0, "All clips are ready")
    rep.log("Finished. Preview and download your clips below", "success")
    return {
        "source_title": title,
        "source_duration": round(duration, 1),
        "clips": clips,
        "warnings": warnings,
        "llm_used": any(c.llm for c in final),
    }


def _clip_record(rank: int, stem: str, c: Candidate, mp4: Path, srt: Path | None,
                 thumb: Path, units: list[Segment], job_dir: Path) -> dict:
    jobs_root = job_dir.parent

    def rel(p: Path | None) -> str | None:
        return str(p.relative_to(jobs_root)).replace("\\", "/") if p else None

    first_units = [u.text for u in units[c.segment_range[0]:c.segment_range[1] + 1]][:2]
    title = (c.llm or {}).get("title") or " ".join(" ".join(first_units).split()[:8])
    return {
        "rank": rank,
        "id": stem,
        "title": title,
        "reason": (c.llm or {}).get("reason", ""),
        "start": round(c.start, 2),
        "end": round(c.end, 2),
        "duration": round(c.duration, 2),
        "transcript": c.text,
        "scores": {
            "final": round(c.final, 1),
            "heuristic": round(c.heuristic, 1),
            "llm": c.llm,
            "features": c.features,
        },
        "files": {
            "video": rel(mp4),
            "thumbnail": rel(thumb),
            "captions": rel(srt),
        },
    }
