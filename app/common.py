"""
common.py - settings and small helpers shared by more than one manager.

Nothing in here makes a decision. It only holds file locations, fixed
settings, the shared vocabulary of codes, and helpers that read the
client's timeline (which years he worked, which years we forecast).
"""

import json
import logging
import os

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


# ----------------------------------------------------------------------------
# Config files
# ----------------------------------------------------------------------------

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
    for key in ("signal_codes", "document_codes", "source_types"):
        if not vocab.get(key):
            problems.append(f"config/vocabulary.json is missing '{key}'")
    rules = policy()
    for key in ("policy_version", "tolerance", "fit", "review_interval_months", "routing"):
        if key not in rules:
            problems.append(f"config/policy.json is missing '{key}'")
    return problems


# ----------------------------------------------------------------------------
# The client's timeline
# ----------------------------------------------------------------------------

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
