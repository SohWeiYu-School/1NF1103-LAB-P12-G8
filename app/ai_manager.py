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
from google import genai as google_genai
from google.genai import types as google_types
from openai import OpenAI

from app import common
from app.utilities import (
    build_instructions,
    cache_check, cache_dir, cache_write,
    client, extract_json, extract_prompt_version,
    forecast_settings, has_api_key,
    load_forecast_schema,
    load_policy, load_prompt, load_schema,
    model,
)


load_dotenv()  # read the API key and model names from .env

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared Functions
# ---------------------------------------------------------------------------
# Single entry point for all web search AI calls — tries OpenAI first, falls back to Gemini if OpenAI fails.
# Gemini grounding requires a paid API key; domain filtering is OpenAI-only.
def _ai_search_pipeline(
    prompt: str,
    schema: dict,
    domains: list | None = None,
) -> tuple[str | None, object | None]:
    research_model = os.getenv("OPENAI_RESEARCH_MODEL", "gpt-4.1-mini")
    text, response = _openai_search_response(prompt, research_model, schema, domains)
    if text is not None:
        logger.info("_ai_search_pipeline: OpenAI search succeeded")
        return text, response
    logger.warning("_ai_search_pipeline: OpenAI search failed, falling back to Gemini")
    gemini_search_model = os.getenv("GEMINI_SEARCH_MODEL", "gemini-3.8-flash")
    # TODO: test Gemini search grounding end-to-end once Google grounding service is stable
    return _gemini_search_response(prompt, gemini_search_model, schema, domains)

# Single entry point for all chat completion AI calls — tries OpenAI first, falls back to Gemini if OpenAI fails
def _ai_chat_completion_pipeline(prompt: str, schema: dict | None = None) -> str | None:
    result = _openai_completion_response(prompt, model(), schema)
    if result is not None:
        logger.info("_ai_chat_completion_pipeline: OpenAI completion succeeded")
        return result
    logger.warning("_ai_chat_completion_pipeline: OpenAI failed, falling back to Gemini")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    return _gemini_completion_response(prompt, gemini_model, schema)

# ============================================================================
# 1. Shared helpers - used by every section below
# ============================================================================

