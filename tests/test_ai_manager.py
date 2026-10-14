"""Tests for app/ai_manager.py — no network, all AI calls are mocked."""

import json
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from app.ai_manager import (
    _ai_chat_completion_pipeline,
    _ai_search_pipeline,
    _convert_schema_for_gemini,
    add_report_ids,
    build_research_payload,
    build_typology_reports,
    extract_api_sources,
    get_ai_research,
    get_typologies,
    strip_excerpts,
    trim_reports,
    validate_ai_response,
)
from app.utilities import cache_key as _cache_key, load_schema as _load_schema

# ---------------------------------------------------------------------------
# Schemas loaded once for validation tests
# ---------------------------------------------------------------------------

TYPOLOGY_SCHEMA = _load_schema("typology_response.schema.json")
RESEARCH_SCHEMA = _load_schema("research_response.schema.json")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SAMPLE_PAYLOAD = {
    "occupation": "COO",
    "industry": "Shipping",
    "age": 51,
    "career_start_year": 1998,
    "country": "SG",
}

_SAMPLE_CASE_INPUT = {
    "client_ref": "ID-2233",
    "occupation": "Chief Operating Officer",
    "industry": "Maritime shipping and freight forwarding",
    "age": 51,
    "career_start_year": 1998,
    "country": "Singapore",
    "country_of_residence": "Singapore",
    "declaration_text": "Some text.",
}

_SAMPLE_RESEARCH_PAYLOAD = {
    "industry": "Maritime shipping and freight forwarding",
    "country": "Singapore",
}

_SAMPLE_RESEARCH = {
    "reports": [
        {
            "report_id": "R1",
            "organisation": "FATF",
            "title": "Trade Based Money Laundering",
            "year": 2006,
            "url": "https://www.fatf-gafi.org/publications/trade-based-money-laundering.html",
            "excerpts": ["Trade-based money laundering is one of the main methods."],
            "summary": ["Describes how shipping invoices are manipulated to move value."],
        }
    ],
    "api_sources": [],
    "retrieved_at": "2026-10-01T00:00:00+00:00",
    "model": "gpt-5.4",
}

_DEFAULT_REPORT_FIELDS = ["report_id", "organisation", "title", "year", "summary", "excerpts"]

# Keys stripped from Gemini schemas — mirrors policy.json gemini_unsupported_schema_keys
_GEMINI_UNSUPPORTED = {"$schema", "$id", "title", "additionalProperties", "required"}


def _valid_typology(typology_id: str = "test_typology") -> dict:
    return {
        "typology_id": typology_id,
        "name": "Test typology name",
        "crime_category": "TRADE_BASED_ML",
        "description": "First sentence about the pattern. Second sentence.",
        "source_ids": ["R1"],
        "source_quotes": [
            {
                "report_id": "R1",
                "quote": "Trade-based money laundering is one of the main methods.",
            }
        ],
        "indicators": [
            {"indicator": "source_contradicts_occupation", "weight": "core"},
            {"indicator": "counterparty_unnamed", "weight": "supporting"},
        ],
        "typical_documents": ["Bank statements", "Invoices"],
    }


def _valid_typology_response(typologies=None) -> dict:
    return {
        "reasoning": "Reports matched this industry. Typologies were generated based on known patterns.",
        "sector_typologies": typologies if typologies is not None else [_valid_typology()],
        "expected_jurisdictions": ["SG", "MY"],
        "max_accumulation_per_year_sgd": 500000,
    }


def _valid_report(title: str = "Test Report") -> dict:
    return {
        "organisation": "FATF",
        "title": title,
        "year": 2020,
        "url": "https://www.fatf-gafi.org/test-report",
        "excerpts": ["Excerpt from the report."],
        "summary": ["Over-invoicing of goods to move funds.", "Use of shell companies."],
    }


def _valid_research_response(reports=None) -> dict:
    return {"reports": reports if reports is not None else [_valid_report()]}


