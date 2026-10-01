"""AI calls and AI boundary enforcement."""

import json
import logging
import os

import jsonschema
from openai import OpenAI

from app.utilities import cache_dir, cache_key, cache_read, cache_write, extract_prompt_version

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BENCHMARK_ALLOWED_FIELDS = ["occupation", "industry", "age", "career_start_year", "country"]

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
}


def _load_prompt(name: str) -> str:
    path = os.path.join(_BASE, "app", "prompts", name)
    with open(path) as f:
        return f.read()


def _load_schema(name: str) -> dict:
    path = os.path.join(_BASE, "app", "schemas", name)
    with open(path) as f:
        return json.load(f)


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    return OpenAI(api_key=api_key)


def _model() -> str:
    return os.getenv("OPENAI_MODEL", os.getenv("AI_MODEL", "gpt-4o"))


def _user_warning(kind: str, error_type: str) -> str:
    """Build the single short line shown to the user when an AI call fails."""
    display = _KIND_DISPLAY.get(kind, kind.capitalize())
    msg = _USER_MESSAGES.get(error_type, "An unexpected error occurred.")
    return f"{display}: {msg} This case needs manual review. Details: logs/app.log"


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
    model = _model()

    key = cache_key(kind, payload, prompt_version, model)
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
            response = _client().chat.completions.create(
                model=model,
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
        data["model"] = model
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

    template = _load_prompt("typology.txt")
    prompt = template.replace("{payload_json}", json.dumps(payload, indent=2))
    schema = _load_schema("typology_response.schema.json")

    return _call_ai("typology", payload, prompt, schema, transform=_trim_typologies)


def get_declaration(case_input: dict) -> tuple[dict | None, list[str]]:
    """Call AI to parse the free-text declaration into structured sources.

    Returns: (response_dict | None, warnings)
    """
    declaration_text = case_input.get("declaration_text", "")

    template = _load_prompt("declaration.txt")
    prompt = template.replace("{declaration_text}", declaration_text)
    schema = _load_schema("declaration.schema.json")

    return _call_ai("declaration", {"declaration_text": declaration_text}, prompt, schema)
