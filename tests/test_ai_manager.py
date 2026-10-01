"""Tests for app/ai_manager.py — no network, all OpenAI calls are mocked."""

import json
from unittest.mock import MagicMock, patch

from app import ai_manager
from app.ai_manager import _call_ai
from app.utilities import cache_key as _cache_key


# ---------------------------------------------------------------------------
# Minimal schema and response used across tests
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "required": ["value"],
    "additionalProperties": False,
    "properties": {"value": {"type": "string"}},
}

_VALID_BODY = {"value": "hello"}
_INVALID_BODY = {"wrong_field": "oops"}   # missing "value", extra key

_PROMPT = "# prompt_version: test-v1\nSome prompt text."


def _fake_response(body: dict) -> MagicMock:
    """Build a minimal fake OpenAI chat-completion response."""
    msg = MagicMock()
    msg.content = json.dumps(body)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


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

    # Pre-write a valid cached entry
    key = _cache_key("test", {"x": 1}, "test-v1", "gpt-4o")
    cached = {**_VALID_BODY, "model": "gpt-4o"}
    (tmp_path / f"{key}.json").write_text(json.dumps(cached))

    with patch("app.ai_manager._client") as mock_client_fn:
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SCHEMA)

    mock_client_fn.assert_not_called()
    assert result["value"] == "hello"
    assert warnings == []


# ---------------------------------------------------------------------------
# 3. One bad response then a good one → succeeds on retry
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
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SCHEMA)

    assert result is not None
    assert result["value"] == "hello"
    assert mock_client.chat.completions.create.call_count == 2
    assert len(warnings) == 1   # one "attempt 1 failed" warning


# ---------------------------------------------------------------------------
# 4. Two failures → (None, warnings), no exception raised
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
        result, warnings = _call_ai("test", {"x": 1}, _PROMPT, _SCHEMA)

    assert result is None
    assert len(warnings) >= 2   # retry warning + final failure warning
    assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# 5. No API key + cache miss → (None, warning)
# ---------------------------------------------------------------------------

def test_no_api_key_cache_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)

    with patch("app.ai_manager._client") as mock_client_fn:
        result, warnings = _call_ai("test", {"x": 99}, _PROMPT, _SCHEMA)

    mock_client_fn.assert_not_called()
    assert result is None
    assert len(warnings) == 1
    assert "key" in warnings[0].lower() or "api" in warnings[0].lower()