def _fake_responses_api_response(body: dict, annotations=None) -> MagicMock:
    """Fake OpenAI Responses API response (used by web search calls)."""
    ann_list = annotations if annotations is not None else []
    text_block = MagicMock()
    text_block.type = "output_text"
    text_block.text = json.dumps(body)
    text_block.annotations = ann_list
    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [text_block]
    resp = MagicMock()
    resp.output = [message_item]
    return resp


def _fake_url_citation(url: str, title: str) -> MagicMock:
    ann = MagicMock()
    ann.type = "url_citation"
    ann.url = url
    ann.title = title
    return ann


def _fake_web_search_item(urls: list[str]) -> MagicMock:
    """Fake web_search_call output item with action.sources."""
    sources = [MagicMock(url=url) for url in urls]
    action = MagicMock(type="search", sources=sources)
    item = MagicMock()
    item.type = "web_search_call"
    item.action = action
    return item


# ===========================================================================
# 1. Cache key determinism
# ===========================================================================

def test_cache_key_same_input_gives_same_key():
    k1 = _cache_key("typology", {"industry": "shipping"}, "typology-v1", "gpt-4o")
    k2 = _cache_key("typology", {"industry": "shipping"}, "typology-v1", "gpt-4o")
    assert k1 == k2


def test_cache_key_different_prompt_version_gives_different_key():
    k1 = _cache_key("typology", {"industry": "shipping"}, "typology-v1", "gpt-4o")
    k2 = _cache_key("typology", {"industry": "shipping"}, "typology-v2", "gpt-4o")
    assert k1 != k2


# ===========================================================================
# 2. _convert_schema_for_gemini — Gemini schema compatibility converter
# ===========================================================================

def test_convert_schema_strips_unsupported_meta_keys():
    """Meta-fields like $schema, $id, title, additionalProperties are stripped."""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "some-id",
        "title": "Response Schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
    }
    result = _convert_schema_for_gemini(schema, unsupported=_GEMINI_UNSUPPORTED)
    assert "$schema" not in result
    assert "$id" not in result
    assert "title" not in result
    assert "additionalProperties" not in result
    assert result["type"] == "object"
    assert "properties" in result


def test_convert_schema_preserves_property_names_matching_unsupported_keys():
    """Property names like 'title', 'url' inside properties must NOT be stripped."""
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "summary": {"type": "string"},
        },
    }
    result = _convert_schema_for_gemini(schema, unsupported=_GEMINI_UNSUPPORTED)
    assert "title" in result["properties"]
    assert "url" in result["properties"]
    assert "summary" in result["properties"]


def test_convert_schema_strips_required_at_top_level():
    schema = {
        "type": "object",
        "required": ["name", "age"],
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
    }
    result = _convert_schema_for_gemini(schema, unsupported=_GEMINI_UNSUPPORTED)
    assert "required" not in result
    assert "name" in result["properties"]
    assert "age" in result["properties"]


def test_convert_schema_strips_required_from_nested_items():
    """required inside array items must also be stripped (Gemini rejects it)."""
    schema = {
        "type": "object",
        "properties": {
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["title", "url"],
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
            }
        },
    }
    result = _convert_schema_for_gemini(schema, unsupported=_GEMINI_UNSUPPORTED)
    nested_items = result["properties"]["sources"]["items"]
    assert "required" not in nested_items
    assert "title" in nested_items["properties"]
    assert "url" in nested_items["properties"]


def test_convert_schema_converts_nullable_type_array():
    """["string", "null"] must become {type: "string", nullable: true}."""
    schema = {
        "type": "object",
        "properties": {
            "comment": {"type": ["string", "null"]},
            "count": {"type": ["integer", "null"]},
        },
    }
    result = _convert_schema_for_gemini(schema, unsupported=_GEMINI_UNSUPPORTED)
    assert result["properties"]["comment"]["type"] == "string"
    assert result["properties"]["comment"]["nullable"] is True
    assert result["properties"]["count"]["type"] == "integer"
    assert result["properties"]["count"]["nullable"] is True


# ===========================================================================
# 3. _ai_chat_completion_pipeline — OpenAI first, Gemini fallback
# ===========================================================================