def _openai_completion_response(prompt: str, model: str, schema: dict | None = None) -> str | None:
    """Plain chat call. Returns the reply text, or None if the call fails.

    With a schema the reply must follow it; without one it is just any JSON object.
    """
    try:
        if schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": schema,
                    "strict": False,
                },
            }
        else:
            response_format = {"type": "json_object"}
        response = client().chat.completions.create(
            model=model,
            temperature=0,
            response_format=response_format,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as exc:
        logger.warning("_openai_completion_response failed: %s", repr(exc))
        return None

# Purely to call Google Gemini chat completion API. Pass schema to enforce JSON output shape; omit for plain JSON mode.
def _convert_schema_for_gemini(node, unsupported: set | None = None):
    """Recursively convert a JSON Schema dict into Gemini-compatible schema format.

    Strips unsupported meta-fields (from policy.json) and converts nullable type arrays
    (e.g. ["string", "null"]) to Gemini's {type, nullable: true} form.
    """
    if unsupported is None:
        unsupported = set(load_policy().get("gemini_unsupported_schema_keys", ["$schema", "$id", "title", "additionalProperties"]))
    if isinstance(node, list):
        return [_convert_schema_for_gemini(item, unsupported) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for k, v in node.items():
        if k in unsupported:
            continue
        if k == "type" and isinstance(v, list):
            non_null = [t for t in v if t != "null"]
            if non_null:
                result["type"] = non_null[0]
                result["nullable"] = True
            continue
        # properties keys are field names (e.g. "title", "url") — never filter them
        if k == "properties" and isinstance(v, dict):
            result["properties"] = {name: _convert_schema_for_gemini(schema, unsupported)
                                    for name, schema in v.items()}
            continue
        result[k] = _convert_schema_for_gemini(v, unsupported)
    return result

def _gemini_completion_response(prompt: str, model: str, schema: dict | None = None) -> str | None:
    """
    Raw Google Gemini API call (chat completion, no web search).
    If schema is provided, passes it as response_schema to enforce structured output.
    Otherwise requests plain JSON via response_mime_type only.
    Returns the response text, or None if the call fails.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("_gemini_completion_response: GEMINI_API_KEY not set")
        return None
    try:
        gemini_client = google_genai.Client(api_key=api_key)
        gemini_schema = _convert_schema_for_gemini(schema) if schema else None
        config = google_types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=gemini_schema,
        )
        response = gemini_client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return response.text
    except Exception as exc:
        logger.warning("_gemini_completion_response failed: %s", repr(exc))
        return None

#Purely to call OpenAI Responses API with web search + strict JSON schema output (json_schema works with web_search, json_object does not)
def _openai_search_response(
    prompt: str,
    model: str,
    schema: dict,
    domains: list | None = None,
) -> tuple[str | None, object | None]:
    """Web-search call. Returns (reply text, raw response), or (None, None) if it fails.

    The reply must follow the schema (json_object mode does not work with web search).
    domains: if given, the search is limited to these sites.
    """
    try:
        tools = [{"type": "web_search"}]
        if domains:
            tools = [{"type": "web_search", "filters": {"allowed_domains": domains}}]
        response = client().responses.create(
            model=model,
            temperature=0,
            input=prompt,
            tools=tools,
            tool_choice="required",
            include=["web_search_call.action.sources"],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "research_response",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        return response.output_text, response
    except Exception as exc:
        logger.warning("_openai_search_response failed: %s", repr(exc))
        return None, None

# Purely to call Google Gemini API with Google Search grounding + structured JSON output.
# Domain filtering is not supported by Gemini — the domains param is accepted for API symmetry but ignored.
def _gemini_search_response(
    prompt: str,
    model: str,
    schema: dict,
    domains: list | None = None,
) -> tuple[str | None, object | None]:
    """
    Raw Google Gemini API call with Google Search grounding.
    Passes schema as response_schema to enforce structured output.
    Domain filtering is not supported by Gemini — domains param is ignored.
    Returns (json_text, response_object), or (None, None) if the call fails.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("_gemini_search_response: GEMINI_API_KEY not set")
        return None, None
    if domains:
        logger.warning("_gemini_search_response: domain filtering not supported by Gemini — searching all sources")
    try:
        gemini_client = google_genai.Client(api_key=api_key)
        gemini_schema = _convert_schema_for_gemini(schema)
        config = google_types.GenerateContentConfig(
            temperature=0,
            tools=[google_types.Tool(google_search=google_types.GoogleSearch())],
            response_mime_type="application/json",
            response_schema=gemini_schema,
        )
        response = gemini_client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return response.text, response
    except Exception as exc:
        logger.warning("_gemini_search_response failed: %s", repr(exc))
        return None, None

#Shared function that accepts (AI reponse & Schema) checking if the AI output matches the actaul schema
def validate_ai_response(data: dict, schema: dict) -> tuple[bool, str | None]:
    """Check an AI reply against a JSON schema. Returns (True, None) or (False, error)."""
    try:
        jsonschema.validate(instance=data, schema=schema)
        return True, None
    except jsonschema.ValidationError as exc:
        return False, exc.message
    except Exception as exc:
        return False, repr(exc)


# ============================================================================
# 2. Benchmark - expected wealth figures for a client profile
# ============================================================================

def get_ai_benchmark(client_data: dict) -> dict | None:
    """Entry point. Returns the benchmark dict, or None if the call fails.

    main.py passes this to logic_manager as a callback.
    """
    mdl = "gpt-4.1-nano"

    # Step 1: Build payload (allowlist only - no declared figures reach the AI)
    payload = build_benchmark_payload(client_data)

    # Step 2: Load prompt + schema
    template = load_prompt("benchmark.txt")
    prompt = template.replace("{client_data}", str(payload))
    schema = load_schema("benchmark.schema.json")

    # Step 3: Reuse a saved answer if there is one
    key, cached = cache_check("benchmark", payload, extract_prompt_version(template), mdl)
    if cached is not None:
        return cached

    # Step 4: Call the AI, parse, validate, save (2 attempts)
    for attempt in range(2):
        text = _ai_chat_completion_pipeline(prompt, schema)
        if text is None:
            logger.warning("get_ai_benchmark attempt %d: API call failed", attempt + 1)
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("get_ai_benchmark attempt %d JSON parse error: %s", attempt + 1, repr(exc))
            continue

        valid, err = validate_ai_response(data, schema)
        if not valid:
            logger.warning("get_ai_benchmark attempt %d schema validation failed: %s", attempt + 1, err)
            continue

        cache_write(key, data, "benchmark")
        return data

    logger.error("get_ai_benchmark exhausted all retries")
    return None


def build_benchmark_payload(client_data: dict) -> dict:
    """Keep only the fields the benchmark AI may see (CLAUDE.md rule 5)."""
    allowed = load_policy().get("benchmark_allowed_fields", ["occupation", "industry", "age", "career_start_year", "country"])
    return {k: client_data[k] for k in allowed if k in client_data}


# ============================================================================
# 3. Research - search trusted sites for published ML/TF reports
# ============================================================================

def get_ai_research(case_input: dict) -> tuple[dict | None, list[str]]:
    """Orchestrate the full research AI flow for a given case input.

    Internally: build payload → cache check → search pipeline (OpenAI first, Gemini fallback) →
    JSON parse → trim + validate → post-process → return.

    Returns (research_dict | None, warnings).
    """
    warnings: list[str] = []

    # Step 1: Build payload (allowlist only - industry + country)
    payload = build_research_payload(case_input)

    # Step 2: Load policy limits, then build prompt + schema
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

    mdl = os.getenv("OPENAI_RESEARCH_MODEL", "gpt-4.1-mini")

    # Step 3: Reuse a saved answer if there is one
    key, cached = cache_check("research", payload, extract_prompt_version(prompt), mdl)
    if cached is not None:
        return cached, warnings

    # Step 4: API call + parse + validate + post-process (no retry — web search is expensive)
    # Uses _ai_search_pipeline: OpenAI Responses API first, Gemini grounding as fallback
    last_error = ""
    error_type = "api"

    for attempt in range(1):
        attempt_warnings: list[str] = []

        text, response = _ai_search_pipeline(prompt, schema=schema, domains=domains)
        if text is None:
            error_type = "api"
            last_error = "API call failed"
            logger.warning("get_ai_research attempt %d: API call failed", attempt + 1)
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            error_type = "json"
            last_error = repr(exc)
            logger.warning("get_ai_research attempt %d JSON parse error: %s", attempt + 1, last_error)
            continue

        # Cut to the policy limits first, so an over-long reply can still pass the schema
        data, trim_warnings = trim_reports(data, max_reports, max_excerpts)
        attempt_warnings.extend(trim_warnings)

        valid, err = validate_ai_response(data, schema)
        if not valid:
            error_type = "schema"
            last_error = err
            logger.warning("get_ai_research attempt %d schema validation failed: %s", attempt + 1, last_error)
            continue

        data, pp_warnings = research_post_process(data, response, key, "research", mdl)
        warnings.extend(attempt_warnings)
        warnings.extend(pp_warnings)
        return data, warnings

    logger.error("get_ai_research exhausted all retries. error_type=%s last_error=%s", error_type, last_error)
    return None, ["Research AI call failed. This case needs manual review. Details: logs/app.log"]


def build_research_payload(case_input: dict) -> dict:
    """Keep only the fields the research AI may see. Country falls back to residence."""
    allowed = load_policy().get("research_allowed_fields", ["industry", "country"])
    payload = {k: case_input[k] for k in allowed if k in case_input}
    if "country" not in payload:
        payload["country"] = case_input.get("country_of_residence", "Singapore")
    return payload


def research_post_process(
    data: dict,
    response,
    key: str,
    kind: str,
    mdl: str,
) -> tuple[dict, list[str]]:
    """Finish a valid research reply and save it. Returns (data, warnings).

    Adds the web sources, report IDs, clean excerpts, a timestamp and the model name.
    """
    warnings: list[str] = []
    api_sources = extract_api_sources(response) if response is not None else []
    if not api_sources:
        warnings.append("Research: web search returned no sources — results may not be grounded.")
    data = add_report_ids(data)
    data = strip_excerpts(data)
    data["api_sources"] = api_sources
    data["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    data["model"] = mdl
    cache_write(key, data, kind)
    data["_cache_path"] = os.path.join(cache_dir(kind), f"{key}.json")
    return data, warnings


def trim_reports(data: dict, max_reports: int, max_excerpts: int) -> tuple[dict, list[str]]:
    """Cut reports and excerpts down to the policy limits. Turns year strings into ints."""
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


def add_report_ids(data: dict) -> dict:
    """Number the reports R1, R2, ... Done by code, not the AI."""
    for i, r in enumerate(data.get("reports", []), 1):
        r["report_id"] = f"R{i}"
    return data


def extract_api_sources(response) -> list[dict]:
    """List the pages the web search really opened, as {url, title}, no repeats."""
    seen: set[str] = set()
    sources: list[dict] = []

    for item in response.output:
        # First place to look: the sources of each search call
        if item.type == "web_search_call":
            action = getattr(item, "action", None)
            for src in getattr(action, "sources", None) or []:
                url = getattr(src, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"url": url, "title": url})
        # Second place: the links cited inside the reply text
        if item.type == "message":
            for block in item.content:
                if block.type != "output_text":
                    continue
                for ann in getattr(block, "annotations", []):
                    if ann.type == "url_citation" and ann.url not in seen:
                        seen.add(ann.url)
                        sources.append({"url": ann.url, "title": ann.title})
    logger.debug("extract_api_sources: found %d sources", len(sources))
    return sources


def strip_excerpts(data: dict) -> dict:
    """Remove the quote marks and spaces the AI puts around each excerpt."""
    quote_chars = '"\u201c\u201d\u2018\u2019\''
    for r in data.get("reports", []):
        r["excerpts"] = [e.strip(quote_chars).strip() for e in r.get("excerpts", [])]
    return data


# TODO: Change name to get_ai_typologies
# Single entry point for typology AI — main.py calls only this
def get_typologies(case_input: dict, research: dict | None = None) -> tuple[dict | None, list[str]]:
    """Entry point. Returns (typologies dict or None, warnings).

    The AI sees only the allowlisted profile fields and the research reports (no URLs).
    """
    warnings: list[str] = []

    # Step 1: Load policy limits, then build payload (profile + reports)
    policy = load_policy()
    allowed_fields = policy.get("benchmark_allowed_fields", ["occupation", "industry", "age", "career_start_year", "country"])
    report_fields = policy.get("typology_report_fields", ["report_id", "organisation", "title", "year", "summary", "excerpts"])
    max_patterns = policy.get("typology_max_patterns", 5)

    profile_payload = {k: case_input[k] for k in allowed_fields if k in case_input}
    if "country" not in profile_payload:
        profile_payload["country"] = case_input.get("country_of_residence", "Singapore")

    reports = build_typology_reports(research, report_fields) if research is not None else []
    payload = {**profile_payload, "reports": reports}

    # Step 2: Load prompt + schema
    template = load_prompt("typology.txt")
    prompt = (
        template
        .replace("{payload_json}", json.dumps(profile_payload, indent=2))
        .replace("{reports_json}", json.dumps(reports, indent=2))
    )
    schema = load_schema("typology_response.schema.json")

    mdl = model()

    # Step 3: Reuse a saved answer if there is one
    key, cached = cache_check("typology", payload, extract_prompt_version(prompt), mdl)
    if cached is not None:
        return cached, warnings

    # Step 4: Call the AI, parse, trim, validate, save (2 attempts)
    last_error = ""
    error_type = "api"

    for attempt in range(2):
        attempt_warnings: list[str] = []

        text = _ai_chat_completion_pipeline(prompt, schema)
        if text is None:
            error_type = "api"
            last_error = "API call failed"
            logger.warning("get_typologies attempt %d: API call failed", attempt + 1)
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            error_type = "json"
            last_error = repr(exc)
            logger.warning("get_typologies attempt %d JSON parse error: %s", attempt + 1, last_error)
            continue

        data, trim_warnings = trim_typologies(data, max_patterns)
        attempt_warnings.extend(trim_warnings)

        valid, err = validate_ai_response(data, schema)
        if not valid:
            error_type = "schema"
            last_error = err
            logger.warning("get_typologies attempt %d schema validation failed: %s", attempt + 1, last_error)
            continue

        data["model"] = mdl
        cache_write(key, data, "typology")
        data["_cache_path"] = os.path.join(cache_dir("typology"), f"{key}.json")
        warnings.extend(attempt_warnings)
        return data, warnings

    logger.error("get_typologies exhausted all retries. error_type=%s last_error=%s", error_type, last_error)
    return None, ["Sector Crime Scan AI call failed. This case needs manual review. Details: logs/app.log"]

# Trim AI response to policy max typologies. Called by get_typologies before schema validation.

def trim_typologies(data: dict, max_patterns: int) -> tuple[dict, list[str]]:
    """Keep at most max_patterns typologies. Returns (data, warnings)."""
    typologies = data.get("sector_typologies", [])
    if len(typologies) <= max_patterns:
        return data, []
    trimmed = {**data, "sector_typologies": typologies[:max_patterns]}
    warning = (
        f"AI returned {len(typologies)} typologies; "
        f"kept first {max_patterns} per policy."
    )
    logger.warning(warning)
    return trimmed, [warning]


def build_typology_reports(research: dict, report_fields: list) -> list[dict]:
    """Keep only the safe report fields (no URLs) for the typology AI."""
    return [
        {k: r[k] for k in report_fields if k in r}
        for r in research.get("reports", [])
    ]


# ============================================================================
# 5. Declaration - free-text declaration -> structured sources (crime scan)
# ============================================================================

def get_declaration(case_input: dict) -> tuple[dict | None, list[str]]:
    """Entry point. Returns (declaration dict or None, warnings)."""
    warnings: list[str] = []

    # Step 1: Build payload (declaration text + a one-line career profile)
    declaration_text = case_input.get("declaration_text", "")
    career = case_input.get("career", {})
    occupation  = career.get("occupation", case_input.get("occupation", "Unknown"))
    industry    = career.get("industry",   case_input.get("industry",   "Unknown"))
    career_profile = f"Occupation: {occupation}. Industry: {industry}."
    payload = {"declaration_text": declaration_text, "career_profile": career_profile}

    # Step 2: Load prompt + schema
    template = load_prompt("crime_scan_declaration.txt")
    prompt = (
        template
        .replace("{declaration_text}", declaration_text)
        .replace("{career_profile}", career_profile)
    )
    schema = load_schema("crime_scan_declaration.schema.json")

    mdl = model()

    # Step 3: Reuse a saved answer if there is one
    key, cached = cache_check("declaration", payload, extract_prompt_version(prompt), mdl)
    if cached is not None:
        logger.info("get_declaration: returning cached result")
        return cached, warnings

    # Step 4: Call the AI, parse, validate, save (2 attempts)
    last_error = ""
    error_type = "api"

    for attempt in range(2):
        text = _ai_chat_completion_pipeline(prompt, schema)
        if text is None:
            error_type = "api"
            last_error = "API call failed"
            logger.warning("get_declaration attempt %d: API call failed", attempt + 1)
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            error_type = "json"
            last_error = repr(exc)
            logger.warning("get_declaration attempt %d JSON parse error: %s", attempt + 1, last_error)
            continue

        valid, err = validate_ai_response(data, schema)
        if not valid:
            error_type = "schema"
            last_error = err
            logger.warning("get_declaration attempt %d schema validation failed: %s", attempt + 1, last_error)
            continue

        data["model"] = mdl
        cache_write(key, data, "declaration")
        data["_cache_path"] = os.path.join(cache_dir("declaration"), f"{key}.json")
        return data, warnings

    logger.error("get_declaration exhausted all retries. error_type=%s last_error=%s", error_type, last_error)
    return None, ["Declaration AI call failed. This case needs manual review. Details: logs/app.log"]


# ============================================================================
# 5b. Document auto-fill - reads uploaded client documents into form details
# ============================================================================

# Most documents sent to the AI in one go (each one is a separate part of the message)
MAX_EXTRACT_DOCUMENTS = 10


def build_document_parts(documents: list[dict]) -> list[dict]:
    """Turn documents read by data_manager.read_document_for_extraction into
    chat message parts. Text goes between <document> markers so the AI treats it as data."""
    parts: list[dict] = []
    for document in documents[:MAX_EXTRACT_DOCUMENTS]:
        name = document.get("file_name", "document")
        kind = document.get("kind")
        if kind == "text":
            body = wrap_free_text(document.get("text", ""), f'<document name="{name}">', "</document>")
            parts.append({"type": "text", "text": body})
        elif kind == "image":
            parts.append({"type": "text", "text": f'<document name="{name}"> (image below)'})
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{document['mime']};base64,{document['data_b64']}"}})
        elif kind == "pdf":
            parts.append({"type": "text", "text": f'<document name="{name}"> (scanned PDF below)'})
            parts.append({"type": "file", "file": {
                "filename": name, "file_data": f"data:application/pdf;base64,{document['data_b64']}"}})
    return parts


def _openai_document_response(prompt: str, parts: list[dict], mdl: str, schema: dict) -> str | None:
    """Chat call with documents attached (text, images, scanned PDFs). Returns the reply text or None."""
    try:
        response = client().chat.completions.create(
            model=mdl,
            temperature=0,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "document_extract", "schema": schema, "strict": False}},
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": parts}],
        )
        return response.choices[0].message.content
    except Exception as exc:
        logger.warning("_openai_document_response failed: %s", repr(exc))
        return None


