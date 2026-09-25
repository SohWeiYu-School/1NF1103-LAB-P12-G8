"""AI calls and AI boundary enforcement."""

import json
import os

import jsonschema
from openai import OpenAI

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
    return OpenAI(api_key=os.getenv("OPEN_AI_API_KEY"))


def _model() -> str:
    model = os.getenv("AI_MODEL", "gpt-4o")
    if "claude" in model.lower() and not os.getenv("ANTHROPIC_API_KEY"):
        return "gpt-4o"
    return model


def _call_ai(prompt: str, schema: dict) -> dict:
    max_retries = int(os.getenv("AI_MAX_RETRIES", "1"))
    temperature = float(os.getenv("AI_TEMPERATURE", "0"))

    for attempt in range(max_retries + 1):
        response = _client().chat.completions.create(
            model=_model(),
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.choices[0].message.content)
        try:
            jsonschema.validate(instance=data, schema=schema)
            return data
        except jsonschema.ValidationError as exc:
            if attempt == max_retries:
                raise RuntimeError(
                    f"AI response failed schema validation after {attempt + 1} attempt(s): {exc.message}"
                ) from exc
    raise RuntimeError("unreachable")


def get_typologies(case_input: dict) -> dict:
    """Call AI for sector abuse typologies only.

    Only the fields in BENCHMARK_ALLOWED_FIELDS are sent to the AI.
    The client's declared figures are never included.
    Returns: {sector_typologies, expected_jurisdictions, max_accumulation_per_year_sgd}
    """
    payload = {k: case_input[k] for k in BENCHMARK_ALLOWED_FIELDS if k in case_input}
    if "country" not in payload:
        payload["country"] = case_input.get("country_of_residence", "Singapore")

    template = _load_prompt("typology.txt")
    prompt = template.replace("{payload_json}", json.dumps(payload, indent=2))
    schema = _load_schema("typology_response.schema.json")

    return _call_ai(prompt, schema)


def get_declaration(case_input: dict) -> dict:
    """Call AI to parse the free-text declaration into structured sources."""
    declaration_text = case_input.get("declaration_text", "")

    template = _load_prompt("declaration.txt")
    prompt = template.replace("{declaration_text}", declaration_text)
    schema = _load_schema("declaration.schema.json")

    return _call_ai(prompt, schema)
