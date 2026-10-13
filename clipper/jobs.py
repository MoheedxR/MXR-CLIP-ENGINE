"""Background job runner with JSON persistence, so projects survive a restart."""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .pipeline import Params, Reporter, run_pipeline
from .fsutil import atomic_write_text
from .presets import parse_aspect
from .variants import render_variant, source_ready

log = logging.getLogger(__name__)

MAX_LOG_ENTRIES = 400


class JobStore:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        # One heavy job at a time: transcription and encoding are CPU-bound.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-job")
        self._load_existing()

    # --- persistence -------------------------------------------------------
    def _load_existing(self) -> None:
        for path in self.jobs_dir.glob("*/job.json"):
            try:
                job = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if job.get("status") in {"queued", "running"}:
                job["status"] = "failed"
                job["error"] = "Interrupted by a restart"
                job.setdefault("log", []).append(
                    {"t": time.time(), "level": "error", "msg": "Stopped because the app was restarted"})
            self._jobs[job["id"]] = job
            # Keep saved state consistent with the in-memory correction above.
            self._save(job)

    def _save(self, job: dict) -> None:
        d = self.jobs_dir / job["id"]
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_text(d / "job.json", json.dumps(job, indent=2, ensure_ascii=False))

    # --- API used by the server -------------------------------------------
    def create(self, params: Params, source_url: str | None, upload_name: str | None) -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "message": "Waiting for the engine",
            "created_at": time.time(),
            "source_url": source_url,
            "upload_name": upload_name,
            "params": vars(params),
            "source_title": None,
            "planned_clips": 0,
            "clips": [],
            "warnings": [],
            "error": None,
            "log": [{"t": time.time(), "level": "info", "msg": "Project created and queued"}],
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        return job

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def submit(self, job_id: str, params: Params, upload: Path | None) -> None:
        self._pool.submit(self._run, job_id, params, upload)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            out = json.loads(json.dumps(job))
        out.setdefault("variants", [])
        out["source_ready"] = source_ready(self.job_dir(job_id))
        return out

    def list_jobs(self) -> list[dict]:
        with self._lock:
            items = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
            return [{k: j.get(k) for k in ("id", "status", "stage", "progress", "message",
                                           "source_title", "source_url", "upload_name",
                                           "created_at", "planned_clips", "params")}
                    | {"clip_count": len(j.get("clips") or [])}
                    for j in items]

    def delete(self, job_id: str) -> bool:
        """Remove a finished job and its files. Returns False if unknown.

        Raises RuntimeError while the job is still queued or running.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job["status"] in {"queued", "running"}:
                raise RuntimeError("job is still processing")
            del self._jobs[job_id]
        shutil.rmtree(self.jobs_dir / job_id, ignore_errors=True)
        return True

    # --- reformatting a finished project ------------------------------------
    def add_variant(self, job_id: str, aspect: str) -> dict:
        """Queue the same clips in a new aspect ratio. Returns the new variant record.

        Raises KeyError if the job is unknown, ValueError for a bad or unavailable request.
        """
        w, h = parse_aspect(aspect)                       # ValueError for a bad ratio
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "done":
                raise ValueError("Finish the clips first, then change the format.")
            if not job.get("clips"):
                raise ValueError("This project has no clips to change.")
            if not source_ready(self.job_dir(job_id)):
                raise ValueError("The downloaded video is no longer in this project folder.")
            variant = {
                "id": uuid.uuid4().hex[:8],
                "aspect": f"{w}:{h}",
                "status": "queued",
                "message": "Waiting to start",
                "created_at": time.time(),
                "clips": [],
                "error": None,
            }
            job.setdefault("variants", []).append(variant)
            self._save(job)
        self._pool.submit(self._run_variant, job_id, variant["id"], f"{w}:{h}")
        return variant

    def _variant_update(self, job_id: str, variant_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for v in job.setdefault("variants", []):
                if v["id"] == variant_id:
                    v.update(fields)
            self._save(job)

    def _run_variant(self, job_id: str, variant_id: str, aspect: str) -> None:
        self._variant_update(job_id, variant_id, status="running", message=f"Cutting clips at {aspect}")
        job = self.get(job_id)
        params = Params(**{k: v for k, v in job["params"].items() if k in Params.__dataclass_fields__})

        def on_clip(record: dict) -> None:
            with self._lock:
                j = self._jobs[job_id]
                for v in j.setdefault("variants", []):
                    if v["id"] == variant_id:
                        v["clips"].append(record)
                self._save(j)

        try:
            render_variant(self.job_dir(job_id), job["clips"], params, variant_id, aspect, on_clip)
            self._variant_update(job_id, variant_id, status="done", message="Ready")
        except Exception as exc:  # show the failure on the variant, keep the project usable
            log.exception("variant %s of job %s failed", variant_id, job_id)
            self._variant_update(job_id, variant_id, status="failed", message="Could not change the format",
                                 error=str(exc))

    # --- mutations used by the worker -------------------------------------
    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(fields)
            self._save(job)

    def _append_log(self, job_id: str, level: str, msg: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            entries = job.setdefault("log", [])
            entries.append({"t": time.time(), "level": level, "msg": msg})
            if len(entries) > MAX_LOG_ENTRIES:
                del entries[: len(entries) - MAX_LOG_ENTRIES]
            self._save(job)

    def _add_clip(self, job_id: str, record: dict) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["clips"].append(record)
            self._save(job)

    def _run(self, job_id: str, params: Params, upload: Path | None) -> None:
        job = self.get(job_id)
        self._update(job_id, status="running", message="Starting")
        reporter = _JobReporter(self, job_id)
        try:
            result = run_pipeline(
                self.job_dir(job_id),
                params,
                source_url=job["source_url"],
                upload=upload,
                upload_name=job.get("upload_name"),
                reporter=reporter,
            )
            self._update(
                job_id,
                status="done",
                stage="done",
                progress=1.0,
                finished_at=time.time(),
                message=f"{len(result['clips'])} clips ready",
                source_title=result["source_title"],
                source_duration=result["source_duration"],
                clips=result["clips"],
                warnings=result["warnings"],
                llm_used=result["llm_used"],
                source_info=result.get("source_info"),
            )
        except Exception as exc:  # surface any failure to the UI
            log.exception("job %s failed", job_id)
            self._append_log(job_id, "error", f"Stopped: {exc}")
            self._update(job_id, status="failed", message="Something went wrong", error=str(exc),
                         finished_at=time.time())


class _JobReporter(Reporter):
    """Routes pipeline progress into the job record that the UI polls."""

    def __init__(self, store: JobStore, job_id: str):
        self.store = store
        self.job_id = job_id

    def stage(self, stage: str, progress: float, message: str) -> None:
        self.store._update(self.job_id, stage=stage, progress=round(progress, 4), message=message)

    def log(self, message: str, level: str = "info") -> None:
        self.store._append_log(self.job_id, level, message)

    def planned(self, count: int) -> None:
        self.store._update(self.job_id, planned_clips=count)

    def clip(self, record: dict) -> None:
        self.store._add_clip(self.job_id, record)
