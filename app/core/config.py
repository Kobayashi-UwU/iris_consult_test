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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
# Fallback chain, most capable first. Free-tier quotas are per model, so when one is
# exhausted or overloaded the call moves down the chain; open Gemma models come last.
DEFAULT_CHAIN = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                 "gemma-4-31b-it", "gemma-4-26b-a4b-it"]
_chain_env = [m.strip() for m in os.getenv("GEMINI_MODELS", "").split(",") if m.strip()]
MODEL_CHAIN = list(dict.fromkeys(_chain_env or [GEMINI_MODEL, *DEFAULT_CHAIN]))
# Free-tier requests per minute (AI Studio, Oct 2026): Gemini Flash 5, Gemma 30.
RPM_GEMINI = int(os.getenv("RPM_GEMINI", "5"))
RPM_GEMMA = int(os.getenv("RPM_GEMMA", "30"))


def rpm_limit(model: str) -> int:
    if model.startswith("gemma"):
        return RPM_GEMMA
    if model.startswith("gemini"):
        return RPM_GEMINI
    return 10_000  # stubs / unknown
APP_MODE = os.getenv("APP_MODE", "demo").strip().lower()  # demo | live
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "2"))
LLM_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "150"))
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
