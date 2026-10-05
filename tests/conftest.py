import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TMP = Path(tempfile.mkdtemp(prefix="iris_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["CACHE_DIR"] = str(_TMP / "cache")
os.environ["APP_MODE"] = "demo"
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture
def candidates():
    from core.seed import read_candidates_csv
    return read_candidates_csv()


@pytest.fixture
def stub_client():
    """A live-mode client whose Gemini call is replaced by the deterministic stub."""
    from ai.client import LLMClient
    from tests.stub_llm import STUB_MODEL, stub_generate
    c = LLMClient("live")
    c.mode, c.model = "live", STUB_MODEL
    c._generate = stub_generate
    return c


@pytest.fixture
def fresh_db():
    from core.seed import reset
    reset()
