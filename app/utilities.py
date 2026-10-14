"""Shared utilities: settings, config, client timeline, file loaders, response cache, client config.

Nothing in here makes a decision. It holds what more than one manager needs,
so the managers never have to import each other.
"""

import hashlib
import time
import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
PROMPT_DIR = os.path.join(APP_DIR, "prompts")
SCHEMA_DIR = os.path.join(APP_DIR, "schemas")

REPORTING_CURRENCY = "SGD"
FORECAST_YEARS = 4                        # how many years after the current year we forecast
SCENARIOS = ("steady", "boom", "downturn")
RANGE_KEYS = ("low", "typical", "high")   # every AI figure comes as a low / typical / high range
BASELINE_TYPE = "T1"                      # T1 is always "his wealth came exactly as declared"


# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------

_config_cache: dict = {}


def read_config(name: str) -> dict:
    """Read config/<name>.json once and keep it. Returns {} if it can't be read."""
    if name in _config_cache:
        return _config_cache[name]
    path = os.path.join(CONFIG_DIR, f"{name}.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        logger.error("Could not read config file %s: %s", path, error)
        return {}
    _config_cache[name] = data
    return data


def vocabulary() -> dict:
    return read_config("vocabulary")


def policy() -> dict:
    return read_config("policy")


def config_problems() -> list[str]:
    """Anything missing from the config folder that would stop the app working."""
    problems = []
    vocab = vocabulary()
    for key in ("signal_codes", "document_codes", "source_types", "crime_categories"):
        if not vocab.get(key):
            problems.append(f"config/vocabulary.json is missing '{key}'")
    rules = policy()
    for key in ("policy_version", "tolerance", "fit", "review_interval_months", "routing", "forecasting"):
        if key not in rules:
            problems.append(f"config/policy.json is missing '{key}'")
    return problems


# ---------------------------------------------------------------------------
# The client's timeline
# ---------------------------------------------------------------------------

def career_positions(record: dict) -> list[dict]:
    """His jobs, oldest first. Falls back to the current job if no history was given."""
    history = record.get("career_history") or []
    if history:
        return history
    career = record.get("career", {})
    return [{**career, "start_year": career.get("career_start_year"), "end_year": None}]


def current_year(record: dict) -> int:
    """The year of the assessment date, e.g. 2026 from '2026-09-24'."""
    return int(str(record.get("current_date", ""))[:4])


def career_years(record: dict) -> tuple[int, int]:
    """(first year he worked, current year)."""
    this_year = current_year(record)
    start_years = [p["start_year"] for p in career_positions(record) if p.get("start_year")]
    first = min(start_years) if start_years else this_year
    return int(first), this_year


def position_end(position: dict, this_year: int) -> int:
    """Last year a job was held. The current job runs to this year."""
    return int(position.get("end_year") or this_year)


def future_years(record: dict) -> list[int]:
    """The forecast years, e.g. [2027, 2028, 2029, 2030]."""
    this_year = current_year(record)
    return list(range(this_year + 1, this_year + FORECAST_YEARS + 1))


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def load_prompt(name: str) -> str:
    path = os.path.join(PROMPT_DIR, name)
    with open(path) as f:
        return f.read()


def load_schema(name: str) -> dict:
    path = os.path.join(SCHEMA_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
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

    FORECAST_WEB_SEARCH=on/off in .env overrides web_search, so market figures can be checked
    online without editing policy.json.
    """
    settings = dict(policy()["forecasting"])
    switch = env_switch("FORECAST_WEB_SEARCH")
    if switch is not None:
        settings["web_search"] = switch
    return settings


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
    shared = {"currency": REPORTING_CURRENCY,
              "declaration_open": declaration_open, "declaration_close": declaration_close}
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
            codes = list(vocabulary().get(node["x-vocab"], {}))
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

_clients: dict = {}


def client() -> OpenAI:
    """One shared OpenAI client per API key. Making a new one for every call left the old ones to be
    closed by the garbage collector at random moments - sometimes in the middle of writing a log line,
    which printed "--- Logging error --- reentrant call" to the console."""
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    if api_key not in _clients:
        _clients[api_key] = OpenAI(api_key=api_key)
    return _clients[api_key]


# Network libraries that log every connection step at DEBUG level
NOISY_LOGGERS = ("httpx", "httpcore", "httpx2", "httpcore2", "openai", "urllib3", "google_genai", "hpack")


def quiet_library_logs() -> None:
    """Keep the network libraries' step-by-step DEBUG lines out of logs/app.log (warnings still go in)."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def has_api_key() -> bool:
    """Without a key only saved replies can be used."""
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY"))


# ONE model for every AI call (document reading, research, typologies, forecast, benchmark),
# so every part of the assessment reasons at the same level. Set it in .env as OPENAI_MODEL.
DEFAULT_MODEL = "gpt-4o-mini"


def model() -> str:
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL


# Kept so older code and tests that ask for a "research", "extract" or "forecast" model still
# work - they all get the one model.
def research_model() -> str:
    return model()


def extract_model() -> str:
    return model()


def forecast_model() -> str:
    return model()


def env_switch(name: str) -> bool | None:
    """An on/off setting from .env: on/true/yes/1 -> True, off/false/no/0 -> False, unset -> None."""
    value = str(os.getenv(name) or "").strip().lower()
    if value in ("on", "true", "yes", "1"):
        return True
    if value in ("off", "false", "no", "0"):
        return False
    return None


# ---------------------------------------------------------------------------
# Text / JSON helpers
# ---------------------------------------------------------------------------

def whole_number(value: object) -> int | None:
    """'16,500,000' / '30%' / 30 -> a whole number, or None if it can't be read."""
    try:
        return int(float(str(value).replace(",", "").replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


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
    base = os.path.join(ROOT_DIR, os.getenv("CACHE_DIR", "data/cache"))
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

# ---------------------------------------------------------------------------
# Assessment rate limit — one assessment per cooldown window per user
# ---------------------------------------------------------------------------

_rate_limit_store: dict[str, float] = {}  # user_key -> timestamp of last assessment


def check_rate_limit(user_key: str) -> tuple[bool, int]:
    """Check whether user_key is allowed to start a new assessment.

    Reads assessment_rate_limit from policy.json.
    Returns (True, 0) if allowed, or (False, seconds_remaining) if still in cooldown.
    """
    config = policy().get("assessment_rate_limit", {})
    if not config.get("enabled", True):
        return True, 0
    cooldown = int(config.get("cooldown_seconds", 60))
    last = _rate_limit_store.get(user_key)
    if last is None:
        return True, 0
    elapsed = time.time() - last
    if elapsed >= cooldown:
        return True, 0
    return False, int(cooldown - elapsed) + 1


def record_assessment(user_key: str) -> None:
    """Record that user_key has just started an assessment (starts the cooldown clock)."""
    _rate_limit_store[user_key] = time.time()