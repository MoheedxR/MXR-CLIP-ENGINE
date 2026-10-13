#!/usr/bin/env python3
"""Start MXR CLIP ENGINE on your machine and open it in your browser.

    python run.py

Options (environment variables):
    PORT=8000          port to listen on
    HOST=127.0.0.1     use 0.0.0.0 to allow other devices on your network
    NO_BROWSER=1       don't open a browser window
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PY = (3, 10)


def fail(msg: str) -> None:
    print(f"\n  ✖ {msg}\n")
    sys.exit(1)


def check_python() -> None:
    if sys.version_info < MIN_PY:
        fail(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ is required (you have {sys.version.split()[0]}).")


def check_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        fail("FFmpeg is required but was not found on your PATH.\n"
             "    macOS:    brew install ffmpeg\n"
             "    Windows:  winget install Gyan.FFmpeg   (then reopen this terminal)\n"
             "    Ubuntu:   sudo apt install ffmpeg")


def check_packages() -> None:
    try:
        import fastapi  # noqa: F401
        import faster_whisper  # noqa: F401
        import uvicorn  # noqa: F401
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        fail(f"A Python package is missing ({exc.name}). Install everything with:\n"
             "    pip install -r requirements.txt")


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, port)) != 0


def main() -> None:
    check_python()
    check_ffmpeg()
    check_packages()

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    if not port_free(host, port):
        fail(f"Port {port} is already in use. Set another one, e.g. PORT=8080 python run.py")

    url = f"http://localhost:{port}"
    print("\n  MXR CLIP ENGINE")
    print(f"  ➜  Open {url} in your browser")
    print("  ➜  Press Ctrl+C to stop\n")
    if not os.getenv("NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    sys.path.insert(0, str(ROOT))
    import uvicorn

    uvicorn.run("clipper.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Stopped. See you next time.\n")
