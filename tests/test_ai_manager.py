"""Tests for app/ai_manager.py — no network, all OpenAI calls are mocked."""

import json
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from app import ai_manager
from app.ai_manager import _call_ai, _load_schema, get_typologies
from app.utilities import cache_key as _cache_key

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

    with patch("app.ai_manager._client") as mock_client_fn:
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

    with patch("app.ai_manager._client", return_value=mock_client):
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

    with patch("app.ai_manager._client", return_value=mock_client):
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

    with patch("app.ai_manager._client") as mock_client_fn:
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

    with patch("app.ai_manager._client", return_value=mock_client):
        result, warnings = get_typologies(_SAMPLE_PAYLOAD)

    assert result is not None
    assert len(result["sector_typologies"]) == 5
    assert len(warnings) == 1
    assert "5" in warnings[0]
