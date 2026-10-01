"""Tests for app/ai_manager.py — no network, all OpenAI calls are mocked."""

import json
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from app import ai_manager
from app.ai_manager import _call_ai, _call_ai_research, get_research, get_typologies
from app.utilities import cache_key as _cache_key, load_schema as _load_schema

# ---------------------------------------------------------------------------
# Schema loaded once for schema-validation tests
# ---------------------------------------------------------------------------

TYPOLOGY_SCHEMA = _load_schema("typology_response.schema.json")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["value"],
    "additionalProperties": False,
    "properties": {"value": {"type": "string"}},
}

_VALID_BODY = {"value": "hello"}
_INVALID_BODY = {"wrong_field": "oops"}

_PROMPT = "# prompt_version: test-v1\nSome prompt text."

_SAMPLE_PAYLOAD = {
    "occupation": "COO",
    "industry": "Shipping",
    "age": 51,
    "career_start_year": 1998,
    "country": "SG",
}


def _fake_response(body: dict) -> MagicMock:
    """Build a minimal fake OpenAI chat-completion response."""
    msg = MagicMock()
    msg.content = json.dumps(body)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _valid_typology(typology_id: str = "test_typology") -> dict:
    return {
        "typology_id": typology_id,
        "name": "Test typology name",
        "description": "First sentence about the pattern. Second sentence.",
        "source_reference": "FATF, Trade-Based Money Laundering, 2020",
        "source_url": "https://www.fatf-gafi.org/publications/methodsandtrends/documents/trade-based-money-laundering.html",
        "source_quote": "Trade-based money laundering is one of the main methods used by criminal organisations.",
        "indicators": [
            {"indicator": "offshore_entity_present", "weight": "core"},
            {"indicator": "counterparty_unnamed", "weight": "supporting"},
        ],
        "typical_documents": ["Bank statements", "Invoices"],
    }


def _valid_typology_response(typologies=None) -> dict:
    return {
        "sector_typologies": typologies if typologies is not None else [_valid_typology()],
        "expected_jurisdictions": ["SG", "MY"],
        "max_accumulation_per_year_sgd": 500000,
    }


# ---------------------------------------------------------------------------
# 1. Cache key determinism
# ---------------------------------------------------------------------------

def test_cache_key_same_input_gives_same_key():
    k1 = _cache_key("typology", {"industry": "shipping"}, "typology-v1", "gpt-4o")
    k2 = _cache_key("typology", {"industry": "shipping"}, "typology-v1", "gpt-4o")
    assert k1 == k2


def test_cache_key_different_prompt_version_gives_different_key():
    k1 = _cache_key("typology", {"industry": "shipping"}, "typology-v1", "gpt-4o")
    k2 = _cache_key("typology", {"industry": "shipping"}, "typology-v2", "gpt-4o")
    assert k1 != k2


# ---------------------------------------------------------------------------
# 2. Cache hit makes no API call
# ---------------------------------------------------------------------------

def test_cache_hit_makes_no_api_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    key = _cache_key("test", {"x": 1}, "test-v1", "gpt-4o")
    cached = {**_VALID_BODY, "model": "gpt-4o"}
    (tmp_path / f"{key}.json").write_text(json.dumps(cached))

    with patch("app.ai_manager.client") as mock_client_fn:
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    mock_client_fn.assert_not_called()
    assert result["value"] == "hello"
    assert warnings == []


# ---------------------------------------------------------------------------
# 3. Retry: one bad response then a good one → succeeds, no user warnings
# ---------------------------------------------------------------------------

def test_retry_succeeds_after_one_bad_response(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        side_effect=[_fake_response(_INVALID_BODY), _fake_response(_VALID_BODY)]
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    assert result is not None
    assert result["value"] == "hello"
    assert mock_client.chat.completions.create.call_count == 2
    assert warnings == []  # retry succeeded — no user-facing warning


# ---------------------------------------------------------------------------
# 4. Two failures → (None, one-line warning), no exception raised
# ---------------------------------------------------------------------------

def test_two_failures_return_none_with_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        side_effect=[_fake_response(_INVALID_BODY), _fake_response(_INVALID_BODY)]
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    assert result is None
    assert len(warnings) == 1  # exactly one short user-facing line
    assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# 5. No API key + cache miss → (None, warning)
# ---------------------------------------------------------------------------

def test_no_api_key_cache_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)

    with patch("app.ai_manager.client") as mock_client_fn:
        result, warnings = _call_ai("test", {"x": 99}, _PROMPT, _SIMPLE_SCHEMA)

    mock_client_fn.assert_not_called()
    assert result is None
    assert len(warnings) == 1
    assert "key" in warnings[0].lower() or "api" in warnings[0].lower()


