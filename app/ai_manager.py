from openai import OpenAI
# from anthropic import Anthropic
from dotenv import load_dotenv
import os
import json

load_dotenv() #loads your secret/environment variables from the .env file

openAi_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
#claude_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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
        response = openAi_client.responses.create(
            model="gpt-4.1-nano",
            input=prompt
        )
        
        # Return the AI's response
        return response.output_text

    except Exception as error:
        handle_ai_failure(error)
        return None

# Sends the prompt to Claude as a backup

# def call_claude(prompt):

#     response = claude_client.messages.create(
#         model="claude-3-5-haiku-latest",
#         max_tokens=1000,
#         messages=[
#             {
#                 "role": "user",
#                 "content": prompt
#             }
#         ]
#     )

#     return response.content[0].text

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

    # # Generate the prompt
    # prompt = generate_prompt(client_data)

    # # Send the prompt to OpenAI
    # result = call_openai(prompt)

    # # Convert the AI response into a Python dictionary
    # ai_data = parse_ai_response(result)

    # # Display the AI data
    # print(result)

    ai_data = get_ai_data(client_data)
    print(json.dumps(ai_data, indent=4))
"""AI calls and AI boundary enforcement."""

import json
import logging
import os
from datetime import datetime, timezone

import jsonschema

from app.utilities import (
    cache_dir, cache_key, cache_read, cache_write,
    client, extract_json, extract_prompt_version,
    load_policy, load_prompt, load_schema,
    model, research_model,
)

logger = logging.getLogger(__name__)

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

    template = load_prompt("declaration.txt")
    prompt = template.replace("{declaration_text}", declaration_text)
    schema = load_schema("declaration.schema.json")

    return _call_ai("declaration", {"declaration_text": declaration_text}, prompt, schema)


# ---------------------------------------------------------------------------
# Research call — uses OpenAI Responses API with web_search tool
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
