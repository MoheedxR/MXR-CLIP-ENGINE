"""Update MXR CLIP ENGINE to the latest version on GitHub.

Keeps your .env settings and your data/ folder (projects, clips, collections).
Run it from the project folder:  python update.py   (or double-click update.bat)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ZIP_URL = "https://github.com/MoheedxR/MXR-CLIP-ENGINE/archive/refs/heads/main.zip"
KEEP = {".env", "data"}          # never overwritten
ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("Downloading the latest version...")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "mxr.zip"
        try:
            urllib.request.urlretrieve(ZIP_URL, zip_path)
        except Exception as exc:
            print(f"Could not download the update: {exc}")
            print("Check your internet connection and try again.")
            return 1
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        source_roots = [p for p in Path(tmp).iterdir() if p.is_dir() and p.name.startswith("MXR-CLIP-ENGINE")]
        if not source_roots:
            print("The downloaded update did not contain the expected folder.")
            return 1
        src_root = source_roots[0]
        copied = 0
        for item in src_root.rglob("*"):
            rel = item.relative_to(src_root)
            if not item.is_file() or rel.parts[0] in KEEP or "__pycache__" in rel.parts:
                continue
            dest = ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            copied += 1
    print(f"Updated {copied} files. Your .env and data/ were kept.")
    print("Start the app again with: py -3.12 run.py   (or python run.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
