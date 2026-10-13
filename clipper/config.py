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
        if not value:
            continue  # a blank line means "use the default", not an empty value
        os.environ.setdefault(key.strip(), value)


def _int_env(name: str, default: int, low: int = 1, high: int = 10**6) -> int:
    """Read an integer setting. Blank or invalid values fall back to the default."""
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


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
# Beam size 1 (greedy) is several times faster on a CPU, with little loss on clear speech.
# Set 5 for the most careful transcript.
WHISPER_BEAM_SIZE = _int_env("WHISPER_BEAM_SIZE", 1, low=1, high=10)
# Threads for the CPU model. Defaults to every core (capped at 16).
WHISPER_THREADS = _int_env("WHISPER_THREADS", os.cpu_count() or 4, low=1, high=16)
# Batched decoding runs several 30-second windows at once. About 5x faster on a CPU, same words.
# Set 1 to use the simpler sequential decoder.
WHISPER_BATCH_SIZE = _int_env("WHISPER_BATCH_SIZE", 8, low=1, high=32)

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# How many candidate moments the AI reviews before it picks. More gives it more to choose from.
LLM_SHORTLIST = _int_env("LLM_SHORTLIST", 36, low=6, high=200)

MAX_DOWNLOAD_HEIGHT = _int_env("MAX_DOWNLOAD_HEIGHT", 1080, low=144, high=4320)

# Clip length limits from the product spec (seconds)
MIN_CLIP_SECONDS = 30.0
MAX_CLIP_SECONDS = 120.0
