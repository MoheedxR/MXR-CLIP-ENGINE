"""Small file helpers that behave on Windows.

On Windows, antivirus, search indexing, or cloud sync (e.g. OneDrive on a
redirected Desktop) can hold a file open for a moment. Renaming a temp file
over it then fails with PermissionError ("Access is denied"). We retry briefly.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_RETRIES = 40          # up to about 4 seconds in total
_DELAY = 0.1           # seconds between attempts


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text to path via a temp file, retrying if Windows locks the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    for attempt in range(_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _RETRIES - 1:
                # Last resort: write in place. Slightly less safe, but the job keeps going.
                path.write_text(text, encoding=encoding)
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return
            time.sleep(_DELAY)