def extract_client_details(documents: list[dict]) -> tuple[dict | None, list[str]]:
    """Entry point. Read uploaded documents and return (form details or None, warnings).

    The details follow app/schemas/document_extract.schema.json. Nothing is decided here:
    the officer reviews every pre-filled value before the client is saved.
    """
    warnings = [d["error"] for d in documents if d.get("kind") == "error"]
    readable = [d for d in documents if d.get("kind") in ("text", "image", "pdf")]
    if len(readable) > MAX_EXTRACT_DOCUMENTS:
        warnings.append(f"Only the first {MAX_EXTRACT_DOCUMENTS} documents were read.")
    if not readable:
        return None, warnings or ["None of the uploaded files could be read."]
    if not has_api_key():
        return None, warnings + ["No OPENAI_API_KEY is set, so documents can't be read automatically."]

    prompt = load_prompt("document_extract.txt")
    schema = load_schema("document_extract.schema.json")
    parts = build_document_parts(readable)
    mdl = model()

    last_error = ""
    for attempt in range(2):
        text = _openai_document_response(prompt, parts, mdl, schema)
        if text is None:
            last_error = "API call failed"
            continue
        try:
            data = json.loads(extract_json(text))
        except json.JSONDecodeError as exc:
            last_error = repr(exc)
            logger.warning("extract_client_details attempt %d JSON error: %s", attempt + 1, last_error)
            continue
        valid, err = validate_ai_response(data, schema)
        if not valid:
            last_error = err or "schema"
            logger.warning("extract_client_details attempt %d schema error: %s", attempt + 1, last_error)
            continue
        return data, warnings

    logger.error("extract_client_details failed: %s", last_error)
    return None, warnings + ["The documents could not be read automatically. Details: logs/app.log"]


