"""End-to-end: source video -> ranked, cut, captioned short clips.

Progress and activity are reported through a Reporter, so the same pipeline
works from the web UI, a test, or a script.
"""
from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, llm
from .candidates import build_candidates
from .captions import build_ass
from .download import download, download_audio
from .media import (MediaError, cut_clip, energy_profile, export_mp3, extract_audio,
                    extract_thumbnail, normalise_energy, probe_duration, probe_video)
from .models import Candidate, Segment
from .presets import DOWNLOAD_LIMITS, PLATFORMS, rating_label, resolve_output, validate
from .scoring import score_candidate, select_diverse
from .transcribe import transcribe

log = logging.getLogger(__name__)


@dataclass
class Params:
    count: int = 5
    min_seconds: float = config.MIN_CLIP_SECONDS
    max_seconds: float = config.MAX_CLIP_SECONDS
    platform: str = "tiktok"            # tiktok | shorts | reels
    quality: str = "quick"              # quick | 4k30 | fps60 | fps120
    quality_mode: str = "default"       # default | custom (custom uses resolution)
    resolution: int = 1080              # short side in px, when quality_mode is custom
    fps_mode: str = "default"           # default | custom (custom uses fps)
    fps: int = 30                       # when fps_mode is custom
    aspect_mode: str = "default"        # default | custom (custom uses aspect)
    aspect: str = "9:16"                # "W:H", when aspect_mode is custom
    captions: bool = False
    caption_style: str = "highlight"    # see presets.CAPTION_STYLES
    caption_position: str = "bottom"    # top | middle | bottom
    caption_uppercase: bool = False
    caption_color: str = "yellow"       # see presets.ACCENT_COLORS
    use_llm: bool = True
    download_limit: str = "full"       # full | medium | gentle, see presets.DOWNLOAD_LIMITS


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


def watch_rate_for(c: Candidate) -> tuple[float, str]:
    """Estimated watch rate (0-100) and where it came from.

    'ai' is the model's guess when the LLM review ran. 'local' falls back to the
    heuristic score. Neither is measured retention.
    """
    if c.llm and c.llm.get("watch_rate") is not None:
        return round(float(c.llm["watch_rate"]), 1), "ai"
    return round(c.heuristic, 1), "local"


def _quality_warnings(src_info: dict, spec: dict) -> list[str]:
    msgs: list[str] = []
    src_h, src_w = src_info["height"], src_info["width"]
    aw, ah = spec["aspect"]
    crop_w = min(src_w, src_h * aw / ah)
    if crop_w < spec["width"]:
        msgs.append(f"The source is {src_w}×{src_h}, so the {aw}:{ah} crop is about {int(crop_w)} px wide. "
                    f"It will be upscaled to {spec['width']} px, which softens the picture.")
    if spec["short"] >= 2160 and min(src_w, src_h) < 2000:
        msgs.append("The source is below 4K. The 4K output is upscaled, so it adds size, not detail.")
    fps_wanted = spec["fps"]
    if fps_wanted > 30 and src_info["fps"] < fps_wanted * 0.8:
        msgs.append(f"The source runs at {src_info['fps']:g} fps. A {fps_wanted} fps export repeats frames, "
                    "so motion will not be smoother than the source.")
    return msgs


