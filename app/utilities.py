"""Shared utilities: response cache and prompt helpers."""

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract_prompt_version(prompt_text: str) -> str:
    """Return the value of the first '# prompt_version: ...' line, or 'unknown'."""
    for line in prompt_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# prompt_version:"):
            return stripped.split(":", 1)[1].strip()
    return "unknown"


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