def test_chat_pipeline_uses_openai_first():
    """If OpenAI succeeds, Gemini is never called."""
    with patch("app.ai_manager._openai_completion_response", return_value='{"x":1}') as mock_openai, \
         patch("app.ai_manager._gemini_completion_response") as mock_gemini:
        result = _ai_chat_completion_pipeline("prompt")
    mock_openai.assert_called_once()
    mock_gemini.assert_not_called()
    assert result == '{"x":1}'


def test_chat_pipeline_falls_back_to_gemini_when_openai_fails():
    """If OpenAI returns None, Gemini is called as fallback."""
    with patch("app.ai_manager._openai_completion_response", return_value=None), \
         patch("app.ai_manager._gemini_completion_response", return_value='{"x":2}') as mock_gemini:
        result = _ai_chat_completion_pipeline("prompt")
    mock_gemini.assert_called_once()
    assert result == '{"x":2}'


def test_chat_pipeline_returns_none_when_both_fail():
    with patch("app.ai_manager._openai_completion_response", return_value=None), \
         patch("app.ai_manager._gemini_completion_response", return_value=None):
        result = _ai_chat_completion_pipeline("prompt")
    assert result is None


# ===========================================================================
# 4. _ai_search_pipeline — OpenAI search first, Gemini grounding fallback
# ===========================================================================

def test_search_pipeline_uses_openai_first():
    """If OpenAI search succeeds, Gemini is never called."""
    fake_response = MagicMock()
    with patch("app.ai_manager._openai_search_response", return_value=('{"x":1}', fake_response)) as mock_openai, \
         patch("app.ai_manager._gemini_search_response") as mock_gemini:
        text, response = _ai_search_pipeline("prompt", schema={})
    mock_openai.assert_called_once()
    mock_gemini.assert_not_called()
    assert text == '{"x":1}'


def test_search_pipeline_falls_back_to_gemini_when_openai_fails():
    """If OpenAI search returns None, Gemini grounding is called as fallback."""
    fake_response = MagicMock()
    with patch("app.ai_manager._openai_search_response", return_value=(None, None)), \
         patch("app.ai_manager._gemini_search_response", return_value=('{"x":2}', fake_response)) as mock_gemini:
        text, response = _ai_search_pipeline("prompt", schema={})
    mock_gemini.assert_called_once()
    assert text == '{"x":2}'


def test_search_pipeline_returns_none_tuple_when_both_fail():
    with patch("app.ai_manager._openai_search_response", return_value=(None, None)), \
         patch("app.ai_manager._gemini_search_response", return_value=(None, None)):
        text, response = _ai_search_pipeline("prompt", schema={})
    assert text is None
    assert response is None


# ===========================================================================
# 5. Cache hit — no API call made
# ===========================================================================

def test_cache_hit_makes_no_api_call():
    """A cached typology result is returned without calling any AI."""
    cached = _valid_typology_response()
    with patch("app.ai_manager.cache_check", return_value=("fake-key", cached)), \
         patch("app.ai_manager._ai_chat_completion_pipeline") as mock_pipeline:
        result, warnings = get_typologies(_SAMPLE_PAYLOAD, _SAMPLE_RESEARCH)
    mock_pipeline.assert_not_called()
    assert result == cached
    assert warnings == []


# ===========================================================================
# 6. Retry — one bad response then a good one
# ===========================================================================