def run_pipeline(job_dir: Path, params: Params, *, source_url: str | None = None,
                 upload: Path | None = None, upload_name: str | None = None,
                 reporter: Reporter | None = None) -> dict:
    """Run the full pipeline inside job_dir and return a result dict."""
    validate(params)
    rep = reporter or NullReporter()
    warnings: list[str] = []
    spec = resolve_output(params)
    platform = PLATFORMS[params.platform]
    rate = DOWNLOAD_LIMITS[params.download_limit]["bytes_per_sec"]

    def warn(msg: str) -> None:
        warnings.append(msg)
        rep.log(msg, "warn")

    job_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    # 1. Get the audio first (small and quick), so transcription can start early ----
    src: Path | None = None          # the full video, once it is on disk
    audio_src: Path | None = None    # any file with the speech track
    title = "Untitled"
    pending_video = None             # Future for the full video, while it downloads
    video_pool = None
    if source_url:
        rep.stage("downloading", 0.0, "Connecting to the video source")
        rep.log(f"Fetching the audio from {source_url} first, so speech recognition can start sooner")
        try:
            audio_src, title = download_audio(source_url, job_dir / "audio_only", rate_limit=rate)
            rep.log(f'Got the audio for "{title}"', "success")
        except Exception as exc:  # fall back to the full download below
            log.warning("Audio-only fetch failed, using the full download: %s", exc)
            rep.log("Audio-only fetch failed, so the full video is downloaded first", "warn")
        if audio_src is not None:
            video_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-video")
            pending_video = video_pool.submit(download, source_url, job_dir, None,
                                              spec["max_source"], spec["fps"] > 30, rate)
            rep.log("Downloading the full video in the background while speech is recognised")
        else:
            def dl_progress(frac: float, text: str) -> None:
                rep.stage("downloading", 0.15 * frac, text)

            src, title = download(source_url, job_dir, on_progress=dl_progress,
                                  max_height=spec["max_source"], prefer_fps=spec["fps"] > 30,
                                  rate_limit=rate)
            rep.log(f'Downloaded "{title}"', "success")
            audio_src = src
    else:
        rep.stage("downloading", 0.05, "Preparing your file")
        title = Path(upload_name).stem if upload_name else (upload.stem if upload else "Untitled")
        src = job_dir / ("source" + upload.suffix.lower())
        if upload.resolve() != src.resolve():
            shutil.move(str(upload), src)
        rep.log(f'Received "{upload_name or upload.name}"', "success")
        audio_src = src

    # 2. Audio and loudness -------------------------------------------------
    rep.stage("audio", 0.16, "Extracting audio track")
    wav = extract_audio(audio_src, job_dir / "audio.wav")
    audio_duration = probe_duration(audio_src)
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

        units = transcribe(wav, audio_duration, on_progress=t_progress)
        transcript_path.write_text(json.dumps([asdict(u) for u in units], ensure_ascii=False))
        rep.log(f"Transcribed {len(units):,} sentences", "success")
    if not units:
        raise MediaError("No speech was detected in this video, so no clips can be made.")

    # 3b. Wait for the full video if it is still downloading ---------------
    if pending_video is not None:
        rep.stage("scoring", 0.55, "Finishing the video download")
        src, _ = pending_video.result()
        video_pool.shutdown(wait=False)
        rep.log("Full video is ready", "success")

    duration = probe_duration(src)
    src_info = probe_video(src)
    rep.log(f"Source is {_fmt(duration)} long, {src_info['width']}×{src_info['height']} at {src_info['fps']:g} fps")
    for msg in _quality_warnings(src_info, spec):
        warn(msg)

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
    rep.log("Scored every candidate on hook, energy, pace, completeness and length")

    # 5. Shortlist, then optional LLM re-rank -------------------------------
    ai_on = params.use_llm and llm.llm_enabled()
    pool = max(params.count * 3, 12)
    if ai_on:
        # The AI judges the moments the local scores pick, so give it a wider pool to choose from.
        pool = max(pool, config.LLM_SHORTLIST)
    shortlist = select_diverse(ranked, pool)
    rep.log(f"Shortlisted {len(shortlist)} moments that don't overlap", "success")
    for c in shortlist[:3]:
        snippet = " ".join(c.text.split())[:90]
        rep.log(f'{c.heuristic:4.1f}  {_fmt(c.start)}  "{snippet}…"', "detail")

    ai_ran = False
    if ai_on:
        rep.stage("ranking", 0.62, "AI is reviewing the shortlist")
        rep.log(f"Sending {len(shortlist)} moments to {config.LLM_MODEL} to pick the ones worth posting")
        try:
            llm.rerank(shortlist)
            ai_ran = True
            kept_n = sum(1 for c in shortlist if c.llm and c.llm.get("keep", True))
            rep.log(f"AI kept {kept_n} of {len(shortlist)} moments and added titles, reasons and watch-rate estimates",
                    "success")
        except Exception as exc:  # network / auth / bad JSON: fall back gracefully
            log.exception("LLM re-rank failed")
            warn(f"AI review failed, using local scores only: {exc}")
            for c in shortlist:
                c.llm = None
    elif params.use_llm:
        rep.log("AI ranking is off (no API key set). Using local scores")

    for c in shortlist:
        c.final = llm.combined_score(c)
    if ai_ran and not any(c.llm and c.llm.get("keep", True) for c in shortlist):
        warn("The AI rejected every moment it reviewed, so these picks use local scores only.")
        final = select_diverse(sorted(shortlist, key=lambda c: c.final, reverse=True), params.count)
    else:
        final = llm.select_final(shortlist, params.count)
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
        ass = None
        if params.captions:
            ass = clips_dir / f"{stem}.ass"
            ass.write_text(build_ass(
                units, c.start, c.end, width=spec["width"], height=spec["height"],
                style=params.caption_style, position=params.caption_position,
                uppercase=params.caption_uppercase, accent=params.caption_color,
                bottom_margin=round(platform["caption_bottom"] * spec["height"] / 1920)), encoding="utf-8")
        cut_kwargs = dict(width=spec["width"], height=spec["height"], fps=spec["fps"],
                          crf=spec["crf"], preset=spec["preset"], ratio=spec["aspect"])
        try:
            cut_clip(src, out_mp4, c.start, c.end, subtitles=ass, **cut_kwargs)
        except MediaError:
            if ass is None:
                raise
            warn(f"{stem}: captions could not be burned in, exported without them")
            cut_clip(src, out_mp4, c.start, c.end, subtitles=None, **cut_kwargs)
            ass = None
        mp3 = None
        if spec["mp3"]:
            mp3 = export_mp3(src, clips_dir / f"{stem}.mp3", c.start, c.end)
        thumb = extract_thumbnail(src, c.start + min(2.0, c.duration / 2), clips_dir / f"{stem}.jpg")
        record = _clip_record(rank, stem, c, out_mp4, ass, thumb, mp3, units, job_dir, params)
        clips.append(record)
        rep.clip(record)
        rep.log(f"Clip {rank} ready · watch rate {record['watch_rate']:.0f} ({record['rating']}) · "
                f"\"{record['title']}\"", "success")

    rep.stage("done", 1.0, "All clips are ready")
    rep.log("Finished. Preview and download your clips below", "success")
    return {
        "source_title": title,
        "source_duration": round(duration, 1),
        "source_info": src_info,
        "clips": clips,
        "warnings": warnings,
        "llm_used": any(c.llm for c in final),
    }


