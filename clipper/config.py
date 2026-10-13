import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so users can configure without extra dependencies.

    Existing environment variables always win.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("CLIPPER_DATA_DIR", ROOT / "data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
COLLECTIONS_FILE = DATA_DIR / "collections.json"
STATIC_DIR = ROOT / "static"

APP_NAME = "MXR CLIP ENGINE"
APP_VERSION = "1.1.0"

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

MAX_DOWNLOAD_HEIGHT = int(os.getenv("MAX_DOWNLOAD_HEIGHT", "1080"))

# Clip length limits from the product spec (seconds)
MIN_CLIP_SECONDS = 30.0
MAX_CLIP_SECONDS = 120.0