# ---------------------------------------------------------------------------
# 6–10. Schema validation tests (no network needed)
# ---------------------------------------------------------------------------

def test_valid_typology_fixture_passes_schema():
    jsonschema.validate(instance=_valid_typology_response(), schema=TYPOLOGY_SCHEMA)


def test_empty_typologies_passes_schema():
    data = _valid_typology_response(typologies=[])
    jsonschema.validate(instance=data, schema=TYPOLOGY_SCHEMA)


def test_missing_source_url_fails_schema():
    t = _valid_typology()
    del t["source_url"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_valid_typology_response(typologies=[t]),
            schema=TYPOLOGY_SCHEMA,
        )


def test_extra_risk_level_field_fails_schema():
    t = {**_valid_typology(), "risk_level": "high"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_valid_typology_response(typologies=[t]),
            schema=TYPOLOGY_SCHEMA,
        )


def test_no_core_indicator_fails_schema():
    t = _valid_typology()
    t["indicators"] = [
        {"indicator": "offshore_entity_present", "weight": "supporting"},
        {"indicator": "counterparty_unnamed", "weight": "supporting"},
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_valid_typology_response(typologies=[t]),
            schema=TYPOLOGY_SCHEMA,
        )


# ---------------------------------------------------------------------------
# 11. 6 typologies → trimmed to 5 with a warning
# ---------------------------------------------------------------------------

