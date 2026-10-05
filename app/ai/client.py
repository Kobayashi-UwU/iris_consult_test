"""Gemini client with versioned prompts, structured output, retry and a cache.

Modes:
- demo: answers come only from precomputed results in data/cache (no API calls).
- live: calls Gemini; on failure falls back to the cache for the same input.

Live calls go through a model fallback chain (config.MODEL_CHAIN, most capable
first). A model that hits its quota or is overloaded is put on cooldown and the
call moves to the next model; the model actually used is recorded with every result.
"""
import os
import re
import sys
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from string import Template

from pydantic import BaseModel, ValidationError

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


# ---------------------------------------------------------------- model fallback chain

_COOLDOWN: dict[str, float] = {}
_COOL_LOCK = threading.Lock()
MAX_WAIT_FOR_COOLDOWN_S = 75


def _log(msg: str) -> None:
    if os.getenv("LLM_DEBUG"):
        print(f"[llm {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _cool(model: str, seconds: float) -> None:
    with _COOL_LOCK:
        _COOLDOWN[model] = max(_COOLDOWN.get(model, 0.0), time.time() + seconds)


def _cooling(model: str) -> float:
    with _COOL_LOCK:
        return max(0.0, _COOLDOWN.get(model, 0.0) - time.time())


def cooldown_status(models: list[str]) -> dict[str, int]:
    return {m: int(_cooling(m)) for m in models}


def _classify(exc: BaseException) -> float:
    """How long to rest a model after this error (seconds). 0 = only skip it for this call."""
    if isinstance(exc, (ValidationError, json.JSONDecodeError, ValueError)):
        return 0
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    delay = re.search(r"retryDelay'?:\s*'(\d+)s'", text)
    hinted = float(delay.group(1)) if delay else None
    if code is None:
        return 60 if "timeout" in text.lower() or "timed out" in text.lower() else 10  # timeout / network error
    code = int(code)
    if code == 429:
        if "PerDay" in text:
            return hinted or 6 * 3600
        return min(hinted or 60, 120)
    if code in (500, 502, 503, 504, 408):
        return 30
    if code == 404:
        return 10 ** 9  # model retired or unavailable for this key
    return 0


class _RateLimiter:
    """Client-side pacing per model (free-tier RPM), so we wait for a better model's
    next minute instead of falling down the chain on a per-minute limit."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: dict[str, list[float]] = {}

    def reserve(self, model: str) -> float:
        rpm = config.rpm_limit(model)
        with self._lock:
            now = time.time()
            q = [x for x in self._calls.get(model, []) if x > now - 60]
            wait = 0.0 if len(q) < rpm else (q[0] + 60.5 - now)
            q = (q[1:] if wait else q) + [now + wait]
            self._calls[model] = q
            return wait


_LIMITER = _RateLimiter()


def _extract_json(text: str) -> str:
    """Some open models wrap JSON in prose or code fences; keep the outermost object."""
    a, b = text.find("{"), text.rfind("}")
    return text[a:b + 1] if a != -1 and b > a else text


class LLMClient:
    def __init__(self, mode: str = config.APP_MODE):
        self.mode = "live" if (mode == "live" and config.live_available()) else "demo"
        self.models = list(config.MODEL_CHAIN)
        self.model = self.models[0]
        self.prefer_cache = False  # build_cache sets this so interrupted builds resume without re-calling the API
        self._genai = None
        self._init_lock = threading.Lock()

    def _client(self):
        with self._init_lock:
            if self._genai is None:
                from google import genai
                from google.genai import types
                # A hung request should fail over to the next model rather than block the workflow.
                self._genai = genai.Client(api_key=config.GEMINI_API_KEY,
                                           http_options=types.HttpOptions(timeout=config.LLM_TIMEOUT_S * 1000))
        return self._genai

    def _call(self, model: str, system: str, user: str, schema: type[BaseModel]) -> dict:
        """One request to one model. Tests replace this with a stub."""
        from google.genai import types
        resp = self._client().models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=schema,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        if isinstance(resp.parsed, schema):
            return resp.parsed.model_dump()
        return schema.model_validate_json(_extract_json(resp.text or "")).model_dump()

    def _generate(self, system: str, user: str, schema: type[BaseModel]) -> tuple[dict, str]:
        """Try models in order of capability; return (output, model used)."""
        last_exc: BaseException | None = None
        for _round in range(4):
            tried = False
            for model in self.models:
                if _cooling(model) > 0:
                    continue
                tried = True
                pause = _LIMITER.reserve(model)
                if pause:
                    _log(f"{model}: pacing {pause:.0f}s")
                    time.sleep(pause)
                t0 = time.time()
                try:
                    out = self._call(model, system, user, schema)
                    _log(f"{model}: ok in {time.time() - t0:.0f}s")
                    return out, model
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    rest = _classify(exc)
                    _log(f"{model}: {type(exc).__name__} {getattr(exc, 'code', '')} after {time.time() - t0:.0f}s "
                         f"-> rest {rest:.0f}s · {str(exc)[:100]}")
                    if rest:
                        _cool(model, rest)
            wait = min((_cooling(m) for m in self.models), default=0)
            if wait > MAX_WAIT_FOR_COOLDOWN_S:
                break
            if not tried or wait > 0:
                time.sleep(wait + 1)
        raise LLMUnavailable(f"All models in the fallback chain are unavailable right now: {last_exc}")

    def run(self, prompt_name: str, variables: dict, schema: type[BaseModel], key_parts: tuple,
            prefer_cache: bool = False) -> LLMResult:
        prompt = load_prompt(prompt_name)
        key = input_hash(prompt.version, *key_parts)
        cached = CACHE.get(prompt.version, key)

        if self.mode == "live" and not ((prefer_cache or self.prefer_cache) and cached):
            t0 = time.time()
            try:
                output, used = self._generate(prompt.system, prompt.user.substitute(variables), schema)
                latency = round(time.time() - t0, 2)
                CACHE.put(prompt.version, key, {
                    "output": output, "model": used, "prompt_version": prompt.version,
                    "latency_s": latency, "created_at": datetime.now(timezone.utc).isoformat(),
                })
                return LLMResult(output, "live", used, prompt.version, key, latency)
            except Exception as exc:  # noqa: BLE001 — any failure falls back to cache
                if cached:
                    return LLMResult(cached["output"], "cache_fallback", cached.get("model", ""), prompt.version, key,
                                     cached.get("latency_s", 0.0), note=f"Live call failed ({type(exc).__name__}); used cached result.")
                raise LLMUnavailable(f"AI call failed and no cached result exists: {exc}") from exc

        if cached:
            return LLMResult(cached["output"], "cache", cached.get("model", ""), prompt.version, key, cached.get("latency_s", 0.0))
        raise LLMUnavailable(
            "Demo mode has no precomputed AI result for this input (for example edited criteria, or an interview "
            "kit for a lower-ranked candidate). Turn on Live AI in the sidebar to generate it."
        )
