"""HTTP API and web UI.

Easiest start: python run.py
Or directly:  uvicorn clipper.server:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from . import config, llm, presets
from .collections_store import CollectionError, CollectionStore, LABEL_COLORS, NotFound, STATUSES
from .jobs import JobStore
from .pipeline import Params
from .variants import ASPECT_CHOICES

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)


@app.on_event("startup")
def _preload_speech_model() -> None:
    """Load the speech model in the background, so the first job starts at once."""
    import threading
    from .transcribe import warm_up
    threading.Thread(target=warm_up, name="whisper-warmup", daemon=True).start()
store = JobStore(config.JOBS_DIR)
collections = CollectionStore(config.COLLECTIONS_FILE)

ALLOWED_UPLOAD_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".mp3", ".wav", ".m4a"}


class JobRequest(BaseModel):
    url: str | None = None
    count: int = Field(5, ge=1, le=20)
    min_seconds: float = Field(config.MIN_CLIP_SECONDS, ge=5, le=600)
    max_seconds: float = Field(config.MAX_CLIP_SECONDS, ge=10, le=600)
    platform: str = "tiktok"
    quality: str = "quick"
    quality_mode: str = "default"
    resolution: int = 1080
    fps_mode: str = "default"
    fps: int = 30
    aspect_mode: str = "default"
    aspect: str = "9:16"
    captions: bool = False
    caption_style: str = "highlight"
    caption_position: str = "bottom"
    caption_uppercase: bool = False
    caption_color: str = "yellow"
    use_llm: bool = True

    @field_validator("url")
    @classmethod
    def _check_url(cls, v):
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not re.match(r"^https?://", v):
            raise ValueError("URL must start with http:// or https://")
        return v

    @model_validator(mode="after")
    def _check_range(self):
        if self.max_seconds <= self.min_seconds:
            raise ValueError("max_seconds must be greater than min_seconds")
        return self


def _params(req: JobRequest) -> Params:
    params = Params(count=req.count, min_seconds=req.min_seconds, max_seconds=req.max_seconds,
                    platform=req.platform, quality=req.quality, captions=req.captions,
                    quality_mode=req.quality_mode, resolution=req.resolution,
                    fps_mode=req.fps_mode, fps=req.fps,
                    aspect_mode=req.aspect_mode, aspect=req.aspect,
                    caption_style=req.caption_style, caption_position=req.caption_position,
                    caption_uppercase=req.caption_uppercase, caption_color=req.caption_color,
                    use_llm=req.use_llm)
    try:
        presets.validate(params)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return params


class CollectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    status: str = "Draft"
    label: str = Field("", max_length=30)
    label_color: str | None = None
    notes: str = Field("", max_length=4000)


class CollectionPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    status: str | None = None
    label: str | None = Field(None, max_length=30)
    label_color: str | None = None
    notes: str | None = Field(None, max_length=4000)


class EntryAdd(BaseModel):
    job_id: str
    clip_id: str


class EntryPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    status: str | None = None
    note: str | None = Field(None, max_length=1000)
    label: str | None = Field(None, max_length=30)
    label_color: str | None = None


class EntryMove(BaseModel):
    direction: str | None = None
    to_index: int | None = None


def _collection_error(exc: Exception):
    if isinstance(exc, NotFound):
        raise HTTPException(404, str(exc))
    raise HTTPException(400, str(exc))


# --- config and projects -------------------------------------------------------

@app.get("/api/config")
def get_config():
    return {
        "app_name": config.APP_NAME,
        "version": config.APP_VERSION,
        "llm_enabled": llm.llm_enabled(),
        "llm_model": config.LLM_MODEL if llm.llm_enabled() else None,
        "whisper_model": config.WHISPER_MODEL,
        "min_seconds": config.MIN_CLIP_SECONDS,
        "max_seconds": config.MAX_CLIP_SECONDS,
        "platforms": [{"id": k, **{x: v[x] for x in ("label", "hint")}} for k, v in presets.PLATFORMS.items()],
        "resolutions": [{"id": k, "label": v["label"]} for k, v in presets.RESOLUTIONS.items()],
        "fps_options": list(presets.FPS_OPTIONS),
        "aspect_presets": list(presets.ASPECT_PRESETS),
        "aspect_default": presets.DEFAULT_ASPECT,
        "aspect_choices": ASPECT_CHOICES,
        "qualities": [{"id": k, **{x: v[x] for x in ("label", "hint", "fps")}} for k, v in presets.QUALITY.items()],
        "caption_styles": [{"id": k, **v} for k, v in presets.CAPTION_STYLES.items()],
        "caption_positions": list(presets.CAPTION_POSITIONS),
        "caption_colors": [{"id": k, "hex": v} for k, v in presets.ACCENT_COLORS.items()],
        "collection_statuses": list(STATUSES),
        "label_colors": list(LABEL_COLORS),
    }


@app.get("/api/jobs")
def list_jobs():
    return store.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


class VariantRequest(BaseModel):
    aspect: str = "9:16"


@app.post("/api/jobs/{job_id}/variants", status_code=202)
def create_variant(job_id: str, req: VariantRequest):
    """Re-cut a finished project's clips in another aspect ratio."""
    try:
        return store.add_variant(job_id, req.aspect)
    except KeyError:
        raise HTTPException(404, "project not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        found = store.delete(job_id)
    except RuntimeError:
        raise HTTPException(409, "This project is still processing. Delete it once it finishes.")
    if not found:
        raise HTTPException(404, "job not found")
    collections.remove_job(job_id)  # cascade: no collection keeps clips from a deleted project
    return Response(status_code=204)


@app.get("/api/clips")
def list_clips():
    """Every finished clip across projects, for the 'add to collection' picker."""
    out = []
    for job in store.list_jobs():
        if job["status"] != "done":
            continue
        full = store.get(job["id"]) or {}
        for clip in full.get("clips", []):
            files = clip.get("files", {})
            out.append({"job_id": job["id"], "clip_id": clip["id"], "title": clip["title"],
                        "source_title": full.get("source_title"), "watch_rate": clip.get("watch_rate"),
                        "rating": clip.get("rating"), "duration": clip.get("duration"),
                        "platform": clip.get("platform"), "quality": clip.get("quality"), "output": clip.get("output"),
                        "reason": clip.get("reason"), "created_at": job.get("created_at"),
                        "video": files.get("video"), "thumbnail": files.get("thumbnail"),
                        "audio": files.get("audio"), "captions": files.get("captions")})
    return out


@app.post("/api/jobs", status_code=202)
def create_job(req: JobRequest):
    if not req.url:
        raise HTTPException(400, "provide a video URL, or use the upload endpoint")
    params = _params(req)
    job = store.create(params, source_url=req.url, upload_name=None)
    store.submit(job["id"], params, upload=None)
    return job


@app.post("/api/jobs/upload", status_code=202)
async def create_job_from_upload(
    file: UploadFile = File(...),
    count: int = Form(5),
    min_seconds: float = Form(config.MIN_CLIP_SECONDS),
    max_seconds: float = Form(config.MAX_CLIP_SECONDS),
    platform: str = Form("tiktok"),
    quality: str = Form("quick"),
    quality_mode: str = Form("default"),
    resolution: int = Form(1080),
    fps_mode: str = Form("default"),
    fps: int = Form(30),
    aspect_mode: str = Form("default"),
    aspect: str = Form("9:16"),
    captions: bool = Form(False),
    caption_style: str = Form("highlight"),
    caption_position: str = Form("bottom"),
    caption_uppercase: bool = Form(False),
    caption_color: str = Form("yellow"),
    use_llm: bool = Form(True),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(400, f"unsupported file type: {ext or 'none'}")
    try:
        req = JobRequest(count=count, min_seconds=min_seconds, max_seconds=max_seconds,
                         platform=platform, quality=quality, captions=captions,
                         quality_mode=quality_mode, resolution=resolution,
                         fps_mode=fps_mode, fps=fps,
                         aspect_mode=aspect_mode, aspect=aspect,
                         caption_style=caption_style, caption_position=caption_position,
                         caption_uppercase=caption_uppercase, caption_color=caption_color,
                         use_llm=use_llm)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    params = _params(req)
    job = store.create(params, source_url=None, upload_name=file.filename)
    dest = store.job_dir(job["id"]) / f"upload{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)
    store.submit(job["id"], params, upload=dest)
    return job


# --- collections ---------------------------------------------------------------

@app.get("/api/collections")
def list_collections():
    return collections.list()


@app.post("/api/collections", status_code=201)
def create_collection(body: CollectionCreate):
    try:
        return collections.create(body.name, status=body.status, label=body.label,
                                  label_color=body.label_color, notes=body.notes)
    except CollectionError as exc:
        _collection_error(exc)


@app.get("/api/collections/{cid}")
def get_collection(cid: str):
    try:
        return collections.get(cid)
    except NotFound as exc:
        _collection_error(exc)


@app.patch("/api/collections/{cid}")
def patch_collection(cid: str, body: CollectionPatch):
    try:
        return collections.update(cid, body.model_dump(exclude_unset=True))
    except (NotFound, CollectionError) as exc:
        _collection_error(exc)


@app.delete("/api/collections/{cid}", status_code=204)
def delete_collection(cid: str):
    try:
        collections.delete(cid)
    except NotFound as exc:
        _collection_error(exc)
    return Response(status_code=204)


@app.post("/api/collections/{cid}/entries", status_code=201)
def add_entry(cid: str, body: EntryAdd):
    job = store.get(body.job_id)
    if not job:
        raise HTTPException(404, "project not found")
    clip = next((c for c in job.get("clips", []) if c["id"] == body.clip_id), None)
    if clip is None:
        raise HTTPException(404, "clip not found")
    snapshot = {
        "job_id": body.job_id,
        "clip_id": body.clip_id,
        "title": clip["title"],
        "source_title": job.get("source_title"),
        "start": clip["start"],
        "end": clip["end"],
        "duration": clip["duration"],
        "watch_rate": clip.get("watch_rate"),
        "watch_rate_source": clip.get("watch_rate_source"),
        "rating": clip.get("rating"),
        "platform": clip.get("platform"),
        "quality": clip.get("quality"), "output": clip.get("output"),
        "video": clip["files"].get("video"),
        "thumbnail": clip["files"].get("thumbnail"),
        "audio": clip["files"].get("audio"),
        "captions": clip["files"].get("captions"),
    }
    try:
        return collections.add_entry(cid, snapshot)
    except (NotFound, CollectionError) as exc:
        _collection_error(exc)


@app.patch("/api/collections/{cid}/entries/{eid}")
def patch_entry(cid: str, eid: str, body: EntryPatch):
    try:
        return collections.update_entry(cid, eid, body.model_dump(exclude_unset=True))
    except (NotFound, CollectionError) as exc:
        _collection_error(exc)


@app.delete("/api/collections/{cid}/entries/{eid}", status_code=204)
def delete_entry(cid: str, eid: str):
    try:
        collections.remove_entry(cid, eid)
    except NotFound as exc:
        _collection_error(exc)
    return Response(status_code=204)


@app.post("/api/collections/{cid}/entries/{eid}/move", status_code=204)
def move_entry(cid: str, eid: str, body: EntryMove):
    try:
        collections.move_entry(cid, eid, direction=body.direction, to_index=body.to_index)
    except (NotFound, CollectionError) as exc:
        _collection_error(exc)
    return Response(status_code=204)


# Static assets (CSS/JS), clip files and thumbnails served from the job folders
config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=config.JOBS_DIR), name="media")


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