def test_retry_succeeds_after_one_bad_response(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    bad = json.dumps({"wrong": "field"})
    good = json.dumps(_valid_typology_response())

    with patch("app.ai_manager.cache_check", return_value=("key", None)), \
         patch("app.ai_manager._ai_chat_completion_pipeline", side_effect=[bad, good]):
        result, warnings = get_typologies(_SAMPLE_PAYLOAD, _SAMPLE_RESEARCH)

    assert result is not None
    assert len(result["sector_typologies"]) == 1
    assert warnings == []


# ===========================================================================
# 7. Both retries fail → (None, one warning), no exception
# ===========================================================================

def test_two_failures_return_none_with_empty_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    bad = json.dumps({"wrong": "field"})

    with patch("app.ai_manager.cache_check", return_value=("key", None)), \
         patch("app.ai_manager._ai_chat_completion_pipeline", side_effect=[bad, bad]):
        result, warnings = get_typologies(_SAMPLE_PAYLOAD, _SAMPLE_RESEARCH)

    assert result is None
    assert warnings == []


# ===========================================================================
# 8. Both providers unavailable → (None, []), no exception raised
# ===========================================================================

def test_both_providers_unavailable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with patch("app.ai_manager.cache_check", return_value=("key", None)):
        result, warnings = get_typologies(_SAMPLE_PAYLOAD)

    assert result is None
    assert warnings == []


# ===========================================================================
# 9. No console output on failure (logging goes to file only)
# ===========================================================================

def test_no_console_output_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    with patch("app.ai_manager.cache_check", return_value=("key", None)), \
         patch("app.ai_manager._ai_chat_completion_pipeline", return_value=None):
        get_typologies(_SAMPLE_PAYLOAD)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# ===========================================================================
# 10–14. Typology schema validation (no network needed)
# ===========================================================================

def test_valid_typology_fixture_passes_schema():
    jsonschema.validate(instance=_valid_typology_response(), schema=TYPOLOGY_SCHEMA)


def test_empty_typologies_passes_schema():
    jsonschema.validate(instance=_valid_typology_response(typologies=[]), schema=TYPOLOGY_SCHEMA)


def test_missing_source_ids_fails_schema():
    t = _valid_typology()
    del t["source_ids"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_typology_response(typologies=[t]), schema=TYPOLOGY_SCHEMA)


def test_extra_risk_level_field_fails_schema():
    t = {**_valid_typology(), "risk_level": "high"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_typology_response(typologies=[t]), schema=TYPOLOGY_SCHEMA)


def test_no_core_indicator_fails_schema():
    t = _valid_typology()
    t["indicators"] = [
        {"indicator": "source_contradicts_occupation", "weight": "supporting"},
        {"indicator": "counterparty_unnamed", "weight": "supporting"},
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_typology_response(typologies=[t]), schema=TYPOLOGY_SCHEMA)


# ===========================================================================
# 15. 6 typologies → trimmed to 5 with a warning
# ===========================================================================

def test_six_typologies_trimmed_to_five(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    six = [_valid_typology(f"typology_{i}") for i in range(6)]
    body = json.dumps(_valid_typology_response(typologies=six))

    with patch("app.ai_manager.cache_check", return_value=("key", None)), \
         patch("app.ai_manager._ai_chat_completion_pipeline", return_value=body):
        result, warnings = get_typologies(_SAMPLE_PAYLOAD, _SAMPLE_RESEARCH)

    assert result is not None
    assert len(result["sector_typologies"]) == 5
    assert any("5" in w for w in warnings)


# ===========================================================================
# 16. Typology payload contains reports but no URLs
# ===========================================================================

def test_typology_prompt_contains_reports_but_not_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    captured = []

    def fake_pipeline(prompt, schema=None):
        captured.append(prompt)
        return json.dumps(_valid_typology_response())

    with patch("app.ai_manager.cache_check", return_value=("key", None)), \
         patch("app.ai_manager._ai_chat_completion_pipeline", side_effect=fake_pipeline):
        get_typologies(_SAMPLE_PAYLOAD, _SAMPLE_RESEARCH)

    assert len(captured) == 1
    prompt_text = captured[0]
    assert "FATF" in prompt_text
    # URL from the research report must NOT be sent to the typology AI
    assert "fatf-gafi.org/publications" not in prompt_text


# ===========================================================================
# 17. Research schema validation (no network needed)
# ===========================================================================

def test_valid_research_fixture_passes_schema():
    jsonschema.validate(instance=_valid_research_response(), schema=RESEARCH_SCHEMA)


def test_empty_research_reports_passes_schema():
    jsonschema.validate(instance=_valid_research_response(reports=[]), schema=RESEARCH_SCHEMA)


def test_report_missing_url_fails_schema():
    r = _valid_report()
    del r["url"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_research_response(reports=[r]), schema=RESEARCH_SCHEMA)


def test_report_extra_risk_level_fails_schema():
    r = {**_valid_report(), "risk_level": "high"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_research_response(reports=[r]), schema=RESEARCH_SCHEMA)


# ===========================================================================
# 18. build_research_payload — only industry + country
# ===========================================================================

def test_research_payload_has_only_industry_and_country():
    payload = build_research_payload(_SAMPLE_CASE_INPUT)
    assert set(payload.keys()) == {"industry", "country"}
    assert payload["industry"] == "Maritime shipping and freight forwarding"
    assert payload["country"] == "Singapore"


def test_research_payload_falls_back_to_country_of_residence():
    case = {**_SAMPLE_CASE_INPUT}
    del case["country"]
    payload = build_research_payload(case)
    assert payload["country"] == "Singapore"  # falls back to country_of_residence


# ===========================================================================
# 19. trim_reports — caps reports and excerpts, coerces year strings
# ===========================================================================

def test_nine_reports_trimmed_to_eight():
    nine = [_valid_report(f"Report {i}") for i in range(9)]
    data = _valid_research_response(reports=nine)
    result, warnings = trim_reports(data, max_reports=8, max_excerpts=5)
    assert len(result["reports"]) == 8
    assert any("8" in w for w in warnings)


def test_trim_reports_coerces_year_string_to_int():
    r = _valid_report()
    r["year"] = "2020"
    data = _valid_research_response(reports=[r])
    result, _ = trim_reports(data, max_reports=8, max_excerpts=5)
    assert result["reports"][0]["year"] == 2020


# ===========================================================================
# 20. add_report_ids — R1, R2, ... assigned by code, not AI
# ===========================================================================

def test_report_ids_added_as_r1_r2():
    data = _valid_research_response(reports=[_valid_report("A"), _valid_report("B")])
    result = add_report_ids(data)
    assert result["reports"][0]["report_id"] == "R1"
    assert result["reports"][1]["report_id"] == "R2"


# ===========================================================================
# 21. extract_api_sources — from web_search_call and url_citation annotations
# ===========================================================================

def test_api_sources_from_url_citation_annotations():
    annotations = [
        _fake_url_citation("https://www.fatf-gafi.org/page1", "FATF Page 1"),
        _fake_url_citation("https://www.mas.gov.sg/page2", "MAS Page 2"),
    ]
    response = _fake_responses_api_response(_valid_research_response(), annotations=annotations)
    sources = extract_api_sources(response)
    assert len(sources) == 2
    assert sources[0]["url"] == "https://www.fatf-gafi.org/page1"
    assert sources[1]["title"] == "MAS Page 2"


def test_api_sources_from_web_search_call_action():
    ws_item = _fake_web_search_item([
        "https://www.fatf-gafi.org/search-1",
        "https://www.mas.gov.sg/search-2",
    ])
    text_block = MagicMock()
    text_block.type = "output_text"
    text_block.annotations = []
    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [text_block]
    resp = MagicMock()
    resp.output = [ws_item, message_item]
    sources = extract_api_sources(resp)
    assert len(sources) == 2
    assert sources[0]["url"] == "https://www.fatf-gafi.org/search-1"


def test_api_sources_deduplicates_urls():
    annotations = [
        _fake_url_citation("https://www.fatf-gafi.org/page1", "FATF"),
        _fake_url_citation("https://www.fatf-gafi.org/page1", "FATF duplicate"),
    ]
    response = _fake_responses_api_response(_valid_research_response(), annotations=annotations)
    sources = extract_api_sources(response)
    assert len(sources) == 1


# ===========================================================================
# 22. strip_excerpts — curly quotes and double quotes stripped
# ===========================================================================

def test_excerpt_curly_and_double_quotes_stripped():
    report = _valid_report()
    report["excerpts"] = [
        "\u201cThis is a quoted excerpt.\u201d",
        "\u2018Another excerpt\u2019",
        '"Double quoted"',
    ]
    data = _valid_research_response(reports=[report])
    result = strip_excerpts(data)
    excerpts = result["reports"][0]["excerpts"]
    assert excerpts[0] == "This is a quoted excerpt."
    assert excerpts[1] == "Another excerpt"
    assert excerpts[2] == "Double quoted"


# ===========================================================================
# 23. get_ai_research integration — correct payload, post-processing applied
# ===========================================================================

def test_get_ai_research_sends_only_industry_and_country(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    body = json.dumps(_valid_research_response())
    fake_response = _fake_responses_api_response(_valid_research_response())

    with patch("app.ai_manager._ai_search_pipeline", return_value=(body, fake_response)) as mock_search:
        result, warnings = get_ai_research(_SAMPLE_CASE_INPUT)

    assert result is not None
    assert result["reports"][0]["report_id"] == "R1"
    assert "api_sources" in result
    assert "retrieved_at" in result

    # Verify the prompt sent to the search pipeline does NOT include client-specific data
    call_args = mock_search.call_args
    prompt_sent = call_args.args[0] if call_args.args else call_args.kwargs.get("prompt", "")
    assert "Chief Operating Officer" not in prompt_sent
    assert "ID-2233" not in prompt_sent
    assert "Maritime shipping" in prompt_sent


def test_get_ai_research_api_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    with patch("app.ai_manager._ai_search_pipeline", return_value=(None, None)):
        result, warnings = get_ai_research(_SAMPLE_CASE_INPUT)

    assert result is None
    assert warnings == []


def test_get_ai_research_empty_sources_gives_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    body = json.dumps(_valid_research_response())
    # Response with no web_search_call items and no annotations → empty api_sources
    fake_response = _fake_responses_api_response(_valid_research_response())

    with patch("app.ai_manager._ai_search_pipeline", return_value=(body, fake_response)):
        result, warnings = get_ai_research(_SAMPLE_CASE_INPUT)

    assert result is not None
    assert result["api_sources"] == []
    assert any("no sources" in w.lower() for w in warnings)


# ===========================================================================
# 24. build_typology_reports — URLs stripped, safe fields only
# ===========================================================================

def test_build_typology_reports_strips_url():
    result = build_typology_reports(_SAMPLE_RESEARCH, _DEFAULT_REPORT_FIELDS)
    assert len(result) == 1
    assert "url" not in result[0]
    assert result[0]["report_id"] == "R1"
    assert result[0]["organisation"] == "FATF"


def test_build_typology_reports_empty_research():
    assert build_typology_reports({"reports": []}, _DEFAULT_REPORT_FIELDS) == []
    assert build_typology_reports({}, _DEFAULT_REPORT_FIELDS) == []


def test_build_typology_reports_different_research_gives_different_content():
    research_a = {"reports": [{"report_id": "R1", "organisation": "FATF", "title": "A",
                                "year": 2020, "summary": [], "excerpts": []}]}
    research_b = {"reports": [{"report_id": "R1", "organisation": "MAS", "title": "B",
                                "year": 2021, "summary": [], "excerpts": []}]}
    a = build_typology_reports(research_a, _DEFAULT_REPORT_FIELDS)
    b = build_typology_reports(research_b, _DEFAULT_REPORT_FIELDS)
    assert a[0]["organisation"] != b[0]["organisation"]


# ===========================================================================
# 25–26. Typology schema edge cases
# ===========================================================================

def test_source_id_wrong_format_fails_schema():
    """source_ids entries must match ^R[0-9]+$."""
    t = _valid_typology()
    t["source_ids"] = ["X1"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_typology_response(typologies=[t]), schema=TYPOLOGY_SCHEMA)


def test_missing_source_quotes_fails_schema():
    t = _valid_typology()
    del t["source_quotes"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=_valid_typology_response(typologies=[t]), schema=TYPOLOGY_SCHEMA)


# ===========================================================================
# 28. Research cache key — same industry+country always gives same key
# ===========================================================================

def test_research_cache_key_ignores_non_research_fields():
    case_a = {"industry": "Shipping", "country": "SG", "age": 30, "occupation": "Analyst"}
    case_b = {"industry": "Shipping", "country": "SG", "age": 55, "occupation": "Director"}
    payload_a = build_research_payload(case_a)
    payload_b = build_research_payload(case_b)
    assert payload_a == payload_b
    k1 = _cache_key("research", payload_a, "research-v1", "gpt-4o")
    k2 = _cache_key("research", payload_b, "research-v1", "gpt-4o")
    assert k1 == k2


