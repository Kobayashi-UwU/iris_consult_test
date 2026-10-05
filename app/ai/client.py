"""Gemini client with versioned prompts, structured output, retry and a cache.

Modes:
- demo: answers come only from precomputed results in data/cache (no API calls).
- live: calls Gemini; on failure falls back to the cache for the same input.
"""
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from string import Template

from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core import config


class LLMUnavailable(RuntimeError):
    """No live answer and nothing cached for this input."""


@dataclass
class LLMResult:
    output: dict
    source: str  # live | cache | cache_fallback
    model: str
    prompt_version: str
    input_hash: str
    latency_s: float
    note: str = ""


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user: Template


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Prompt:
    """Load prompts/<name>.md, which has '## system' and '## user' sections."""
    path = config.PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    system = text.split("## system", 1)[1].split("## user", 1)[0].strip()
    user = text.split("## user", 1)[1].strip()
    return Prompt(version=path.stem, system=system, user=Template(user))


def input_hash(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class _Cache:
    """One JSON file per prompt family: data/cache/<family>.json -> {hash: record}."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def _family(self, prompt_version: str) -> str:
        return prompt_version.split(".")[0]

    def _load(self, family: str) -> dict:
        if family not in self._data:
            p = config.CACHE_DIR / f"{family}.json"
            self._data[family] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return self._data[family]

    def get(self, prompt_version: str, key: str) -> dict | None:
        with self._lock:
            return self._load(self._family(prompt_version)).get(key)

    def put(self, prompt_version: str, key: str, record: dict) -> None:
        with self._lock:
            self._load(self._family(prompt_version))[key] = record

    def save(self) -> None:
        with self._lock:
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for family, data in self._data.items():
                p = config.CACHE_DIR / f"{family}.json"
                p.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def keys(self, family: str) -> set[str]:
        with self._lock:
            return set(self._load(family).keys())


CACHE = _Cache()


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is None:
        return True  # network errors etc.
    return int(code) in (408, 429, 500, 502, 503, 504)


class LLMClient:
    def __init__(self, mode: str = config.APP_MODE):
        self.mode = "live" if (mode == "live" and config.live_available()) else "demo"
        self.model = config.GEMINI_MODEL
        self.prefer_cache = False  # build_cache sets this so interrupted builds resume without re-calling the API
        self._genai = None

    def _client(self):
        if self._genai is None:
            from google import genai
            self._genai = genai.Client(api_key=config.GEMINI_API_KEY)
        return self._genai

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=2, max=40),
        reraise=True,
    )
    def _generate(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        from google.genai import types
        resp = self._client().models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        parsed = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
        return parsed.model_dump()

    def run(self, prompt_name: str, variables: dict, schema: type[BaseModel], key_parts: tuple,
            prefer_cache: bool = False) -> LLMResult:
        prompt = load_prompt(prompt_name)
        key = input_hash(prompt.version, *key_parts)
        cached = CACHE.get(prompt.version, key)

        if self.mode == "live" and not ((prefer_cache or self.prefer_cache) and cached):
            t0 = time.time()
            try:
                output = self._generate(prompt.system, prompt.user.substitute(variables), schema)
                latency = round(time.time() - t0, 2)
                CACHE.put(prompt.version, key, {
                    "output": output, "model": self.model, "prompt_version": prompt.version,
                    "latency_s": latency, "created_at": datetime.now(timezone.utc).isoformat(),
                })
                return LLMResult(output, "live", self.model, prompt.version, key, latency)
            except Exception as exc:  # noqa: BLE001 — any failure falls back to cache
                if cached:
                    return LLMResult(cached["output"], "cache_fallback", cached.get("model", ""), prompt.version, key,
                                     cached.get("latency_s", 0.0), note=f"Live call failed ({type(exc).__name__}); used cached result.")
                raise LLMUnavailable(f"Gemini call failed and no cached result exists: {exc}") from exc

        if cached:
            return LLMResult(cached["output"], "cache", cached.get("model", ""), prompt.version, key, cached.get("latency_s", 0.0))
        raise LLMUnavailable(
            "Demo mode has no precomputed AI result for this input (it was edited). "
            "Switch to Live AI mode with a Gemini API key, or reset the demo."
        )
