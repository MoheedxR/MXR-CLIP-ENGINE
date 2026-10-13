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

from . import config, llm
from .jobs import JobStore
from .pipeline import Params

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)
store = JobStore(config.JOBS_DIR)

ALLOWED_UPLOAD_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".mp3", ".wav", ".m4a"}


class JobRequest(BaseModel):
    url: str | None = None
    count: int = Field(5, ge=1, le=20)
    min_seconds: float = Field(config.MIN_CLIP_SECONDS, ge=5, le=600)
    max_seconds: float = Field(config.MAX_CLIP_SECONDS, ge=10, le=600)
    vertical: bool = True
    captions: bool = False
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
    return Params(count=req.count, min_seconds=req.min_seconds, max_seconds=req.max_seconds,
                  vertical=req.vertical, captions=req.captions, use_llm=req.use_llm)


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


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        found = store.delete(job_id)
    except RuntimeError:
        raise HTTPException(409, "This project is still processing. Delete it once it finishes.")
    if not found:
        raise HTTPException(404, "job not found")
    return Response(status_code=204)


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
    vertical: bool = Form(True),
    captions: bool = Form(False),
    use_llm: bool = Form(True),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(400, f"unsupported file type: {ext or 'none'}")
    try:
        req = JobRequest(count=count, min_seconds=min_seconds, max_seconds=max_seconds,
                         vertical=vertical, captions=captions, use_llm=use_llm)
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


# Static assets (CSS/JS), clip files and thumbnails served from the job folders
config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=config.JOBS_DIR), name="media")


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
