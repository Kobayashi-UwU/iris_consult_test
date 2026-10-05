"""Runtime configuration, read from environment variables (.env supported locally)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "prompts"

load_dotenv(ROOT / ".env")

CACHE_DIR = Path(os.getenv("CACHE_DIR", str(DATA_DIR / "cache")))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
APP_MODE = os.getenv("APP_MODE", "demo").strip().lower()  # demo | live
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "2"))
SHORTLIST_SIZE = int(os.getenv("SHORTLIST_SIZE", "12"))
BORDERLINE_BAND = float(os.getenv("BORDERLINE_BAND", "5"))

JOB_ID = "GEP-2027"
JOB_TITLE = "Graduate Engineer Programme 2027"
JOB_SEATS = 80


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{ROOT / 'local.db'}"
    # Railway provides postgres:// or postgresql://; SQLAlchemy + psycopg 3 needs the driver name.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def live_available() -> bool:
    return bool(GEMINI_API_KEY)