# ============================================================================
# 6. Forecasting - runs after the benchmark
# ============================================================================

# A check takes the parsed reply and returns a list of problems (empty = fine)
CheckFunction = Callable[[dict], list[str]]

# Settings: forecast_settings() in app/utilities.py reads the "forecasting" block of
# config/policy.json. Prompts and schemas are loaded there too; the model comes from .env.


# ---- Cleaning the input before it goes to the AI ----

def strip_keys(value: Any, keys: set) -> Any:
    """Remove the given keys from every dict inside value."""
    if isinstance(value, dict):
        return {k: strip_keys(v, keys) for k, v in value.items() if k.lower() not in keys}
    if isinstance(value, list):
        return [strip_keys(item, keys) for item in value]
    return value


def redact_names(text: str, names: list[str]) -> str:
    """Replace the client's name, including 'Mr Tan' style references, with 'the client'."""
    honorifics = r"(?:" + "|".join(re.escape(h) for h in forecast_settings()["honorifics"]) + r")\.?"
    words = {w for name in names for w in re.split(r"\s+", name or "") if len(w) > 1}
    # Longest words first, so a short name can't break up a longer one
    for word in sorted(words, key=len, reverse=True):
        pattern = re.escape(word)
        text = re.sub(rf"\b{honorifics}\s+{pattern}\b", "the client", text, flags=re.IGNORECASE)
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
    settings = forecast_settings()
    if include_declaration:
        names = [str(record[key]) for key in settings["personal_keys"] if record.get(key)]
        text = redact_names(record.get("sow_declaration", ""), names)
        facts["sow_declaration"] = wrap_free_text(text, *settings["declaration_markers"])
    facts = strip_keys(facts, set(settings["personal_keys"]))
    return strip_keys(facts, set(settings["claimed_figure_keys"]))


# ---- Checking the AI's reply: general checks ----

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


def sort_ranges(value: Any) -> Any:
    """Put every {low, typical, high} range in order (e.g. -1/-2/-3 for a fall becomes -3/-2/-1).
    The model sometimes labels a negative range back to front; the three numbers are kept,
    only relabelled, so nothing is invented. Same reply in, same result out."""
    if isinstance(value, dict):
        if set(value) == set(common.RANGE_KEYS):
            numbers = [value[k] for k in common.RANGE_KEYS]
            if all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers):
                value["low"], value["typical"], value["high"] = sorted(numbers)
            return value
        for item in value.values():
            sort_ranges(item)
    elif isinstance(value, list):
        for item in value:
            sort_ranges(item)
    return value


def parse_reply(raw: str | None) -> tuple[dict | None, str | None]:
    """Pull the JSON object out of the model's reply. Returns (data, error)."""
    if not raw or not raw.strip():
        return None, "empty reply (possible refusal or cut-off)"
    try:
        return json.loads(extract_json(raw)), None
    except json.JSONDecodeError as error:
        return None, f"invalid JSON: {error}"


def _top_level_keys(schema: dict) -> list[str]:
    """The keys the answer must have at the top level."""
    return list(schema.get("required") or schema.get("properties", {}).keys())


def _looks_like_schema(data: object) -> bool:
    """The model sometimes sends the schema back instead of filling it in."""
    return isinstance(data, dict) and ("$schema" in data or {"type", "properties"} <= set(data))


def validate_reply(raw: str | None, schema: dict,
                   check: CheckFunction | None = None) -> tuple[dict | None, list[str]]:
    """Parse, then check the schema, then the ranges, then the piece's own rules."""
    data, error = parse_reply(raw)
    if error:
        return None, [error]
    if _looks_like_schema(data) and not _looks_like_schema(schema.get("properties", {})):
        keys = ", ".join(f'"{k}"' for k in _top_level_keys(schema))
        return None, [f"you returned the JSON schema itself instead of an answer. Return the ANSWER: "
                      f"one JSON object whose top-level keys are {keys}, filled in with real values."]
    valid, error = validate_ai_response(data, schema)
    errors = [] if valid else [f"does not follow the schema: {error}"]
    if not errors:
        errors = range_errors(sort_ranges(data))
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
    """An error if any id was used that is not in the known set."""
    unknown = sorted(set(used) - known)
    return [f"{label} uses unknown ids {unknown}"] if unknown else []


