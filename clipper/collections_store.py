"""Collections: user-made groups of clips, saved as one JSON file.

Each entry stores a snapshot of the clip (title, times, watch rate, file paths),
so a collection still reads correctly if the project is later renamed. When a
project is deleted, its entries are removed from every collection.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from .fsutil import atomic_write_text

STATUSES = ("Draft", "In review", "Ready", "Scheduled", "Posted")
LABEL_COLORS = ("yellow", "cyan", "pink", "lime", "violet", "grey")
MAX_NOTES = 4000
MAX_NAME = 80


class CollectionError(ValueError):
    """Bad input from the user. The server turns this into a 400."""


class NotFound(LookupError):
    pass


class CollectionStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = {"collections": []}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Keep the broken file for inspection instead of silently losing it.
                path.rename(path.with_suffix(".corrupt.json"))
        self._data.setdefault("collections", [])

    # --- persistence -------------------------------------------------------
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self._data, indent=2, ensure_ascii=False))

    def _find(self, cid: str) -> dict:
        for c in self._data["collections"]:
            if c["id"] == cid:
                return c
        raise NotFound("collection not found")

    @staticmethod
    def _find_entry(col: dict, eid: str) -> dict:
        for e in col["entries"]:
            if e["id"] == eid:
                return e
        raise NotFound("clip not found in this collection")

    @staticmethod
    def _renumber(col: dict) -> None:
        # The list order is authoritative; 'order' simply mirrors the position.
        for i, e in enumerate(col["entries"]):
            e["order"] = i

    @staticmethod
    def _clean_name(name: str | None, fallback: str | None = None) -> str:
        value = (name if name is not None else fallback or "").strip()
        if not value:
            raise CollectionError("name cannot be empty")
        if len(value) > MAX_NAME:
            raise CollectionError(f"name must be {MAX_NAME} characters or fewer")
        return value

    @staticmethod
    def _check_status(status: str) -> str:
        if status not in STATUSES:
            raise CollectionError(f"status must be one of: {', '.join(STATUSES)}")
        return status

    @staticmethod
    def _check_color(color: str | None) -> str | None:
        if color is None or color == "":
            return None
        if color not in LABEL_COLORS:
            raise CollectionError(f"label colour must be one of: {', '.join(LABEL_COLORS)}")
        return color

    # --- collections ---------------------------------------------------------
    def list(self) -> list[dict]:
        with self._lock:
            out = []
            for c in self._data["collections"]:
                out.append({**{k: c[k] for k in ("id", "name", "status", "label", "label_color",
                                                  "created_at", "updated_at")},
                            "clip_count": len(c["entries"])})
            return sorted(out, key=lambda c: c["updated_at"], reverse=True)

    def get(self, cid: str) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._find(cid)))

    def create(self, name: str, *, status: str = "Draft", label: str = "",
               label_color: str | None = None, notes: str = "") -> dict:
        now = time.time()
        col = {
            "id": uuid.uuid4().hex[:10],
            "name": self._clean_name(name),
            "status": self._check_status(status),
            "label": (label or "").strip()[:30],
            "label_color": self._check_color(label_color),
            "notes": (notes or "")[:MAX_NOTES],
            "created_at": now,
            "updated_at": now,
            "entries": [],
        }
        with self._lock:
            self._data["collections"].append(col)
            self._save()
            return json.loads(json.dumps(col))

    def update(self, cid: str, fields: dict) -> dict:
        with self._lock:
            col = self._find(cid)
            if "name" in fields:
                col["name"] = self._clean_name(fields["name"])
            if "status" in fields:
                col["status"] = self._check_status(fields["status"])
            if "label" in fields:
                col["label"] = (fields["label"] or "").strip()[:30]
            if "label_color" in fields:
                col["label_color"] = self._check_color(fields["label_color"])
            if "notes" in fields:
                col["notes"] = (fields["notes"] or "")[:MAX_NOTES]
            col["updated_at"] = time.time()
            self._save()
            return json.loads(json.dumps(col))

    def delete(self, cid: str) -> None:
        with self._lock:
            before = len(self._data["collections"])
            self._data["collections"] = [c for c in self._data["collections"] if c["id"] != cid]
            if len(self._data["collections"]) == before:
                raise NotFound("collection not found")
            self._save()

    # --- entries -------------------------------------------------------------
    def add_entry(self, cid: str, snapshot: dict) -> dict:
        """snapshot: job_id, clip_id, title, start, end, duration, watch_rate, rating,
        platform, quality, video, thumbnail, audio, captions."""
        required = ("job_id", "clip_id", "title", "video")
        for key in required:
            if not snapshot.get(key):
                raise CollectionError(f"missing {key}")
        with self._lock:
            col = self._find(cid)
            for e in col["entries"]:
                if e["job_id"] == snapshot["job_id"] and e["clip_id"] == snapshot["clip_id"]:
                    raise CollectionError("this clip is already in the collection")
            entry = {
                "id": uuid.uuid4().hex[:10],
                "order": len(col["entries"]),
                "note": "",
                "status": "Draft",
                "added_at": time.time(),
                **snapshot,
                "title": str(snapshot["title"])[:200],
            }
            col["entries"].append(entry)
            col["updated_at"] = time.time()
            self._save()
            return json.loads(json.dumps(entry))

    def update_entry(self, cid: str, eid: str, fields: dict) -> dict:
        with self._lock:
            col = self._find(cid)
            entry = self._find_entry(col, eid)
            if "title" in fields:
                entry["title"] = self._clean_name(fields["title"])[:200]
            if "status" in fields:
                entry["status"] = self._check_status(fields["status"])
            if "note" in fields:
                entry["note"] = (fields["note"] or "")[:1000]
            if "label" in fields:
                entry["label"] = (fields["label"] or "").strip()[:30]
            if "label_color" in fields:
                entry["label_color"] = self._check_color(fields["label_color"])
            col["updated_at"] = time.time()
            self._save()
            return json.loads(json.dumps(entry))

    def remove_entry(self, cid: str, eid: str) -> None:
        with self._lock:
            col = self._find(cid)
            self._find_entry(col, eid)
            col["entries"] = [e for e in col["entries"] if e["id"] != eid]
            self._renumber(col)
            col["updated_at"] = time.time()
            self._save()

    def move_entry(self, cid: str, eid: str, direction: str | None = None,
                   to_index: int | None = None) -> None:
        with self._lock:
            col = self._find(cid)
            self._renumber(col)
            entries = col["entries"]
            idx = next((i for i, e in enumerate(entries) if e["id"] == eid), None)
            if idx is None:
                raise NotFound("clip not found in this collection")
            if to_index is not None:
                target = max(0, min(int(to_index), len(entries) - 1))
            elif direction == "up":
                target = max(0, idx - 1)
            elif direction == "down":
                target = min(len(entries) - 1, idx + 1)
            else:
                raise CollectionError("give direction 'up' or 'down', or a to_index")
            item = entries.pop(idx)
            entries.insert(target, item)
            self._renumber(col)
            col["updated_at"] = time.time()
            self._save()

    # --- cascades --------------------------------------------------------------
    def remove_job(self, job_id: str) -> int:
        """Drop every entry that came from a deleted project. Returns how many were removed."""
        removed = 0
        with self._lock:
            for col in self._data["collections"]:
                before = len(col["entries"])
                col["entries"] = [e for e in col["entries"] if e["job_id"] != job_id]
                if len(col["entries"]) != before:
                    removed += before - len(col["entries"])
                    self._renumber(col)
                    col["updated_at"] = time.time()
            if removed:
                self._save()
        return removed
