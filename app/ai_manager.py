"""
ai_manager.py - every AI call in the system (OpenAI / ChatGPT).

The AI only does what an algorithm can't: it reads free text and it forecasts.
It never decides. All decisions are made by logic_manager with fixed rules.

The sections run in this order for a new client:

    1. Sector Crime Scan  (teammates)
         get_research       trusted-site search for published ML/TF typology reports
         get_typologies     up to 5 ways his industry is commonly abused
         get_declaration    free-text declaration -> structured sources (crime-scan format)
    2. Benchmark          (teammates)
         get_ai_data        single expected values (wealth, liquidity, composition, ...)
    3. Forecasting        (runs after the benchmark)
         run_onboarding_forecast   Step 0 + Calls 1-4: declaration, markets, earnings,
                                   honest type + crime categories, future events
         read_review_notes         officer notes at each review -> structured data

All sections use OpenAI. Settings come from .env:
    OPENAI_API_KEY            the key (OPEN_AI_API_KEY also accepted)
    OPENAI_MODEL              crime scan + forecasting model (e.g. gpt-4o-mini)
    OPENAI_RESEARCH_MODEL     model for the trusted-site research
    OPENAI_FORECAST_MODEL     optional: a different model just for forecasting
    FORECAST_WEB_SEARCH       on/off: let Call 1 search the web for market figures (default off)
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

import jsonschema
from dotenv import load_dotenv
from jsonschema import Draft202012Validator
from openai import OpenAI

from app import common
from app.utilities import (
    cache_dir, cache_key, cache_read, cache_write,
    client, extract_json, extract_prompt_version,
    load_policy, load_prompt, load_schema,
    model, research_model,
)

load_dotenv()  # loads your secret/environment variables from the .env file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Benchmark Section
# ---------------------------------------------------------------------------

# Generate a prompt using the client's information
def generate_prompt(client_data):

    # Read the prompt from the text file
    with open("app/prompts/benchmark.txt", "r") as file:
        prompt = file.read()

    # Replace {client_data} with the actual client information
    prompt = prompt.replace("{client_data}", str(client_data))

    return prompt

# Send the prompt to OpenAI
def call_openai(prompt):

    # Send the prompt to the AI model
    try:
        response = client().responses.create(
            model="gpt-4.1-nano",
            input=prompt
        )
        
        # Return the AI's response
        return response.output_text

    except Exception as error:
        handle_ai_failure(error)
        return None

# Handles AI request errors
def handle_ai_failure(error):

    #Handle AI request failure
    print("AI request failed.")
    print("Error:", error)

    return None

# Converts the AI response into Python data
def parse_ai_response(response):

    # Remove Markdown code block formatting
    response = response.replace("```json", "")
    response = response.replace("```", "")

    # Remove unnecessary spaces
    response = response.strip()

    # Convert the JSON response into a Python dictionary
    data = json.loads(response)

    return data

#Get the AI result, to be used in logic
def get_ai_data(client_data):
    prompt = generate_prompt(client_data)
    result = call_openai(prompt)

    if result is not None:
        return parse_ai_response(result)
    return None
    

# ---------------------------------------------------------------------------
# Typologies Section
# ---------------------------------------------------------------------------

BENCHMARK_ALLOWED_FIELDS = ["occupation", "industry", "age", "career_start_year", "country"]
RESEARCH_ALLOWED_FIELDS = ["industry", "country"]

# Must match policy.json "typology_max_patterns"
_TYPOLOGY_MAX_PATTERNS = 5

# User-facing one-line messages per error type (full detail goes to log)
_USER_MESSAGES = {
    "no_key": "No OpenAI API key found. Add OPENAI_API_KEY to .env.",
    "api":    "Could not reach OpenAI. Check your internet or API key.",
    "json":   "The AI's answer was not valid JSON.",
    "schema": "The AI's answer did not match the expected format.",
}

# Display name for each call kind shown to the user
_KIND_DISPLAY = {
    "typology":    "Sector Crime Scan",
    "declaration": "Declaration reading",
    "research":    "Research",
}


def _user_warning(kind: str, error_type: str) -> str:
    """Build the single short line shown to the user when an AI call fails."""
    display = _KIND_DISPLAY.get(kind, kind.capitalize())
    msg = _USER_MESSAGES.get(error_type, "An unexpected error occurred.")
    return f"{display}: {msg} This case needs manual review. Details: logs/app.log"

# Trim AI response to 5 typologies
def _trim_typologies(data: dict) -> tuple[dict, list[str]]:
    """Trim sector_typologies to _TYPOLOGY_MAX_PATTERNS before schema validation."""
    typologies = data.get("sector_typologies", [])
    if len(typologies) <= _TYPOLOGY_MAX_PATTERNS:
        return data, []
    trimmed = {**data, "sector_typologies": typologies[:_TYPOLOGY_MAX_PATTERNS]}
    warning = (
        f"AI returned {len(typologies)} typologies; "
        f"kept first {_TYPOLOGY_MAX_PATTERNS} per policy."
    )
    logger.warning(warning)
    return trimmed, [warning]


def _call_ai(
    kind: str,
    payload: dict,
    prompt: str,
    schema: dict,
    transform=None,
) -> tuple[dict | None, list[str]]:
    """Call OpenAI with caching and a single retry on failure.

    Returns (result_dict, warnings). Never raises.

    Error handling:
    - Cache hit      → return immediately, no API call.
    - No API key     → (None, [one-line warning]).
    - API/network    → retry once; two failures → (None, [one-line warning]).
    - Bad JSON       → retry once; two failures → (None, [one-line warning]).
    - Schema invalid → retry once; two failures → (None, [one-line warning]).

    Full error detail is logged to logs/app.log only; never shown on screen.

    transform: optional (dict) -> (dict, list[str]) called after JSON parse and
               before schema validation (e.g. to trim oversized arrays).

    The returned dict includes "model" and "_cache_path" metadata keys.
    """
    warnings: list[str] = []
    prompt_version = extract_prompt_version(prompt)
    mdl = model()

    key = cache_key(kind, payload, prompt_version, mdl)
    cached = cache_read(key)
    if cached is not None:
        logger.debug("Cache hit for key %.12s", key)
        cached["_cache_path"] = os.path.join(cache_dir(), f"{key}.json")
        return cached, warnings

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    if not api_key:
        logger.warning("_call_ai (%s): no API key configured", kind)
        return None, [_user_warning(kind, "no_key")]

    last_error = ""
    error_type = "api"

    for attempt in range(2):
        attempt_warnings: list[str] = []

        # ── Step 1: network call ──────────────────────────────────────────
        try:
            response = client().chat.completions.create(
                model=mdl,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            error_type = "api"
            last_error = repr(exc)
            logger.warning(
                "_call_ai (%s) attempt %d API error: %s", kind, attempt + 1, last_error
            )
            continue

        # ── Step 2: JSON parse ────────────────────────────────────────────
        raw_content = response.choices[0].message.content
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            error_type = "json"
            last_error = repr(exc)
            logger.warning(
                "_call_ai (%s) attempt %d JSON parse error: %s | raw: %.2000s",
                kind, attempt + 1, last_error, raw_content,
            )
            continue

        # ── Step 3: optional transform then schema validation ─────────────
        try:
            if transform is not None:
                data, attempt_warnings = transform(data)
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            error_type = "schema"
            last_error = exc.message
            logger.warning(
                "_call_ai (%s) attempt %d schema validation failed: %s",
                kind, attempt + 1, last_error,
            )
            continue
        except Exception as exc:
            error_type = "api"
            last_error = repr(exc)
            logger.warning(
                "_call_ai (%s) attempt %d unexpected error in transform/validate: %s",
                kind, attempt + 1, last_error,
            )
            continue

        # ── Success ───────────────────────────────────────────────────────
        data["model"] = mdl
        cache_write(key, data)
        data["_cache_path"] = os.path.join(cache_dir(), f"{key}.json")
        warnings.extend(attempt_warnings)
        return data, warnings

    # Both attempts exhausted
    logger.error(
        "_call_ai (%s) exhausted all retries. error_type=%s last_error=%s",
        kind, error_type, last_error,
    )
    return None, [_user_warning(kind, error_type)]


def get_typologies(case_input: dict) -> tuple[dict | None, list[str]]:
    """Call AI for sector abuse typologies only.

    Only the fields in BENCHMARK_ALLOWED_FIELDS are sent to the AI.
    The client's declared figures are never included.
    Returns: (response_dict | None, warnings)
    """
    payload = {k: case_input[k] for k in BENCHMARK_ALLOWED_FIELDS if k in case_input}
    if "country" not in payload:
        payload["country"] = case_input.get("country_of_residence", "Singapore")

    template = load_prompt("typology.txt")
    prompt = template.replace("{payload_json}", json.dumps(payload, indent=2))
    schema = load_schema("typology_response.schema.json")

    return _call_ai("typology", payload, prompt, schema, transform=_trim_typologies)


def get_declaration(case_input: dict) -> tuple[dict | None, list[str]]:
    """Call AI to parse the free-text declaration into structured sources.

    Returns: (response_dict | None, warnings)
    """
    declaration_text = case_input.get("declaration_text", "")

    template = load_prompt("crime_scan_declaration.txt")
    prompt = template.replace("{declaration_text}", declaration_text)
    schema = load_schema("crime_scan_declaration.schema.json")

    return _call_ai("declaration", {"declaration_text": declaration_text}, prompt, schema)


# ---------------------------------------------------------------------------
# Research AI Function
# ---------------------------------------------------------------------------

def _build_research_payload(case_input: dict) -> dict:
    """Extract only RESEARCH_ALLOWED_FIELDS from case_input."""
    payload = {k: case_input[k] for k in RESEARCH_ALLOWED_FIELDS if k in case_input}
    if "country" not in payload:
        payload["country"] = case_input.get("country_of_residence", "Singapore")
    return payload


def _trim_reports(data: dict, max_reports: int, max_excerpts: int) -> tuple[dict, list[str]]:
    """Trim reports and excerpts to policy limits. Coerce year strings to int."""
    warnings: list[str] = []
    reports = data.get("reports", [])

    if len(reports) > max_reports:
        warning = (
            f"AI returned {len(reports)} reports; "
            f"kept first {max_reports} per policy."
        )
        logger.warning(warning)
        warnings.append(warning)
        reports = reports[:max_reports]

    for r in reports:
        excerpts = r.get("excerpts", [])
        if len(excerpts) > max_excerpts:
            r["excerpts"] = excerpts[:max_excerpts]
        if isinstance(r.get("year"), str):
            try:
                r["year"] = int(r["year"])
            except (ValueError, TypeError):
                pass

    return {**data, "reports": reports}, warnings


def _add_report_ids(data: dict) -> dict:
    """Add report_id R1, R2, ... to each report. Done by code, not the AI."""
    for i, r in enumerate(data.get("reports", []), 1):
        r["report_id"] = f"R{i}"
    return data


def _extract_api_sources(response) -> list[dict]:
    """Extract URL citations from Responses API annotations.

    Returns a de-duplicated list of {url, title} dicts.
    """
    seen: set[str] = set()
    sources: list[dict] = []
    for item in response.output:
        # Primary: web_search_call.action.sources (requires include=["web_search_call.action.sources"])
        if item.type == "web_search_call":
            action = getattr(item, "action", None)
            for src in getattr(action, "sources", None) or []:
                url = getattr(src, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"url": url, "title": url})
        # Secondary: url_citation annotations on the text block (populated by some models)
        if item.type == "message":
            for block in item.content:
                if block.type != "output_text":
                    continue
                for ann in getattr(block, "annotations", []):
                    if ann.type == "url_citation" and ann.url not in seen:
                        seen.add(ann.url)
                        sources.append({"url": ann.url, "title": ann.title})
    logger.debug("_extract_api_sources: found %d sources", len(sources))
    return sources


_QUOTE_CHARS = '"\u201c\u201d\u2018\u2019\''


def _strip_excerpts(data: dict) -> dict:
    """Strip leading/trailing quote marks and extra whitespace from every excerpt."""
    for r in data.get("reports", []):
        r["excerpts"] = [e.strip(_QUOTE_CHARS).strip() for e in r.get("excerpts", [])]
    return data


def _call_ai_research(
    kind: str,
    payload: dict,
    prompt: str,
    schema: dict,
    domains: list[str],
    transform=None,
) -> tuple[dict | None, list[str]]:
    """Call OpenAI Responses API with web_search tool. Cache + single retry.

    Returns (result_dict, warnings). Never raises.
    The result includes api_sources, retrieved_at, model, and _cache_path.
    """
    warnings: list[str] = []
    prompt_version = extract_prompt_version(prompt)
    mdl = research_model()

    key = cache_key(kind, payload, prompt_version, mdl)
    cached = cache_read(key)
    if cached is not None:
        logger.debug("Cache hit for research key %.12s", key)
        cached["_cache_path"] = os.path.join(cache_dir(), f"{key}.json")
        return cached, warnings

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    if not api_key:
        logger.warning("_call_ai_research (%s): no API key configured", kind)
        return None, [_user_warning(kind, "no_key")]

    last_error = ""
    error_type = "api"

    for attempt in range(2):
        attempt_warnings: list[str] = []

        # ── Step 1: Responses API call with web_search ──────────────────
        try:
            response = client().responses.create(
                model=mdl,
                temperature=0,
                tools=[{
                    "type": "web_search",
                    "filters": {"allowed_domains": domains},
                }],
                tool_choice="required",
                include=["web_search_call.action.sources"],
                input=prompt,
            )
        except Exception as exc:
            error_type = "api"
            last_error = repr(exc)
            logger.warning(
                "_call_ai_research (%s) attempt %d API error: %s",
                kind, attempt + 1, last_error,
            )
            continue

        # ── Step 2: extract text content from response output ───────────
        output_types = [item.type for item in response.output]
        logger.debug("_call_ai_research (%s) attempt %d output item types: %s", kind, attempt + 1, output_types)
        raw_content = ""
        for item in response.output:
            if item.type == "message":
                for block in item.content:
                    if block.type == "output_text":
                        raw_content = block.text
                        break
                if raw_content:
                    break

        if not raw_content:
            error_type = "json"
            last_error = "No text content in response output"
            logger.warning(
                "_call_ai_research (%s) attempt %d: %s", kind, attempt + 1, last_error,
            )
            continue

        # ── Step 3: JSON parse (web search can't use json_object mode) ──
        logger.debug("_call_ai_research (%s) attempt %d raw: %.500s", kind, attempt + 1, raw_content)
        try:
            data = json.loads(extract_json(raw_content))
        except json.JSONDecodeError as exc:
            error_type = "json"
            last_error = repr(exc)
            logger.warning(
                "_call_ai_research (%s) attempt %d JSON parse error: %s | raw: %.2000s",
                kind, attempt + 1, last_error, raw_content,
            )
            continue

        # ── Step 4: optional transform then schema validation ───────────
        try:
            if transform is not None:
                data, attempt_warnings = transform(data)
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            error_type = "schema"
            last_error = exc.message
            logger.warning(
                "_call_ai_research (%s) attempt %d schema validation failed: %s",
                kind, attempt + 1, last_error,
            )
            continue
        except Exception as exc:
            error_type = "api"
            last_error = repr(exc)
            logger.warning(
                "_call_ai_research (%s) attempt %d unexpected error: %s",
                kind, attempt + 1, last_error,
            )
            continue

        # ── Success ─────────────────────────────────────────────────────
        try:
            api_sources = _extract_api_sources(response)
            if not api_sources:
                attempt_warnings.append(
                    "Research: web search returned no sources — results may not be grounded."
                )
            data = _add_report_ids(data)
            data = _strip_excerpts(data)
            data["api_sources"] = api_sources
            data["retrieved_at"] = datetime.now(timezone.utc).isoformat()
            data["model"] = mdl
            cache_write(key, data)
            data["_cache_path"] = os.path.join(cache_dir(), f"{key}.json")
            warnings.extend(attempt_warnings)
            return data, warnings
        except Exception as exc:
            error_type = "api"
            last_error = repr(exc)
            logger.warning(
                "_call_ai_research (%s) attempt %d success-block error: %s",
                kind, attempt + 1, last_error,
            )
            continue

    # Both attempts exhausted
    logger.error(
        "_call_ai_research (%s) exhausted all retries. error_type=%s last_error=%s",
        kind, error_type, last_error,
    )
    return None, [_user_warning(kind, error_type)]


def get_research(case_input: dict) -> tuple[dict | None, list[str]]:
    """Search trusted sites for published ML/TF typology reports.

    Only industry + country are sent. No client-specific data.
    Uses the OpenAI Responses API with the web_search tool, domain-restricted
    to the trusted_sources list in config/policy.json.

    Returns: (response_dict | None, warnings)
    """
    payload = _build_research_payload(case_input)

    policy = load_policy()
    domains = policy.get("trusted_sources", [])
    max_reports = policy.get("research_max_reports")
    max_excerpts = policy.get("research_max_excerpts_per_report")

    template = load_prompt("research.txt")
    prompt = (
        template
        .replace("{payload_json}", json.dumps(payload, indent=2))
        .replace("{trusted_sources}", ", ".join(domains))
        .replace("{max_reports}", str(max_reports))
        .replace("{max_excerpts}", str(max_excerpts))
    )
    schema = load_schema("research_response.schema.json")

    return _call_ai_research(
        "research", payload, prompt, schema, domains,
        transform=lambda data: _trim_reports(data, max_reports, max_excerpts),
    )


# ===========================================================================
# Forecasting Section (runs after the benchmark)
# ===========================================================================

AIClient = Any                      # an OpenAI client, or None (tests use a stand-in)
CheckFunction = Callable[[dict], list[str]]


# ============================================================================
# Forecasting settings (from .env)
# ============================================================================

FORECAST_MODEL = os.getenv("OPENAI_FORECAST_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
FORECAST_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0"))
WEB_SEARCH = os.getenv("FORECAST_WEB_SEARCH", "off").strip().lower() in ("on", "true", "1", "yes")
MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "16000"))
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 2
RATE_LIMIT_WAIT_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 600
MIN_EVENTS, MAX_EVENTS = 3, 6
MAX_CRIME_TYPES = int(os.getenv("MAX_CRIME_TYPES", "5"))   # crime types after the honest one
HONEST_CATEGORY = "HONEST"

# Free-text wrappers, so the model treats the text as data, not instructions
DECLARATION_OPEN, DECLARATION_CLOSE = "<<<BEGIN_DECLARATION>>>", "<<<END_DECLARATION>>>"
NOTES_OPEN, NOTES_CLOSE = "<<<BEGIN_OFFICER_NOTES>>>", "<<<END_OFFICER_NOTES>>>"

# Never sent to the AI: who he is, and every figure he claims (so the
# forecasts can't be anchored by his own story).
PERSONAL_KEYS = {"name", "full_name", "client_name", "nationality"}
CLAIMED_FIGURE_KEYS = {"current_declared_value", "income_produced", "declared_net_worth",
                       "expected_aum", "asset_composition", "current_wealth", "reviews"}
HONORIFICS = r"(?:Mr|Mrs|Ms|Miss|Mdm|Madam|Dr|Sir)\.?"


def init_client() -> AIClient:
    """Create the OpenAI client for forecasting. Returns None (and logs why) if it can't."""
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is not set, so forecasting AI requests will be skipped")
        return None
    try:
        # max_retries=0: call_ai decides when to retry, so the SDK doesn't burn quota on hidden retries
        return OpenAI(api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as error:
        logger.error("Could not create the OpenAI client: %s", error)
        return None


# ============================================================================
# Cleaning the input before it goes to the AI
# ============================================================================

def strip_keys(value: Any, keys: set) -> Any:
    """Remove the given keys from every dict inside value."""
    if isinstance(value, dict):
        return {k: strip_keys(v, keys) for k, v in value.items() if k.lower() not in keys}
    if isinstance(value, list):
        return [strip_keys(item, keys) for item in value]
    return value


def redact_names(text: str, names: list[str]) -> str:
    """Replace the client's name, including 'Mr Tan' style references, with 'the client'."""
    words = {w for name in names for w in re.split(r"\s+", name or "") if len(w) > 1}
    for word in sorted(words, key=len, reverse=True):
        pattern = re.escape(word)
        text = re.sub(rf"\b{HONORIFICS}\s+{pattern}\b", "the client", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{pattern}\b", "the client", text, flags=re.IGNORECASE)
    return text


def wrap_free_text(text: str, open_tag: str, close_tag: str) -> str:
    """Put free text between markers, removing any copies of the markers inside it."""
    cleaned = (text or "").replace(open_tag, "").replace(close_tag, "")
    return f"{open_tag}\n{cleaned.strip()}\n{close_tag}"


def client_facts(record: dict, include_declaration: bool = False) -> dict:
    """The facts the AI may see: jobs, assets (no claimed values), named countries, PEP status."""
    claims = record.get("claims", {})
    facts = {
        "current_date": record.get("current_date"),
        "career": record.get("career", {}),
        "career_history": [{"position_index": i, **job}
                           for i, job in enumerate(common.career_positions(record))],
        "assets": [{"asset_index": i, **asset} for i, asset in enumerate(record.get("investments", []))],
        "countries_named_by_client": claims.get("wealth_countries", []),
        "pep_status": claims.get("pep_status"),
    }
    if include_declaration:
        names = [str(record[key]) for key in PERSONAL_KEYS if record.get(key)]
        text = redact_names(record.get("sow_declaration", ""), names)
        facts["sow_declaration"] = wrap_free_text(text, DECLARATION_OPEN, DECLARATION_CLOSE)
    facts = strip_keys(facts, PERSONAL_KEYS)
    return strip_keys(facts, CLAIMED_FIGURE_KEYS)


# ============================================================================
# Prompt and schema files
# ============================================================================

def load_forecast_prompt(name: str) -> tuple[str, str]:
    """Read app/prompts/<name>.txt. Returns (version, text without the # comment lines)."""
    path = os.path.join(common.PROMPT_DIR, f"{name}.txt")
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    version = name
    for line in lines:
        if line.startswith("# prompt_version:"):
            version = line.split(":", 1)[1].strip()
    text = "\n".join(line for line in lines if not line.startswith("#")).strip()
    return version, text


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
    shared = {"currency": common.REPORTING_CURRENCY,
              "declaration_open": DECLARATION_OPEN, "declaration_close": DECLARATION_CLOSE,
              "notes_open": NOTES_OPEN, "notes_close": NOTES_CLOSE}
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
        path = os.path.join(common.SCHEMA_DIR, f"{name}.schema.json")
        with open(path, "r", encoding="utf-8") as handle:
            _schema_cache[name] = _add_vocabulary(json.load(handle))
    return _schema_cache[name]


# ============================================================================
# Checking the AI's reply
# ============================================================================

def schema_errors(data: Any, schema: dict) -> list[str]:
    errors = []
    for error in Draft202012Validator(schema).iter_errors(data):
        where = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{where}: {error.message}")
    return errors


def range_errors(value: Any, where: str = "") -> list[str]:
    """Every {low, typical, high} range must be in order."""
    errors = []
    if isinstance(value, dict):
        if set(value) == set(common.RANGE_KEYS):
            if not value["low"] <= value["typical"] <= value["high"]:
                errors.append(f"{where or '<root>'}: range out of order {value}")
            return errors
        for key, item in value.items():
            errors += range_errors(item, f"{where}/{key}" if where else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors += range_errors(item, f"{where}/{index}")
    return errors


def parse_reply(raw: str | None) -> tuple[dict | None, str | None]:
    """Pull the JSON object out of the model's reply. Returns (data, error)."""
    if not raw or not raw.strip():
        return None, "empty reply (possible refusal or cut-off)"
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text), None
    except json.JSONDecodeError as error:
        return None, f"invalid JSON: {error}"


def validate_reply(raw: str | None, schema_name: str,
                   check: CheckFunction | None = None) -> tuple[dict | None, list[str]]:
    """Parse, then check the schema, then the ranges, then the piece's own rules."""
    data, error = parse_reply(raw)
    if error:
        return None, [error]
    errors = schema_errors(data, load_forecast_schema(schema_name))
    if not errors:
        errors = range_errors(data)
    if not errors and check is not None:
        errors = check(data)
    return (data if not errors else None), errors


def _years_errors(label: str, found: list[int], expected: list[int]) -> list[str]:
    """The years must be exactly the expected ones, once each."""
    errors = []
    missing = [year for year in expected if year not in found]
    repeated = sorted({year for year in found if found.count(year) > 1})
    unexpected = sorted({year for year in found if year not in expected})
    if missing:
        errors.append(f"{label}: missing years {missing}")
    if repeated:
        errors.append(f"{label}: repeated years {repeated}")
    if unexpected:
        errors.append(f"{label}: unexpected years {unexpected}")
    return errors


def _unknown_ids(label: str, used: list, known: set) -> list[str]:
    unknown = sorted(set(used) - known)
    return [f"{label} uses unknown ids {unknown}"] if unknown else []


# ---- One check per kind of piece ------------------------------------------

def check_declaration(data: dict, job_count: int, asset_count: int) -> list[str]:
    errors = []
    ids = [s["source_id"] for s in data["claimed_sources"]]
    if not ids:
        errors.append("at least one claimed source is required")
    if len(ids) != len(set(ids)):
        errors.append("source_id values must be unique")
    for source in data["claimed_sources"]:
        job, asset = source["linked_position_index"], source["linked_asset_index"]
        if job is not None and not 0 <= job < job_count:
            errors.append(f"{source['source_id']}: linked_position_index {job} doesn't exist")
        if asset is not None and not 0 <= asset < asset_count:
            errors.append(f"{source['source_id']}: linked_asset_index {asset} doesn't exist")
        if source["start_year"] and source["end_year"] and source["start_year"] > source["end_year"]:
            errors.append(f"{source['source_id']}: start_year is after end_year")
    return errors


def check_markets_list(data: dict, forecast_years: list[int]) -> list[str]:
    ids = [m["market_id"] for m in data["relevant_markets"]]
    errors = [] if ids else ["at least one market is required"]
    if len(ids) != len(set(ids)):
        errors.append("market_id values must be unique")
    errors += [f"market_id '{i}' may only use letters, digits and _" for i in ids
               if not re.fullmatch(r"[A-Za-z0-9_]+", i)]
    chances = data["scenario_chances"]
    errors += _years_errors("scenario_chances", [c["year"] for c in chances], forecast_years)
    for c in chances:
        total = c["steady"] + c["boom"] + c["downturn"]
        if not 95 <= total <= 105 or min(c["steady"], c["boom"], c["downturn"]) < 0:
            errors.append(f"scenario_chances {c['year']}: chances must be 0-100 and add up to 100")
    return errors


def check_market_series(data: dict, market_id: str, history_years: list[int],
                        forecast_years: list[int]) -> list[str]:
    errors = [] if data["market_id"] == market_id else [f"market_id must be '{market_id}'"]
    errors += _years_errors(f"{market_id} history", [h["year"] for h in data["history"]], history_years)
    errors += _years_errors(f"{market_id} outlook", [o["year"] for o in data["outlook"]], forecast_years)
    return errors


def check_position_pay(data: dict, index: int, years: list[int]) -> list[str]:
    errors = [] if data["position_index"] == index else [f"position_index must be {index}"]
    errors += _years_errors(f"job {index}", [row["year"] for row in data["yearly"]], years)
    return errors


def check_future_pay(data: dict, forecast_years: list[int]) -> list[str]:
    return _years_errors("future pay", [row["year"] for row in data["yearly"]], forecast_years)


def check_asset_model(data: dict, index: int, years: list[int], market_ids: set) -> list[str]:
    errors = [] if data["asset_index"] == index else [f"asset_index must be {index}"]
    if not data["exposures"]:
        errors.append("at least one market exposure is required")
    errors += _unknown_ids("exposures", [e["market_id"] for e in data["exposures"]], market_ids)
    errors += _years_errors(f"asset {index} income_yield_by_year",
                            [row["year"] for row in data["income_yield_by_year"]], years)
    return errors


def check_person_types(data: dict, record: dict, source_records: list[dict]) -> list[str]:
    """
    T1 is the honest person. Every other type is ONE standard crime category from the
    vocabulary, tied to the job, asset or declared source that makes it possible for him,
    and open only in the years that job/asset/source existed.
    """
    types = data["person_types"]
    ids = [t["type_id"] for t in types]
    start, end = common.career_years(record)
    last_forecast_year = common.future_years(record)[-1]
    jobs = common.career_positions(record)
    sources = {s["source_id"]: s for s in source_records}
    assets = record.get("investments", [])
    errors = []

    if not ids or ids[0] != common.BASELINE_TYPE:
        errors.append(f"the first type must be {common.BASELINE_TYPE} (wealth exactly as declared)")
    if len(ids) < 2:
        errors.append("at least one crime type besides the honest one is required")
    if len(ids) - 1 > MAX_CRIME_TYPES:
        errors.append(f"at most {MAX_CRIME_TYPES} crime types, ranked most relevant first; got {len(ids) - 1}")
    if len(ids) != len(set(ids)):
        errors.append("type_id values must be unique")

    categories = [t["category"] for t in types]
    if types and categories[0] != HONEST_CATEGORY:
        errors.append(f"{common.BASELINE_TYPE} must use category {HONEST_CATEGORY}")
    if HONEST_CATEGORY in categories[1:]:
        errors.append(f"only {common.BASELINE_TYPE} may use category {HONEST_CATEGORY}")
    crime_categories = categories[1:]
    if len(crime_categories) != len(set(crime_categories)):
        errors.append("each crime category may appear only once")

    for person_type in types:
        type_id = person_type["type_id"]
        first, last = person_type["open_from"], person_type["open_to"]
        if not start <= first <= last <= last_forecast_year:
            errors.append(f"{type_id}: open years must sit inside {start}-{last_forecast_year}, "
                          f"open_from <= open_to (got {first}-{last})")
        for document in person_type["rule_out_documents"]:
            errors += _unknown_ids(f"{type_id} also_rules_out", document["also_rules_out"], set(ids))
        if type_id == common.BASELINE_TYPE:
            # The honest type covers his whole career and every forecast year.
            if (first, last) != (start, last_forecast_year):
                errors.append(f"{type_id}: open_from/open_to must be {start}-{last_forecast_year}")
            continue

        # What makes this crime possible for HIM. At least one link, and the years must fit it.
        job_index = person_type["enabled_by_position_index"]
        asset_index = person_type["enabled_by_asset_index"]
        source_id = person_type["enabled_by_source_id"]
        if job_index is None and asset_index is None and source_id is None:
            errors.append(f"{type_id}: must name the job, asset or declared source that makes it possible")
        if job_index is not None:
            if not 0 <= job_index < len(jobs):
                errors.append(f"{type_id}: enabled_by_position_index {job_index} doesn't exist")
            else:
                job = jobs[job_index]
                job_first = int(job["start_year"])
                # A job he still holds can carry the crime into the forecast years; a past job can't.
                job_last = last_forecast_year if not job.get("end_year") else common.position_end(job, end)
                if not job_first <= first <= job_last:
                    errors.append(f"{type_id}: open_from {first} must fall while job {job_index} was held "
                                  f"({job_first}-{job_last})")
                elif asset_index is None and source_id is None and last > job_last:
                    errors.append(f"{type_id}: open_to {last} is after job {job_index} ended ({job_last}); "
                                  f"a crime that only this job enables must stop when the job does")
        if asset_index is not None:
            if not 0 <= asset_index < len(assets):
                errors.append(f"{type_id}: enabled_by_asset_index {asset_index} doesn't exist")
            elif first < int(assets[asset_index]["year_acquired"]):
                errors.append(f"{type_id}: open_from {first} is before asset {asset_index} was bought "
                              f"({assets[asset_index]['year_acquired']})")
        if source_id is not None:
            if source_id not in sources:
                errors.append(f"{type_id}: enabled_by_source_id '{source_id}' is not a declared source")
            elif sources[source_id].get("start_year") and first < int(sources[source_id]["start_year"]):
                errors.append(f"{type_id}: open_from {first} is before declared source {source_id} "
                              f"started ({sources[source_id]['start_year']})")
    return errors


def check_type_behaviour(data: dict, type_id: str, start: int, end: int,
                         forecast_years: list[int], market_ids: set,
                         open_years: tuple[int, int] | None = None) -> list[str]:
    errors = [] if data["type_id"] == type_id else [f"type_id must be '{type_id}'"]
    first, last = data["route_open_from"], data["route_open_to"]
    if not start <= first <= last <= forecast_years[-1]:
        errors.append(f"route years must sit inside {start}-{forecast_years[-1]}, first <= last")
    if open_years is not None and (first, last) != tuple(open_years):
        errors.append(f"route years must be {open_years[0]}-{open_years[1]}, the years given in the type list")
    history_years = list(range(first, min(last, end) + 1)) if first <= end else []
    errors += _years_errors("extra_money_history", [h["year"] for h in data["extra_money_history"]],
                            history_years)
    errors += _years_errors("extra_money_forecast", [f["year"] for f in data["extra_money_forecast"]],
                            forecast_years)
    errors += _unknown_ids("linked_market_ids", data["linked_market_ids"], market_ids)
    if not data["signals"]:
        errors.append("at least one signal is required")
    for signal in data["signals"]:
        if signal["first_year"] > signal["last_year"]:
            errors.append(f"{signal['signal_code']}: first_year is after last_year")
    return errors


def check_events(data: dict, forecast_years: list[int], market_ids: set) -> list[str]:
    events = data["events"]
    ids = [e["event_id"] for e in events]
    errors = []
    if not MIN_EVENTS <= len(events) <= MAX_EVENTS:
        errors.append(f"expected {MIN_EVENTS}-{MAX_EVENTS} events, got {len(events)}")
    if len(ids) != len(set(ids)):
        errors.append("event_id values must be unique")
    for event in events:
        if event["year"] not in forecast_years:
            errors.append(f"{event['event_id']}: year must be one of {forecast_years}")
        if not event["shocks"]:
            errors.append(f"{event['event_id']}: needs at least one market shock")
        errors += _unknown_ids(event["event_id"], [s["market_id"] for s in event["shocks"]], market_ids)
    return errors


def check_type_reactions(data: dict, type_id: str, event_ids: list[str]) -> list[str]:
    errors = [] if data["type_id"] == type_id else [f"type_id must be '{type_id}'"]
    found = sorted(r["event_id"] for r in data["reactions"])
    if found != sorted(event_ids):
        errors.append(f"reactions need exactly one entry per event {event_ids}, got {found}")
    return errors


def check_review(data: dict, market_ids: set, event_ids: set) -> list[str]:
    errors = _unknown_ids("market_moves", [m["market_id"] for m in data["market_moves"]], market_ids)
    errors += _unknown_ids("events_seen", data["events_seen"], event_ids)
    errors += [f"inflow {i['signal_code']}: times_received must be at least 1"
               for i in data["inflows"] if i["times_received"] < 1]
    return errors


# ============================================================================
# Sending one request: retry, validate, log, never crash
# ============================================================================

def _empty_result(call_name: str, prompt_version: str) -> dict:
    return {"call": call_name, "ok": False, "data": None, "raw": None, "attempts": 0,
            "from_cache": False, "error": None, "prompt_version": prompt_version}


def _status_code(error: Exception) -> int | None:
    """The HTTP status of an SDK error, if it has one."""
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
    return None


def _build_request(instructions: str, user_text: str, schema: dict,
                   web_search: bool, full_options: bool) -> dict:
    """One OpenAI Responses API request. The schema goes in the instructions and the
    reply is checked locally, because our schemas use features strict JSON mode rejects."""
    request = {"model": FORECAST_MODEL, "input": user_text, "max_output_tokens": MAX_OUTPUT_TOKENS,
               "store": False,
               "instructions": instructions + "\n\nYour answer must be ONE JSON object matching this schema:\n"
                               + json.dumps(schema, separators=(",", ":"))}
    if web_search:
        request["tools"] = [{"type": "web_search"}]       # web search can't be combined with JSON mode
    elif full_options:
        request["text"] = {"format": {"type": "json_object"}}
    if full_options:
        request["temperature"] = FORECAST_TEMPERATURE      # some models reject this; dropped on a 400
    return request


def call_ai(client: AIClient, call_name: str, prompt_name: str, values: dict, user_data: dict,
            schema_name: str, check: CheckFunction | None = None,
            cached_raw: str | None = None, web_search: bool = False) -> dict:
    """Send one request and return a checked result. Never raises."""
    try:
        version, instructions = build_instructions(prompt_name, values, web_search)
        schema = load_forecast_schema(schema_name)
    except (OSError, ValueError) as error:  # a missing or broken prompt/schema file
        result = _empty_result(call_name, prompt_name)
        result["error"] = f"could not prepare the request: {error}"
        logger.error("%s: %s", call_name, result["error"])
        return result
    result = _empty_result(call_name, version)

    # A saved reply for this request: reuse it so reruns are identical and free
    if cached_raw:
        data, errors = validate_reply(cached_raw, schema_name, check)
        if not errors:
            result.update(ok=True, data=data, raw=cached_raw, from_cache=True)
            logger.info("%s: using saved reply", call_name)
            return result
        logger.warning("%s: saved reply no longer valid (%s), asking again", call_name, errors[:2])

    if client is None:
        result["error"] = "no AI client available (is OPENAI_API_KEY set?)"
        logger.error("%s: %s", call_name, result["error"])
        return result

    user_text = json.dumps(user_data, indent=2, ensure_ascii=False)
    full_options = True
    rate_limited = False
    last_errors: list[str] = []

    for attempt in range(1, MAX_RETRIES + 1):
        result["attempts"] = attempt
        message = user_text
        if last_errors:
            # The same prompt with a fixed seed would repeat the mistake, so say what to fix
            message += ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED because:\n- " + "\n- ".join(last_errors[:10])
                        + "\nReturn a complete, corrected JSON object.")
        request = _build_request(instructions, message, schema, web_search, full_options)
        try:
            reply = client.responses.create(**request)
        except Exception as error:
            status = _status_code(error)
            result["error"] = f"API error {status}: {error}"
            if status == 400 and full_options:
                logger.warning("%s: request options rejected by the model, retrying without JSON mode "
                               "and temperature", call_name)
                full_options = False
                continue
            if status in (400, 401, 403, 404):
                logger.error("%s: %s (not retrying - check the API key and model name)", call_name, error)
                return result
            if status == 429:
                if rate_limited:
                    result["error"] = "quota used up (429); see https://ai.dev/rate-limit"
                    logger.error("%s: %s", call_name, result["error"])
                    return result
                rate_limited = True
                logger.warning("%s: rate limited, waiting %ds", call_name, RATE_LIMIT_WAIT_SECONDS)
                time.sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            logger.warning("%s: attempt %d failed: %s", call_name, attempt, error)
            time.sleep(RETRY_WAIT_SECONDS * attempt)
            continue

        status = getattr(reply, "status", None)
        if status not in (None, "completed"):         # e.g. "incomplete" when it ran out of tokens
            result["error"] = f"request ended with status '{status}'"
            logger.warning("%s: attempt %d: %s", call_name, attempt, result["error"])
            continue

        raw = getattr(reply, "output_text", None) or ""
        data, errors = validate_reply(raw, schema_name, check)
        if not errors:
            result.update(ok=True, data=data, raw=raw, error=None)
            logger.info("%s: valid reply on attempt %d", call_name, attempt)
            return result
        result["raw"] = raw
        last_errors = errors
        result["error"] = "reply failed checks: " + "; ".join(errors[:5])
        logger.warning("%s: attempt %d rejected: %s", call_name, attempt, errors[:3])

    logger.error("%s: giving up after %d attempts (%s)", call_name, MAX_RETRIES, result["error"])
    return result


# ============================================================================
# The pipeline
# ============================================================================

def _years_text(years: list[int]) -> str:
    return f"{years[0]}-{years[-1]}" if years else "none"


def _markets_brief(markets: dict) -> list[dict]:
    return [{k: m[k] for k in ("market_id", "name", "indicator")} for m in markets["relevant_markets"]]


def _market_changes(markets: dict, first: int, last: int) -> dict:
    """{market_id: {year: change}} for the years a piece needs."""
    return {market_id: {h["year"]: h["change_pct"] for h in series["history"] if first <= h["year"] <= last}
            for market_id, series in markets["series"].items()}


def _market_outlooks(markets: dict) -> dict:
    return {market_id: series["outlook"] for market_id, series in markets["series"].items()}


def _types_brief(types: dict) -> list[dict]:
    return [{k: t[k] for k in ("type_id", "category", "name", "route")} for t in types["person_types"]]


def _crime_categories() -> list[dict]:
    """The fixed list of crime categories the AI must choose from (config vocabulary)."""
    return [{"category": code, "description": text}
            for code, text in common.vocabulary().get("crime_categories", {}).items()]


def read_declaration(client: AIClient, record: dict, cache: dict, results: dict) -> dict | None:
    """Step 0: free-text declaration -> structured list of claimed sources."""
    jobs, assets = len(common.career_positions(record)), len(record.get("investments", []))
    result = call_ai(client, "step0_declaration", "declaration", {},
                     client_facts(record, include_declaration=True), "declaration",
                     lambda data: check_declaration(data, jobs, assets), cache.get("step0_declaration"))
    results[result["call"]] = result
    return result["data"] if result["ok"] else None


def forecast_markets(client: AIClient, record: dict, declaration: dict,
                     cache: dict, results: dict) -> dict | None:
    """Call 1: choose the markets, then recall and forecast each one."""
    start, end = common.career_years(record)
    years = common.future_years(record)
    values = {"start_year": start, "end_year": end, "forecast_years": _years_text(years),
              "last_forecast_year": years[-1]}

    name = "call1_markets.list"
    listing = call_ai(client, name, "markets_list", values,
                      {"client": client_facts(record), "claimed_sources": declaration["claimed_sources"]},
                      "markets_list", lambda data: check_markets_list(data, years),
                      cache.get(name), WEB_SEARCH)
    results[name] = listing
    if not listing["ok"]:
        return None

    markets = {**listing["data"], "series": {}}
    for market in listing["data"]["relevant_markets"]:
        market_id = market["market_id"]
        name = f"call1_markets.series.{market_id}"
        piece = call_ai(client, name, "market_series",
                        {**values, "market_id": market_id, "market_name": market["name"],
                         "indicator": market["indicator"]},
                        {"market": market, "client": client_facts(record)}, "market_series",
                        lambda data, m=market_id: check_market_series(data, m, list(range(start, end + 1)), years),
                        cache.get(name), WEB_SEARCH)
        results[name] = piece
        if not piece["ok"]:
            return None
        markets["series"][market_id] = piece["data"]
    return markets


def forecast_earnings(client: AIClient, record: dict, markets: dict,
                      cache: dict, results: dict) -> dict | None:
    """Call 2: expected pay for every job, future pay, and how each asset behaves."""
    _, end = common.career_years(record)
    years = common.future_years(record)
    market_ids = set(markets["series"])
    earnings = {"positions": [], "assets": []}
    earlier_pay = []

    for index, job in enumerate(common.career_positions(record)):
        first, last = int(job["start_year"]), common.position_end(job, end)
        name = f"call2_earnings.position.{index}"
        piece = call_ai(client, name, "position_pay",
                        {"position_index": index, "first_year": first, "last_year": last},
                        {"job": {"position_index": index, **job}, "earlier_pay_typical": earlier_pay,
                         "markets": _markets_brief(markets),
                         "market_changes": _market_changes(markets, first, last)},
                        "position_pay",
                        lambda data, i=index, y=list(range(first, last + 1)): check_position_pay(data, i, y),
                        cache.get(name))
        results[name] = piece
        if not piece["ok"]:
            return None
        earnings["positions"].append(piece["data"])
        earlier_pay += [{"year": row["year"], "base_pay": row["base_pay"]["typical"],
                         "bonus": row["bonus"]["typical"]} for row in piece["data"]["yearly"]]

    name = "call2_earnings.future_pay"
    piece = call_ai(client, name, "future_pay", {"forecast_years": _years_text(years)},
                    {"recent_pay_typical": earlier_pay[-3:], "client": client_facts(record),
                     "markets": _markets_brief(markets), "market_outlooks": _market_outlooks(markets)},
                    "future_pay", lambda data: check_future_pay(data, years), cache.get(name))
    results[name] = piece
    if not piece["ok"]:
        return None
    earnings["future_pay"] = piece["data"]

    for index, asset in enumerate(record.get("investments", [])):
        bought = int(asset["year_acquired"])
        name = f"call2_earnings.asset.{index}"
        piece = call_ai(client, name, "asset_model",
                        {"asset_index": index, "bought_year": bought, "end_year": end,
                         "forecast_years": _years_text(years)},
                        {"asset": strip_keys({"asset_index": index, **asset}, CLAIMED_FIGURE_KEYS),
                         "markets": _markets_brief(markets),
                         "market_changes": _market_changes(markets, bought, end)},
                        "asset_model",
                        lambda data, i=index, y=list(range(bought, end + 1)): check_asset_model(data, i, y, market_ids),
                        cache.get(name))
        results[name] = piece
        if not piece["ok"]:
            return None
        earnings["assets"].append(piece["data"])
    return earnings


def forecast_types(client: AIClient, record: dict, declaration: dict, markets: dict,
                   earnings: dict, cache: dict, results: dict) -> dict | None:
    """
    Call 3: T1 (honest) plus the standard crime categories his jobs, assets and declared
    sources make possible, each tied to what enables it; then one behaviour forecast per type.
    """
    start, end = common.career_years(record)
    years = common.future_years(record)
    values = {"start_year": start, "end_year": end, "forecast_years": _years_text(years),
              "last_forecast_year": years[-1], "baseline_type": common.BASELINE_TYPE,
              "honest_category": HONEST_CATEGORY, "max_crime_types": MAX_CRIME_TYPES}
    claimed_sources = declaration["claimed_sources"]

    name = "call3_types.list"
    listing = call_ai(client, name, "person_types", values,
                      {"client": client_facts(record, include_declaration=True),
                       "claimed_sources": declaration["claimed_sources"],
                       "markets": _markets_brief(markets),
                       "career_outlook": earnings["future_pay"]["career_outlook"],
                       "crime_categories": _crime_categories()},
                      "person_types", lambda data: check_person_types(data, record, claimed_sources),
                      cache.get(name))
    results[name] = listing
    if not listing["ok"]:
        return None

    types = {"person_types": listing["data"]["person_types"], "behaviour": {}}
    for person_type in types["person_types"]:
        type_id = person_type["type_id"]
        if type_id == common.BASELINE_TYPE:
            baseline_note = "This is the as-declared type, so its extra money is 0 every year."
        else:
            baseline_note = ("Give only the money this crime ADDS on top of his declared sources, and "
                             "only in the years it was open. It must come through the job, asset or "
                             "source named in enabled_by, at the size that channel could realistically carry.")
        open_years = (person_type["open_from"], person_type["open_to"])
        name = f"call3_types.behaviour.{type_id}"
        piece = call_ai(client, name, "type_behaviour",
                        {**values, "type_id": type_id, "type_name": person_type["name"],
                         "baseline_note": baseline_note},
                        {"this_type": person_type, "all_types": _types_brief(types),
                         "claimed_sources": declaration["claimed_sources"],
                         "markets": _markets_brief(markets), "client": client_facts(record)},
                        "type_behaviour",
                        lambda data, t=type_id, o=open_years: check_type_behaviour(
                            data, t, start, end, years, set(markets["series"]), o),
                        cache.get(name))
        results[name] = piece
        if not piece["ok"]:
            return None
        types["behaviour"][type_id] = piece["data"]
    return types


def forecast_events(client: AIClient, record: dict, markets: dict, types: dict,
                    cache: dict, results: dict) -> dict | None:
    """Call 4: specific future events, then how each type would react to them."""
    years = common.future_years(record)
    name = "call4_events.list"
    listing = call_ai(client, name, "events",
                      {"forecast_years": _years_text(years), "min_events": MIN_EVENTS, "max_events": MAX_EVENTS},
                      {"markets": _markets_brief(markets), "market_outlooks": _market_outlooks(markets),
                       "person_types": _types_brief(types), "client": client_facts(record)},
                      "events", lambda data: check_events(data, years, set(markets["series"])),
                      cache.get(name))
    results[name] = listing
    if not listing["ok"]:
        return None

    events = {"events": listing["data"]["events"], "reactions": {}}
    event_ids = [e["event_id"] for e in events["events"]]
    for person_type in types["person_types"]:
        type_id = person_type["type_id"]
        name = f"call4_events.reactions.{type_id}"
        piece = call_ai(client, name, "type_reactions",
                        {"type_id": type_id, "type_name": person_type["name"], "event_ids": ", ".join(event_ids)},
                        {"this_type": person_type, "its_signals": types["behaviour"][type_id]["signals"],
                         "events": events["events"]},
                        "type_reactions", lambda data, t=type_id: check_type_reactions(data, t, event_ids),
                        cache.get(name))
        results[name] = piece
        if not piece["ok"]:
            return None
        events["reactions"][type_id] = piece["data"]
    return events


def run_onboarding_forecast(client: AIClient, record: dict, cache: dict | None = None) -> dict:
    """
    Run step 0 and calls 1-4 in order, stopping at the first failure.

    cache: {piece_name: raw_text} of saved replies (from data_manager).
    Returns {"ok", "failed_step", "results": {piece: result}, "forecast": {...}}.
    Finished pieces are in "results" even after a failure, so a rerun resumes there.
    """
    cache = cache or {}
    results: dict = {}
    run = {"ok": False, "failed_step": None, "results": results, "forecast": {}}

    declaration = read_declaration(client, record, cache, results)
    if declaration is None:
        run["failed_step"] = "step0_declaration"
        return run
    markets = forecast_markets(client, record, declaration, cache, results)
    if markets is None:
        run["failed_step"] = "call1_markets"
        return run
    earnings = forecast_earnings(client, record, markets, cache, results)
    if earnings is None:
        run["failed_step"] = "call2_earnings"
        return run
    types = forecast_types(client, record, declaration, markets, earnings, cache, results)
    if types is None:
        run["failed_step"] = "call3_types"
        return run
    events = forecast_events(client, record, markets, types, cache, results)
    if events is None:
        run["failed_step"] = "call4_events"
        return run

    run["forecast"] = {
        "declaration": declaration,
        "markets": markets,
        "earnings": earnings,
        "types": types,
        "events": events,
        "prompt_versions": {name: result["prompt_version"] for name, result in results.items()},
    }
    run["ok"] = True
    return run


def read_review_notes(client: AIClient, review: dict, forecast: dict, cache: dict | None = None) -> dict:
    """Review: officer notes -> structured payments, what markets did, which events happened."""
    name = f"review.{review.get('current_date', 'undated')}"
    markets = forecast["markets"]
    events = forecast["events"]["events"]
    user_data = {
        "officer_notes": wrap_free_text(review.get("officer_notes", ""), NOTES_OPEN, NOTES_CLOSE),
        "markets": _markets_brief(markets),
        "forecast_events": [{k: e[k] for k in ("event_id", "name", "year")} for e in events],
    }
    market_ids = set(markets["series"])
    event_ids = {e["event_id"] for e in events}
    return call_ai(client, name, "review", {"review_date": review.get("current_date")}, user_data, "review",
                   lambda data: check_review(data, market_ids, event_ids), (cache or {}).get(name))


# ---------------------------------------------------------------------------
# Quick manual test of the benchmark call:  python -m app.ai_manager
# ---------------------------------------------------------------------------

# Test the AI
if __name__ == "__main__":

    # Sample client information
    client_data = {
    "client_id": "CID1001",
    "age": 28,
    "nationality": "Singaporean",
    "country_of_residence": "Singapore",
    "occupation": "Software Engineer",
    "career_start_year": 2020,
    "employment_years": 6,
    "properties": "1 condominium",
    "investment": "Stocks and ETFs",
    "industry": "Information Technology"
}

    ai_data = get_ai_data(client_data)
    print(json.dumps(ai_data, indent=4))