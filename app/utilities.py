"""Shared utilities: response cache, file loaders, client config."""

import hashlib
import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def load_prompt(name: str) -> str:
    path = os.path.join(_BASE, "app", "prompts", name)
    with open(path) as f:
        return f.read()


def load_schema(name: str) -> dict:
    path = os.path.join(_BASE, "app", "schemas", name)
    with open(path) as f:
        return json.load(f)


def load_policy() -> dict:
    path = os.path.join(_BASE, "config", "policy.json")
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# OpenAI client and model config
# ---------------------------------------------------------------------------

def client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    return OpenAI(api_key=api_key)


def model() -> str:
    return os.getenv("OPENAI_MODEL", os.getenv("AI_MODEL", "gpt-4o"))


def research_model() -> str:
    """Model for research calls (must support web_search in Responses API)."""
    return os.getenv("OPENAI_RESEARCH_MODEL", "gpt-5.4")


# ---------------------------------------------------------------------------
# Text / JSON helpers
# ---------------------------------------------------------------------------

def extract_prompt_version(prompt_text: str) -> str:
    """Return the value of the first '# prompt_version: ...' line, or 'unknown'."""
    for line in prompt_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# prompt_version:"):
            return stripped.split(":", 1)[1].strip()
    return "unknown"


def extract_json(text: str) -> str:
    """Extract a JSON object from text that may contain markdown fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[-1].strip() == "```":
            inner = "\n".join(lines[1:-1])
            return inner.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start:end + 1]
    return stripped


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

def cache_dir() -> str:
    return os.path.join(_BASE, os.getenv("CACHE_DIR", "data/cache"))


def cache_key(kind: str, payload: dict, prompt_version: str, model: str) -> str:
    """SHA-256 of canonical JSON of {kind, payload, prompt_version, model}."""
    canonical = json.dumps(
        {"kind": kind, "model": model, "payload": payload, "prompt_version": prompt_version},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def cache_read(key: str) -> dict | None:
    path = os.path.join(cache_dir(), f"{key}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_write(key: str, data: dict) -> None:
    """Write data to cache atomically (temp file + os.replace)."""
    directory = cache_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{key}.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Cache write failed: %s", exc)