# ---- Checking the AI's reply: one check per kind of piece ----
# Each check_* returns a list of problems (empty = fine); the problems are sent back to
# the AI on the retry. Each repair_* first fixes what the code can work out by itself.

def check_declaration(data: dict, job_count: int, asset_count: int) -> list[str]:
    """Step 0: sources have unique ids, link to real jobs/assets, and years are in order."""
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
    """Call 1 list: market ids are unique and simple; yearly chances add up to 100."""
    ids = [m["market_id"] for m in data["relevant_markets"]]
    errors = [] if ids else ["at least one market is required"]
    if len(ids) != len(set(ids)):
        errors.append("market_id values must be unique")
    errors += [f"market_id '{i}' may only use letters, digits and _" for i in ids
               if not re.fullmatch(r"[A-Za-z0-9_]+", i)]
    # One steady/boom/downturn split per forecast year (the range allows for rounding)
    lowest, highest = forecast_settings()["scenario_chance_total_range"]
    chances = data["scenario_chances"]
    errors += _years_errors("scenario_chances", [c["year"] for c in chances], forecast_years)
    for c in chances:
        total = c["steady"] + c["boom"] + c["downturn"]
        if not lowest <= total <= highest or min(c["steady"], c["boom"], c["downturn"]) < 0:
            errors.append(f"scenario_chances {c['year']}: chances must be 0-100 and add up to 100")
    return errors


def check_market_series(data: dict, market_id: str, history_years: list[int],
                        forecast_years: list[int]) -> list[str]:
    """Call 1 series: every year is there once, and the yearly changes are believable."""
    errors = [] if data["market_id"] == market_id else [f"market_id must be '{market_id}'"]
    errors += _years_errors(f"{market_id} history", [h["year"] for h in data["history"]], history_years)
    errors += _years_errors(f"{market_id} outlook", [o["year"] for o in data["outlook"]], forecast_years)
    # All zeros means the model didn't know and filled in 0
    settings = forecast_settings()
    known = [h["change_pct"] for h in data["history"] if h["change_pct"] is not None]
    if len(known) >= settings["all_zero_history_min_years"] and all(change == 0 for change in known):
        errors.append(f"{market_id} history: every year is 0. Use null for years you don't know; "
                      "0 means the market really didn't move")
    # A change far outside the range is usually an index level, not a % change
    low, high = settings["market_change_range"]
    errors += [f"{market_id} {h['year']}: {h['change_pct']}% is not a believable one-year change "
               f"(give the % change from the year before, not an index level)"
               for h in data["history"] if h["change_pct"] is not None and not low <= h["change_pct"] <= high]
    return errors


def check_position_pay(data: dict, index: int, years: list[int]) -> list[str]:
    """Call 2 job pay: right job, and one row for every year the job was held."""
    errors = [] if data["position_index"] == index else [f"position_index must be {index}"]
    errors += _years_errors(f"job {index}", [row["year"] for row in data["yearly"]], years)
    return errors


def check_future_pay(data: dict, forecast_years: list[int]) -> list[str]:
    """Call 2 future pay: one row for every forecast year."""
    return _years_errors("future pay", [row["year"] for row in data["yearly"]], forecast_years)


def check_asset_model(data: dict, index: int, years: list[int], market_ids: set) -> list[str]:
    """Call 2 asset: follows known markets, and its numbers sit inside the sense limits."""
    settings = forecast_settings()
    errors = [] if data["asset_index"] == index else [f"asset_index must be {index}"]
    if not data["exposures"]:
        errors.append("at least one market exposure is required")
    errors += _unknown_ids("exposures", [e["market_id"] for e in data["exposures"]], market_ids)
    if len(data["exposures"]) > settings["max_asset_exposures"]:
        errors.append(f"at most {settings['max_asset_exposures']} market exposures: only the markets that really drive "
                      f"this asset's value (e.g. a property follows its property market and currency)")
    low, high = settings["sensitivity_range"]
    for exposure in data["exposures"]:
        values = exposure["sensitivity"].values()
        if not all(low <= v <= high for v in values):
            errors.append(f"{exposure['market_id']}: sensitivity {exposure['sensitivity']} must be between "
                          f"{low} and {high} (1.0 = the asset moves 1% when the market moves 1%)")
    low, high = settings["own_growth_range"]
    if not all(low <= v <= high for v in data["own_growth_pct"].values()):
        errors.append(f"own_growth_pct {data['own_growth_pct']} must be between {low} and {high} (% a year)")
    errors += _years_errors(f"asset {index} income_yield_by_year",
                            [row["year"] for row in data["income_yield_by_year"]], years)
    return errors


def repair_person_types(data: dict, record: dict, source_records: list[dict]) -> dict:
    """Fix the years the code can work out itself, before the reply is checked.

    T1 (honest) always covers his whole career and every forecast year. A crime type's
    years are moved inside the window of the job / asset / source that enables it
    (e.g. open from 2008 through a job held 2015-2019 becomes 2015). Only a type whose
    years can't overlap its enabler at all is left for check_person_types to reject.
    Same reply in, same repair out, so saved replies give identical results.
    """
    start, end = common.career_years(record)
    last_forecast_year = common.future_years(record)[-1]
    jobs = common.career_positions(record)
    assets = record.get("investments", [])
    sources = {s["source_id"]: s for s in source_records}
    for person_type in data.get("person_types", []):
        if person_type.get("type_id") == common.BASELINE_TYPE:
            person_type["open_from"], person_type["open_to"] = start, last_forecast_year
            if not person_type.get("rule_out_documents"):
                # the documents that test the declared story as a whole (policy: NET_WORTH)
                codes = load_policy().get("breach_documents", {}).get("NET_WORTH") \
                    or ["DOC_TAX_RETURNS", "DOC_BANK_STATEMENTS"]
                person_type["rule_out_documents"] = [{"document_code": c, "also_rules_out": []} for c in codes]
            continue

        # A crime type: collect the years each enabler (job, asset, source) existed
        windows = []
        job_index = person_type.get("enabled_by_position_index")
        if isinstance(job_index, int) and 0 <= job_index < len(jobs):
            job = jobs[job_index]
            job_last = last_forecast_year if not job.get("end_year") else common.position_end(job, end)
            windows.append((int(job["start_year"]), job_last))
        asset_index = person_type.get("enabled_by_asset_index")
        if isinstance(asset_index, int) and 0 <= asset_index < len(assets):
            windows.append((int(assets[asset_index]["year_acquired"]), last_forecast_year))
        source = sources.get(person_type.get("enabled_by_source_id"))
        if source:
            windows.append((int(source.get("start_year") or start), last_forecast_year))
        if not windows:
            continue

        # Move the type's years inside those windows, if they overlap at all
        first = max([start, min(w[0] for w in windows)])
        last = min([last_forecast_year, max(w[1] for w in windows)])
        open_from, open_to = person_type.get("open_from"), person_type.get("open_to")
        if not isinstance(open_from, int) or not isinstance(open_to, int):
            continue
        new_from, new_to = max(open_from, first), min(open_to, last)
        if new_from <= new_to and (new_from, new_to) != (open_from, open_to):
            logger.info("%s: years %d-%d moved to %d-%d to fit what enables it",
                        person_type.get("type_id"), open_from, open_to, new_from, new_to)
            person_type["open_from"], person_type["open_to"] = new_from, new_to
    return data