def _clip_record(rank: int, stem: str, c: Candidate, mp4: Path, ass: Path | None,
                 thumb: Path, mp3: Path | None, units: list[Segment], job_dir: Path,
                 params: Params) -> dict:
    jobs_root = job_dir.parent

    def rel(p: Path | None) -> str | None:
        return str(p.relative_to(jobs_root)).replace("\\", "/") if p else None

    first_units = [u.text for u in units[c.segment_range[0]:c.segment_range[1] + 1]][:2]
    title = (c.llm or {}).get("title") or " ".join(" ".join(first_units).split()[:8])
    watch, source = watch_rate_for(c)
    return {
        "rank": rank,
        "id": stem,
        "title": title,
        "reason": (c.llm or {}).get("reason", ""),
        "start": round(c.start, 2),
        "end": round(c.end, 2),
        "duration": round(c.duration, 2),
        "transcript": c.text,
        "platform": params.platform,
        "quality": params.quality,
        "output": resolve_output(params)["description"],
        "watch_rate": watch,
        "watch_rate_source": source,
        "rating": rating_label(watch),
        "scores": {
            "final": round(c.final, 1),
            "heuristic": round(c.heuristic, 1),
            "llm": c.llm,
            "features": c.features,
        },
        "files": {
            "video": rel(mp4),
            "thumbnail": rel(thumb),
            "audio": rel(mp3),
            "captions": rel(ass),
        },
    }
