"""AI calls and AI boundary enforcement."""

import json
import logging
import os

import jsonschema
from openai import OpenAI

from app.utilities import cache_key, cache_read, cache_write, extract_prompt_version

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BENCHMARK_ALLOWED_FIELDS = ["occupation", "industry", "age", "career_start_year", "country"]


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


def _call_ai(
    kind: str,
    payload: dict,
    prompt: str,
    schema: dict,
) -> tuple[dict | None, list[str]]:
    """Call OpenAI with caching and a single retry on failure.

    Returns (result_dict, warnings). Never raises.

    - Cache hit  → returns immediately, no API call.
    - No API key → returns (None, [warning]).
    - API error or schema validation failure → retries once.
    - Two failures → returns (None, warnings).

    The returned dict includes a "model" key recording which model responded.
    """
    warnings: list[str] = []
    prompt_version = extract_prompt_version(prompt)
    model = _model()

    key = cache_key(kind, payload, prompt_version, model)
    cached = cache_read(key)
    if cached is not None:
        logger.debug("Cache hit for key %.12s", key)
        return cached, warnings

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY")
    if not api_key:
        return None, ["No OPENAI_API_KEY set and no cached response available."]

    last_error = ""
    for attempt in range(2):
        try:
            response = _client().chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(response.choices[0].message.content)
            jsonschema.validate(instance=data, schema=schema)
            data["model"] = model
            cache_write(key, data)
            return data, warnings
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                msg = f"Attempt 1 failed — {last_error}. Retrying."
                warnings.append(msg)
                logger.warning("_call_ai attempt 1 failed: %s", last_error)

    final = f"AI call failed after 2 attempts. Last error: {last_error}"
    warnings.append(final)
    logger.error(final)
    return None, warnings


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

    return _call_ai("typology", payload, prompt, schema)


def get_declaration(case_input: dict) -> tuple[dict | None, list[str]]:
    """Call AI to parse the free-text declaration into structured sources.

    Returns: (response_dict | None, warnings)
    """
    declaration_text = case_input.get("declaration_text", "")

    template = _load_prompt("declaration.txt")
    prompt = template.replace("{declaration_text}", declaration_text)
    schema = _load_schema("declaration.schema.json")

    return _call_ai("declaration", {"declaration_text": declaration_text}, prompt, schema)
