"""Shared utilities: response cache, file loaders, client config."""

import hashlib
import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

from app import common

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


def forecast_settings() -> dict:
    """The "forecasting" block of config/policy.json.

    Running the calls:
        web_search             let Call 1 search the web for market figures
        max_retries            how many times one piece is asked before giving up
        retry_wait_seconds     wait after a failed request (times the attempt number)
        max_errors_sent_back   how many problems are listed to the AI on a retry
        min_events/max_events  how many future events Call 4 must give
        max_crime_types        crime types after the honest one
        honest_category        the vocabulary category only the honest type (T1) uses
        honest_required_signal the signal the honest type must always include

    Sense limits (an answer outside these can't be right, so it is asked again):
        max_asset_exposures    markets one asset may follow
        sensitivity_range      how many % an asset moves when its market moves 1%
        own_growth_range       an asset's own % growth a year, apart from its markets
        market_change_range    a market's % change in one year
        scenario_chance_total_range   what a year's steady/boom/downturn chances may add up to
        all_zero_history_min_years    known years needed before an all-0 history is rejected

    Never sent to the AI (so his own story can't steer the forecasts):
        personal_keys          name, nationality, ...
        claimed_figure_keys    declared values, income, net worth, ...
        honorifics             titles removed together with the client's name (Mr Tan)

    Free-text wrappers, so the model treats the text as data, not instructions:
        declaration_markers    [open, close] around the client's declaration
        notes_markers          [open, close] around the officer's review notes
    """
    return load_policy()["forecasting"]


# ---------------------------------------------------------------------------
# Forecasting prompt and schema files
# ---------------------------------------------------------------------------

def load_forecast_prompt(name: str) -> tuple[str, str]:
    """Read app/prompts/<name>.txt. Returns (version, text without the # comment lines)."""
    template = load_prompt(f"{name}.txt")
    text = "\n".join(line for line in template.splitlines() if not line.startswith("#")).strip()
    return extract_prompt_version(template), text


def fill_placeholders(text: str, values: dict) -> str:
    """Replace each {name} in the text. Fails loudly if a placeholder is left unfilled."""
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    leftover = re.findall(r"\{[a-z_]+\}", text)
    if leftover:
        raise ValueError(f"prompt placeholders not filled: {sorted(set(leftover))}")
    return text


def build_instructions(prompt_name: str, values: dict, web_search: bool = False) -> tuple[str, str]:
    """System rules + the task prompt, filled in. Returns (prompt_version, instructions)."""
    settings = forecast_settings()
    declaration_open, declaration_close = settings["declaration_markers"]
    notes_open, notes_close = settings["notes_markers"]
    shared = {"currency": common.REPORTING_CURRENCY,
              "declaration_open": declaration_open, "declaration_close": declaration_close,
              "notes_open": notes_open, "notes_close": notes_close}
    _, system_text = load_forecast_prompt("system")
    version, task_text = load_forecast_prompt(prompt_name)
    parts = [fill_placeholders(system_text, shared), fill_placeholders(task_text, {**shared, **values})]
    if prompt_name in ("markets_list", "market_series") and not web_search:
        parts.append(load_forecast_prompt("no_web_search")[1])
    return version, "\n\n".join(parts)


_schema_cache: dict = {}


def _add_vocabulary(node: Any) -> Any:
    """Turn {"x-vocab": "signal_codes"} markers into a real list of allowed codes."""
    if isinstance(node, dict):
        if "x-vocab" in node:
            codes = list(common.vocabulary().get(node["x-vocab"], {}))
            return {"type": "string", "enum": codes}
        return {key: _add_vocabulary(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_add_vocabulary(item) for item in node]
    return node


def load_forecast_schema(name: str) -> dict:
    """Read app/schemas/<name>.schema.json with the vocabulary codes filled in."""
    if name not in _schema_cache:
        _schema_cache[name] = _add_vocabulary(load_schema(f"{name}.schema.json"))
    return _schema_cache[name]


# ---------------------------------------------------------------------------
# OpenAI client and model config
# ---------------------------------------------------------------------------

def client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    return OpenAI(api_key=api_key)


def has_api_key() -> bool:
    """Without a key only saved replies can be used."""
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY"))


def model() -> str:
    return os.getenv("OPENAI_MODEL")


def research_model() -> str:
    return os.getenv("OPENAI_RESEARCH_MODEL")


def forecast_model() -> str:
    """OPENAI_FORECAST_MODEL if set, otherwise the model every other section uses."""
    return os.getenv("OPENAI_FORECAST_MODEL") or model()


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

def cache_dir(kind: str | None = None) -> str:
    base = os.path.join(_BASE, os.getenv("CACHE_DIR", "data/cache"))
    return os.path.join(base, kind) if kind else base


def cache_key(kind: str, payload: dict, prompt_version: str, model: str) -> str:
    """SHA-256 of canonical JSON of {kind, payload, prompt_version, model}."""
    canonical = json.dumps(
        {"kind": kind, "model": model, "payload": payload, "prompt_version": prompt_version},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def cache_read(key: str, kind: str | None = None) -> dict | None:
    path = os.path.join(cache_dir(kind), f"{key}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_check(kind: str, payload: dict, prompt_version: str, mdl: str) -> tuple[str, dict | None]:
    """Compute cache key and return (key, cached_data) on hit or (key, None) on miss.

    cached_data includes _cache_path so the caller does not need to reconstruct it.
    The returned key should be passed to cache_write() on a successful API call.
    """
    key = cache_key(kind, payload, prompt_version, mdl)
    cached = cache_read(key, kind)
    if cached is not None:
        cached["_cache_path"] = os.path.join(cache_dir(kind), f"{key}.json")
        return key, cached
    return key, None


def cache_write(key: str, data: dict, kind: str | None = None) -> None:
    """Write data to cache atomically (temp file + os.replace)."""
    directory = cache_dir(kind)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{key}.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Cache write failed: %s", exc)