def check_person_types(data: dict, record: dict, source_records: list[dict]) -> list[str]:
    """Call 3 list: T1 is the honest person, the rest are crime types.

    Each crime type is ONE standard category from the vocabulary, tied to the job, asset
    or declared source that makes it possible for him, and open only in the years that
    job/asset/source existed.
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
    settings = forecast_settings()
    max_crime_types = settings["max_crime_types"]
    if len(ids) - 1 > max_crime_types:
        errors.append(f"at most {max_crime_types} crime types, ranked most relevant first; got {len(ids) - 1}")
    if len(ids) != len(set(ids)):
        errors.append("type_id values must be unique")

    honest_category = settings["honest_category"]
    categories = [t["category"] for t in types]
    if types and categories[0] != honest_category:
        errors.append(f"{common.BASELINE_TYPE} must use category {honest_category}")
    if honest_category in categories[1:]:
        errors.append(f"only {common.BASELINE_TYPE} may use category {honest_category}")
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
        if type_id != common.BASELINE_TYPE and not person_type["rule_out_documents"]:
            errors.append(f"{type_id}: list at least one document that would prove this type wrong")
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


def repair_type_behaviour(data: dict, type_id: str, end: int, forecast_years: list[int],
                          open_years: tuple[int, int] | None, market_ids: set) -> dict:
    """Fix what the code can work out itself, before the reply is checked.

    The type id and route years come from the type list. Years outside the route (or
    repeated) are dropped, and so are unknown market ids. Missing years are NOT invented,
    so those still go back to the AI.
    """
    data["type_id"] = type_id
    if open_years is not None:
        data["route_open_from"], data["route_open_to"] = open_years
    first, last = data.get("route_open_from"), data.get("route_open_to")
    if isinstance(first, int) and isinstance(last, int):
        history_years = set(range(first, min(last, end) + 1))
        for key, allowed in (("extra_money_history", history_years), ("extra_money_forecast", set(forecast_years))):
            rows, seen = [], set()
            for row in data.get(key) or []:
                year = row.get("year") if isinstance(row, dict) else None
                if year in allowed and year not in seen:
                    seen.add(year)
                    rows.append(row)
            data[key] = rows
    if isinstance(data.get("linked_market_ids"), list):
        data["linked_market_ids"] = [m for m in data["linked_market_ids"] if m in market_ids]
    return data


def repair_type_reactions(data: dict, type_id: str, event_ids: list[str]) -> dict:
    """Keep one reaction per known event (extras and repeats dropped). Missing ones still go back to the AI."""
    data["type_id"] = type_id
    kept, seen = [], set()
    for reaction in data.get("reactions") or []:
        event_id = reaction.get("event_id") if isinstance(reaction, dict) else None
        if event_id in event_ids and event_id not in seen:
            seen.add(event_id)
            kept.append(reaction)
    data["reactions"] = kept
    return data


def check_type_behaviour(data: dict, type_id: str, start: int, end: int,
                         forecast_years: list[int], market_ids: set,
                         open_years: tuple[int, int] | None = None) -> list[str]:
    """Call 3 behaviour: route years match the type list, every year is there, signals make sense."""
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
        if signal["amount_per_year"]["high"] <= 0:
            errors.append(f"{signal['signal_code']}: amount_per_year is 0; give the money this activity "
                          f"would really show in his account each year (in SGD)")
    salary_signal = forecast_settings()["honest_required_signal"]
    if type_id == common.BASELINE_TYPE and salary_signal not in {s["signal_code"] for s in data["signals"]}:
        errors.append(f"the honest type must include his regular salary ({salary_signal}) among its signals")
    return errors


def check_events(data: dict, forecast_years: list[int], market_ids: set) -> list[str]:
    """Call 4 list: the right number of events, each in a forecast year and hitting a known market."""
    events = data["events"]
    ids = [e["event_id"] for e in events]
    settings = forecast_settings()
    errors = []
    if not settings["min_events"] <= len(events) <= settings["max_events"]:
        errors.append(f"expected {settings['min_events']}-{settings['max_events']} events, got {len(events)}")
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
    """Call 4 reactions: exactly one reaction per event."""
    errors = [] if data["type_id"] == type_id else [f"type_id must be '{type_id}'"]
    found = sorted(r["event_id"] for r in data["reactions"])
    if found != sorted(event_ids):
        errors.append(f"reactions need exactly one entry per event {event_ids}, got {found}")
    return errors


def check_review(data: dict, market_ids: set, event_ids: set) -> list[str]:
    """Review: only known markets and events, and every inflow was received at least once."""
    errors = _unknown_ids("market_moves", [m["market_id"] for m in data["market_moves"]], market_ids)
    errors += _unknown_ids("events_seen", data["events_seen"], event_ids)
    errors += [f"inflow {i['signal_code']}: times_received must be at least 1"
               for i in data["inflows"] if i["times_received"] < 1]
    return errors


# ---- Sending one request: retry, validate, log, never crash ----

def _empty_result(call_name: str, prompt_version: str) -> dict:
    """The result dict every call returns, filled in as 'not done yet'."""
    return {"call": call_name, "ok": False, "data": None, "raw": None, "attempts": 0,
            "from_cache": False, "error": None, "prompt_version": prompt_version}


def _build_prompt(instructions: str, user_text: str, schema: dict) -> str:
    """The single prompt the shared OpenAI functions take: the rules, the schema, then the data.
    The reply is checked locally, so the schema is also spelled out in the prompt."""
    return (instructions
            + "\n\nYour answer must be ONE JSON object whose top-level keys are "
            + ", ".join(f'"{k}"' for k in _top_level_keys(schema))
            + ", filled in with your answer. It must follow the JSON schema below. "
              "Do NOT repeat or return the schema itself.\nSchema:\n"
            + json.dumps(schema, separators=(",", ":"))
            + "\n\nDATA:\n" + user_text + "\n\nAnswer with ONE JSON object.")


#Call forecasting ai name
def run_forecasting(call_name: str, prompt_name: str, values: dict, user_data: dict,
                    schema_name: str, check: CheckFunction | None = None,
                    cached_raw: str | None = None, web_search: bool = False) -> dict:
    """Send one forecasting request and return a checked result. Never raises.

    call_name    name of this piece (also its key in the saved replies)
    prompt_name  prompt file to use; values fill its {placeholders}
    user_data    the facts sent to the AI
    schema_name  schema the reply must follow; check adds the piece's own rules
    cached_raw   a saved reply to reuse instead of asking again
    Returns {"call", "ok", "data", "raw", "attempts", "from_cache", "error", "prompt_version"}.
    """
    try:
        settings = forecast_settings()
        version, instructions = build_instructions(prompt_name, values, web_search)
        schema = load_forecast_schema(schema_name)
    except (OSError, ValueError, KeyError) as error:  # a missing or broken prompt/schema/policy file
        result = _empty_result(call_name, prompt_name)
        result["error"] = f"could not prepare the request: {error}"
        logger.error("%s: %s", call_name, result["error"])
        return result
    result = _empty_result(call_name, version)

    # A saved reply for this request: reuse it so reruns are identical and free
    if cached_raw:
        data, errors = validate_reply(cached_raw, schema, check)
        if not errors:
            result.update(ok=True, data=data, raw=cached_raw, from_cache=True)
            logger.info("%s: using saved reply", call_name)
            return result
        logger.warning("%s: saved reply no longer valid (%s), asking again", call_name, errors[:2])

    if not has_api_key():
        result["error"] = "no OpenAI API key (is OPENAI_API_KEY set?)"
        logger.error("%s: %s", call_name, result["error"])
        return result

    prompt = _build_prompt(instructions, json.dumps(user_data, indent=2, ensure_ascii=False), schema)
    max_retries = settings["max_retries"]
    last_errors: list[str] = []

    for attempt in range(1, max_retries + 1):
        result["attempts"] = attempt
        message = prompt
        if last_errors:
            # The same prompt at temperature 0 would repeat the mistake, so say what to fix
            message += ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED because:\n- " + "\n- ".join(last_errors[:settings["max_errors_sent_back"]])
                        + "\nReturn a complete, corrected JSON object.")
        if web_search:
            raw, _ = _ai_search_pipeline(message, schema)
        else:
            raw = _ai_chat_completion_pipeline(message, schema)
        if raw is None:
            result["error"] = "the AI request failed (details in logs/app.log)"
            logger.warning("%s: attempt %d failed", call_name, attempt)
            if attempt < max_retries:
                time.sleep(settings["retry_wait_seconds"] * attempt)
            continue

        data, errors = validate_reply(raw, schema, check)
        if not errors:
            result.update(ok=True, data=data, raw=raw, error=None)
            logger.info("%s: valid reply on attempt %d", call_name, attempt)
            return result
        result["raw"] = raw
        last_errors = errors
        result["error"] = "reply failed checks: " + "; ".join(errors[:5])
        logger.warning("%s: attempt %d rejected: %s", call_name, attempt, errors[:3])

    logger.error("%s: giving up after %d attempts (%s)", call_name, max_retries, result["error"])
    return result


# ---- The pipeline: small helpers that shape data for the prompts ----

def _years_text(years: list[int]) -> str:
    """[2026, 2027, 2028] -> "2026-2028"."""
    return f"{years[0]}-{years[-1]}" if years else "none"


def _markets_brief(markets: dict) -> list[dict]:
    """Just the id, name and indicator of each market."""
    return [{k: m[k] for k in ("market_id", "name", "indicator")} for m in markets["relevant_markets"]]


def _market_changes(markets: dict, first: int, last: int) -> dict:
    """{market_id: {year: change}} for the years a piece needs."""
    return {market_id: {h["year"]: h["change_pct"] for h in series["history"] if first <= h["year"] <= last}
            for market_id, series in markets["series"].items()}


def _market_outlooks(markets: dict) -> dict:
    """{market_id: outlook} for the forecast years."""
    return {market_id: series["outlook"] for market_id, series in markets["series"].items()}


def _types_brief(types: dict) -> list[dict]:
    """Just the id, category, name and route of each type."""
    return [{k: t[k] for k in ("type_id", "category", "name", "route")} for t in types["person_types"]]


def _crime_categories() -> list[dict]:
    """The fixed list of crime categories the AI must choose from (config vocabulary)."""
    return [{"category": code, "description": text}
            for code, text in common.vocabulary().get("crime_categories", {}).items()]


# ---- The pipeline: Step 0 + Calls 1-4 ----
# Each step takes the saved replies (cache), adds its results to `results`,
# and returns its data, or None as soon as one piece fails.

def read_declaration(record: dict, cache: dict, results: dict) -> dict | None:
    """Step 0: free-text declaration -> structured list of claimed sources."""
    jobs, assets = len(common.career_positions(record)), len(record.get("investments", []))
    result = run_forecasting("step0_declaration", "declaration", {},
                             client_facts(record, include_declaration=True), "declaration",
                             lambda data: check_declaration(data, jobs, assets), cache.get("step0_declaration"))
    results[result["call"]] = result
    return result["data"] if result["ok"] else None


def forecast_markets(record: dict, declaration: dict,
                     cache: dict, results: dict) -> dict | None:
    """Call 1: choose the markets, then recall and forecast each one."""
    start, end = common.career_years(record)
    years = common.future_years(record)
    values = {"start_year": start, "end_year": end, "forecast_years": _years_text(years),
              "last_forecast_year": years[-1]}
    web_search = forecast_settings()["web_search"]

    # 1a. Which markets matter for this client
    name = "call1_markets.list"
    listing = run_forecasting(name, "markets_list", values,
                              {"client": client_facts(record), "claimed_sources": declaration["claimed_sources"]},
                              "markets_list", lambda data: check_markets_list(data, years),
                              cache.get(name), web_search)
    results[name] = listing
    if not listing["ok"]:
        return None

    # 1b. History + outlook, one request per market
    markets = {**listing["data"], "series": {}}
    for market in listing["data"]["relevant_markets"]:
        market_id = market["market_id"]
        name = f"call1_markets.series.{market_id}"
        piece = run_forecasting(name, "market_series",
                                {**values, "market_id": market_id, "market_name": market["name"],
                                 "indicator": market["indicator"]},
                                {"market": market, "client": client_facts(record)}, "market_series",
                                lambda data, m=market_id: check_market_series(data, m, list(range(start, end + 1)), years),
                                cache.get(name), web_search)
        results[name] = piece
        if not piece["ok"]:
            return None
        markets["series"][market_id] = piece["data"]
    return markets


def forecast_earnings(record: dict, markets: dict,
                      cache: dict, results: dict) -> dict | None:
    """Call 2: expected pay for every job, future pay, and how each asset behaves."""
    _, end = common.career_years(record)
    years = common.future_years(record)
    market_ids = set(markets["series"])
    claimed_figure_keys = set(forecast_settings()["claimed_figure_keys"])
    earnings = {"positions": [], "assets": []}
    earlier_pay = []

    # 2a. Past pay, one request per job (each one sees the pay from the jobs before it)
    for index, job in enumerate(common.career_positions(record)):
        first, last = int(job["start_year"]), common.position_end(job, end)
        name = f"call2_earnings.position.{index}"
        piece = run_forecasting(name, "position_pay",
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

    # 2b. Future pay, from his last 3 years of pay and the market outlooks
    name = "call2_earnings.future_pay"
    piece = run_forecasting(name, "future_pay", {"forecast_years": _years_text(years)},
                            {"recent_pay_typical": earlier_pay[-3:], "client": client_facts(record),
                             "markets": _markets_brief(markets), "market_outlooks": _market_outlooks(markets)},
                            "future_pay", lambda data: check_future_pay(data, years), cache.get(name))
    results[name] = piece
    if not piece["ok"]:
        return None
    earnings["future_pay"] = piece["data"]

    # 2c. How each asset behaves, one request per asset (claimed values removed)
    for index, asset in enumerate(record.get("investments", [])):
        bought = int(asset["year_acquired"])
        name = f"call2_earnings.asset.{index}"
        piece = run_forecasting(name, "asset_model",
                                {"asset_index": index, "bought_year": bought, "end_year": end,
                                 "forecast_years": _years_text(years)},
                                {"asset": strip_keys({"asset_index": index, **asset}, claimed_figure_keys),
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


def forecast_types(record: dict, declaration: dict, markets: dict,
                   earnings: dict, cache: dict, results: dict) -> dict | None:
    """Call 3: the person types, then one behaviour forecast per type.

    The types are T1 (honest) plus the standard crime categories his jobs, assets and
    declared sources make possible, each tied to what enables it.
    """
    start, end = common.career_years(record)
    years = common.future_years(record)
    values = {"start_year": start, "end_year": end, "forecast_years": _years_text(years),
              "last_forecast_year": years[-1], "baseline_type": common.BASELINE_TYPE,
              "honest_category": forecast_settings()["honest_category"],
              "max_crime_types": forecast_settings()["max_crime_types"]}
    claimed_sources = declaration["claimed_sources"]

    # 3a. The list of types (years are repaired first, then checked)
    name = "call3_types.list"
    listing = run_forecasting(name, "person_types", values,
                              {"client": client_facts(record, include_declaration=True),
                               "claimed_sources": declaration["claimed_sources"],
                               "markets": _markets_brief(markets),
                               "career_outlook": earnings["future_pay"]["career_outlook"],
                               "crime_categories": _crime_categories()},
                              "person_types",
                              lambda data: check_person_types(repair_person_types(data, record, claimed_sources),
                                                              record, claimed_sources),
                              cache.get(name))
    results[name] = listing
    if not listing["ok"]:
        return None

    # 3b. Behaviour (extra money + signals), one request per type
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
        piece = run_forecasting(name, "type_behaviour",
                                {**values, "type_id": type_id, "type_name": person_type["name"],
                                 "baseline_note": baseline_note},
                                {"this_type": person_type, "all_types": _types_brief(types),
                                 "claimed_sources": declaration["claimed_sources"],
                                 "markets": _markets_brief(markets), "client": client_facts(record)},
                                "type_behaviour",
                                lambda data, t=type_id, o=open_years: check_type_behaviour(
                                    repair_type_behaviour(data, t, end, years, o, set(markets["series"])),
                                    t, start, end, years, set(markets["series"]), o),
                                cache.get(name))
        results[name] = piece
        if not piece["ok"]:
            return None
        types["behaviour"][type_id] = piece["data"]
    return types


def forecast_events(record: dict, markets: dict, types: dict,
                    cache: dict, results: dict) -> dict | None:
    """Call 4: specific future events, then how each type would react to them."""
    years = common.future_years(record)
    settings = forecast_settings()

    # 4a. The list of future events
    name = "call4_events.list"
    listing = run_forecasting(name, "events",
                              {"forecast_years": _years_text(years), "min_events": settings["min_events"],
                               "max_events": settings["max_events"]},
                              {"markets": _markets_brief(markets), "market_outlooks": _market_outlooks(markets),
                               "person_types": _types_brief(types), "client": client_facts(record)},
                              "events", lambda data: check_events(data, years, set(markets["series"])),
                              cache.get(name))
    results[name] = listing
    if not listing["ok"]:
        return None

    # 4b. How each type reacts to those events, one request per type
    events = {"events": listing["data"]["events"], "reactions": {}}
    event_ids = [e["event_id"] for e in events["events"]]
    for person_type in types["person_types"]:
        type_id = person_type["type_id"]
        name = f"call4_events.reactions.{type_id}"
        piece = run_forecasting(name, "type_reactions",
                                {"type_id": type_id, "type_name": person_type["name"], "event_ids": ", ".join(event_ids)},
                                {"this_type": person_type, "its_signals": types["behaviour"][type_id]["signals"],
                                 "events": events["events"]},
                                "type_reactions",
                                lambda data, t=type_id: check_type_reactions(repair_type_reactions(data, t, event_ids),
                                                                             t, event_ids),
                                cache.get(name))
        results[name] = piece
        if not piece["ok"]:
            return None
        events["reactions"][type_id] = piece["data"]
    return events


# ---- The pipeline: entry points called by main.py ----

def run_onboarding_forecast(record: dict, cache: dict | None = None) -> dict:
    """Entry point. Run Step 0 and Calls 1-4 in order, stopping at the first failure.

    cache: {piece_name: raw_text} of saved replies (from data_manager).
    Returns {"ok", "failed_step", "results": {piece: result}, "forecast": {...}}.
    Finished pieces are in "results" even after a failure, so a rerun resumes there.
    """
    cache = cache or {}
    results: dict = {}
    run = {"ok": False, "failed_step": None, "results": results, "forecast": {}}

    declaration = read_declaration(record, cache, results)
    if declaration is None:
        run["failed_step"] = "step0_declaration"
        return run
    markets = forecast_markets(record, declaration, cache, results)
    if markets is None:
        run["failed_step"] = "call1_markets"
        return run
    earnings = forecast_earnings(record, markets, cache, results)
    if earnings is None:
        run["failed_step"] = "call2_earnings"
        return run
    types = forecast_types(record, declaration, markets, earnings, cache, results)
    if types is None:
        run["failed_step"] = "call3_types"
        return run
    events = forecast_events(record, markets, types, cache, results)
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


def read_review_notes(review: dict, forecast: dict, cache: dict | None = None) -> dict:
    """Entry point. Review: officer notes -> payments, what markets did, which events happened."""
    name = f"review.{review.get('current_date', 'undated')}"
    markets = forecast["markets"]
    events = forecast["events"]["events"]
    user_data = {
        "officer_notes": wrap_free_text(review.get("officer_notes", ""), *forecast_settings()["notes_markers"]),
        "markets": _markets_brief(markets),
        "forecast_events": [{k: e[k] for k in ("event_id", "name", "year")} for e in events],
    }
    market_ids = set(markets["series"])
    event_ids = {e["event_id"] for e in events}
    return run_forecasting(name, "review", {"review_date": review.get("current_date")}, user_data, "review",
                           lambda data: check_review(data, market_ids, event_ids), (cache or {}).get(name))