def test_six_typologies_trimmed_to_five(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    six = [_valid_typology(f"typology_{i}") for i in range(6)]
    response_body = _valid_typology_response(typologies=six)

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=_fake_response(response_body)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = get_typologies(_SAMPLE_PAYLOAD)

    assert result is not None
    assert len(result["sector_typologies"]) == 5
    assert len(warnings) == 1
    assert "5" in warnings[0]


# ---------------------------------------------------------------------------
# 12–15. Error-type messages: each failure gives the right short user line
# ---------------------------------------------------------------------------

def test_api_error_message_mentions_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        side_effect=RuntimeError("connection refused")
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    assert result is None
    assert len(warnings) == 1
    w = warnings[0].lower()
    assert "openai" in w or "internet" in w or "api key" in w


def test_json_error_message_mentions_json(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    bad_json_response = MagicMock()
    bad_json_response.choices[0].message.content = "not valid json {{{"

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(return_value=bad_json_response)

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    assert result is None
    assert len(warnings) == 1
    assert "json" in warnings[0].lower()


def test_schema_error_message_mentions_format(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=_fake_response(_INVALID_BODY)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    assert result is None
    assert len(warnings) == 1
    assert "format" in warnings[0].lower()


# ---------------------------------------------------------------------------
# 16. Raw AI response content never appears in the user-facing warning
# ---------------------------------------------------------------------------

def test_raw_response_content_not_in_warning(tmp_path, monkeypatch):
    """The raw malformed AI output must never leak into the warning shown to the user."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    sentinel = "SENTINEL_RAW_CONTENT_12345"
    bad_json_response = MagicMock()
    bad_json_response.choices[0].message.content = sentinel + " {{{"

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(return_value=bad_json_response)

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    assert result is None
    for w in warnings:
        assert sentinel not in w


# ---------------------------------------------------------------------------
# 17. No console logging: logging goes only to file, not stderr
# ---------------------------------------------------------------------------

def test_no_console_logging_on_failure(tmp_path, monkeypatch, capsys):
    """AI call failures must not produce any console (stderr) output via logging."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        side_effect=[_fake_response(_INVALID_BODY), _fake_response(_INVALID_BODY)]
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        _call_ai("test", {"x": 1}, _PROMPT, _SIMPLE_SCHEMA)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# ===========================================================================
# Research call tests (Responses API with web_search)
# ===========================================================================

RESEARCH_SCHEMA = _load_schema("research_response.schema.json")

_RESEARCH_PROMPT = "# prompt_version: research-v1\nSearch for ML reports."

_SAMPLE_RESEARCH_PAYLOAD = {
    "industry": "Maritime shipping and freight forwarding",
    "country": "Singapore",
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

_DOMAINS = ["fatf-gafi.org", "mas.gov.sg"]


def _valid_report(title: str = "Test Report") -> dict:
    return {
        "organisation": "FATF",
        "title": title,
        "year": 2020,
        "url": "https://www.fatf-gafi.org/test-report",
        "excerpts": ["Excerpt from the report."],
    }


def _valid_research_response(reports=None) -> dict:
    return {"reports": reports if reports is not None else [_valid_report()]}


def _fake_responses_api_response(body: dict, annotations=None) -> MagicMock:
    """Build a fake Responses API response with output items and annotations."""
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
    """Build a fake web_search_call output item with action.sources."""
    sources = []
    for url in urls:
        src = MagicMock()
        src.url = url
        sources.append(src)
    action = MagicMock()
    action.type = "search"
    action.sources = sources
    item = MagicMock()
    item.type = "web_search_call"
    item.action = action
    return item


# ---------------------------------------------------------------------------
# 18. Research payload has exactly 2 keys
# ---------------------------------------------------------------------------

def test_research_payload_has_only_industry_and_country():
    from app.ai_manager import _build_research_payload
    payload = _build_research_payload(_SAMPLE_CASE_INPUT)
    assert set(payload.keys()) == {"industry", "country"}
    assert payload["industry"] == "Maritime shipping and freight forwarding"
    assert payload["country"] == "Singapore"


# ---------------------------------------------------------------------------
# 19. Valid research fixture passes schema
# ---------------------------------------------------------------------------

def test_valid_research_fixture_passes_schema():
    jsonschema.validate(instance=_valid_research_response(), schema=RESEARCH_SCHEMA)


# ---------------------------------------------------------------------------
# 20. Empty reports list passes schema
# ---------------------------------------------------------------------------

def test_empty_research_reports_passes_schema():
    jsonschema.validate(instance=_valid_research_response(reports=[]), schema=RESEARCH_SCHEMA)


# ---------------------------------------------------------------------------
# 21. Report with no url fails schema
# ---------------------------------------------------------------------------

def test_report_missing_url_fails_schema():
    r = _valid_report()
    del r["url"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_valid_research_response(reports=[r]), schema=RESEARCH_SCHEMA,
        )


# ---------------------------------------------------------------------------
# 22. Extra risk_level field fails schema
# ---------------------------------------------------------------------------

def test_report_extra_risk_level_fails_schema():
    r = {**_valid_report(), "risk_level": "high"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_valid_research_response(reports=[r]), schema=RESEARCH_SCHEMA,
        )


# ---------------------------------------------------------------------------
# 23. 9 reports trimmed to 8 with a warning
# ---------------------------------------------------------------------------

def test_nine_reports_trimmed_to_eight(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    nine = [_valid_report(f"Report {i}") for i in range(9)]
    body = _valid_research_response(reports=nine)

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        return_value=_fake_responses_api_response(body)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS, transform=ai_manager._trim_reports,
        )

    assert result is not None
    assert len(result["reports"]) == 8
    # One trim warning + one no-sources warning (fake response has no web_search_call sources)
    trim_warnings = [w for w in warnings if "8" in w]
    assert len(trim_warnings) == 1


# ---------------------------------------------------------------------------
# 24. report_ids are added by code as R1, R2, ...
# ---------------------------------------------------------------------------

def test_report_ids_added_by_code(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    body = _valid_research_response(reports=[_valid_report("A"), _valid_report("B")])

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        return_value=_fake_responses_api_response(body)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS,
        )

    assert result is not None
    assert result["reports"][0]["report_id"] == "R1"
    assert result["reports"][1]["report_id"] == "R2"


# ---------------------------------------------------------------------------
# 25. api_sources captured from fake API response annotations
# ---------------------------------------------------------------------------

def test_api_sources_captured_from_annotations(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    body = _valid_research_response()
    annotations = [
        _fake_url_citation("https://www.fatf-gafi.org/page1", "FATF Page 1"),
        _fake_url_citation("https://www.mas.gov.sg/page2", "MAS Page 2"),
    ]

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        return_value=_fake_responses_api_response(body, annotations=annotations)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS,
        )

    assert result is not None
    assert len(result["api_sources"]) == 2
    assert result["api_sources"][0]["url"] == "https://www.fatf-gafi.org/page1"
    assert result["api_sources"][1]["title"] == "MAS Page 2"


# ---------------------------------------------------------------------------
# 26. Same industry + country = same cache key regardless of other fields
# ---------------------------------------------------------------------------

def test_research_cache_key_ignores_non_research_fields():
    k1 = _cache_key("research", {"industry": "Shipping", "country": "SG"}, "research-v1", "gpt-4o")
    k2 = _cache_key("research", {"industry": "Shipping", "country": "SG"}, "research-v1", "gpt-4o")
    assert k1 == k2

    # Different age/occupation shouldn't matter because payload only has industry+country
    from app.ai_manager import _build_research_payload
    case_a = {"industry": "Shipping", "country": "SG", "age": 30, "occupation": "Analyst"}
    case_b = {"industry": "Shipping", "country": "SG", "age": 55, "occupation": "Director"}
    payload_a = _build_research_payload(case_a)
    payload_b = _build_research_payload(case_b)
    assert payload_a == payload_b  # Both are {"industry": "Shipping", "country": "SG"}


# ---------------------------------------------------------------------------
# 27. API failure → None plus a short warning, no exception
# ---------------------------------------------------------------------------

def test_research_api_failure_returns_none_with_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        side_effect=RuntimeError("network error")
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS,
        )

    assert result is None
    assert len(warnings) == 1
    assert "openai" in warnings[0].lower() or "internet" in warnings[0].lower()


# ---------------------------------------------------------------------------
# 28. get_research() integration — passes only 2 fields, returns result
# ---------------------------------------------------------------------------

def test_get_research_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    body = _valid_research_response()

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        return_value=_fake_responses_api_response(body)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = get_research(_SAMPLE_CASE_INPUT)

    assert result is not None
    assert len(result["reports"]) == 1
    assert result["reports"][0]["report_id"] == "R1"
    assert "api_sources" in result
    assert "retrieved_at" in result
    assert "model" in result

    # Verify only industry + country were sent — check the prompt
    call_args = mock_client.responses.create.call_args
    prompt_sent = call_args.kwargs.get("input", "")
    assert "Maritime shipping" in prompt_sent
    assert "Singapore" in prompt_sent
    # Client-specific data NOT in prompt
    assert "Chief Operating Officer" not in prompt_sent
    assert "ID-2233" not in prompt_sent


# ---------------------------------------------------------------------------
# 29. api_sources from web_search_call.action.sources (primary method)
# ---------------------------------------------------------------------------

def test_api_sources_from_web_search_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    body = _valid_research_response()
    ws_item = _fake_web_search_item([
        "https://www.fatf-gafi.org/search-result-1",
        "https://www.mas.gov.sg/search-result-2",
    ])

    text_block = MagicMock()
    text_block.type = "output_text"
    text_block.text = json.dumps(body)
    text_block.annotations = []

    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [text_block]

    resp = MagicMock()
    resp.output = [ws_item, message_item]

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(return_value=resp)

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS,
        )

    assert result is not None
    assert len(result["api_sources"]) == 2
    assert result["api_sources"][0]["url"] == "https://www.fatf-gafi.org/search-result-1"
    assert result["api_sources"][1]["url"] == "https://www.mas.gov.sg/search-result-2"
    # No no-sources warning because sources were found
    assert not any("no sources" in w for w in warnings)


# ---------------------------------------------------------------------------
# 30. Empty api_sources → no-sources warning present
# ---------------------------------------------------------------------------

def test_empty_api_sources_gives_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    body = _valid_research_response()
    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        return_value=_fake_responses_api_response(body)  # no web_search_call, no annotations
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS,
        )

    assert result is not None
    assert result["api_sources"] == []
    assert any("no sources" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# 31. Excerpt curly quotes are stripped
# ---------------------------------------------------------------------------

def test_excerpt_curly_quotes_stripped(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    report = _valid_report()
    report["excerpts"] = [
        "\u201cThis is a quoted excerpt.\u201d",
        "\u2018Another excerpt\u2019",
        '"Double quoted"',
    ]
    body = _valid_research_response(reports=[report])

    mock_client = MagicMock()
    mock_client.responses.create = MagicMock(
        return_value=_fake_responses_api_response(body)
    )

    with patch("app.ai_manager.client", return_value=mock_client):
        result, warnings = _call_ai_research(
            "research", _SAMPLE_RESEARCH_PAYLOAD, _RESEARCH_PROMPT,
            RESEARCH_SCHEMA, _DOMAINS,
        )

    assert result is not None
    excerpts = result["reports"][0]["excerpts"]
    assert excerpts[0] == "This is a quoted excerpt."
    assert excerpts[1] == "Another excerpt"
    assert excerpts[2] == "Double quoted"
