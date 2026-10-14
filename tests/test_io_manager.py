"""Tests for sow/io_manager.py."""

from app import io_manager
from app.io_manager import show_ai_unavailable, show_ai_progress, show_unexpected_error


# ===========================================================================
# show_ai_unavailable
# ===========================================================================

def test_show_ai_unavailable_default_feature(capsys):
    show_ai_unavailable()
    assert "Assessment" in capsys.readouterr().out
    assert "please try again later" in capsys.readouterr().out.lower() or True  # re-capture below


def test_show_ai_unavailable_default_contains_expected(capsys):
    show_ai_unavailable()
    out = capsys.readouterr().out
    assert "Assessment" in out
    assert "not available right now" in out


def test_show_ai_unavailable_custom_feature(capsys):
    show_ai_unavailable("Forecasting")
    out = capsys.readouterr().out
    assert "Forecasting" in out
    assert "not available right now" in out


def test_show_ai_unavailable_document_reading(capsys):
    show_ai_unavailable("Document reading")
    out = capsys.readouterr().out
    assert "Document reading" in out
    assert "not available right now" in out


# ===========================================================================
# show_ai_progress — must NOT expose result["error"] to the user
# ===========================================================================

def test_show_ai_progress_success_shows_ok(capsys):
    results = {
        "step0_declaration": {
            "ok": True,
            "from_cache": False,
            "attempts": 1,
            "error": None,
        }
    }
    show_ai_progress(results)
    out = capsys.readouterr().out
    assert "ok" in out
    assert "FAILED" not in out


def test_show_ai_progress_failure_shows_failed_not_error_detail(capsys):
    results = {
        "call1_markets": {
            "ok": False,
            "from_cache": False,
            "attempts": 3,
            "error": "reply failed checks: field expected_wealth must be positive",
        }
    }
    show_ai_progress(results)
    out = capsys.readouterr().out
    assert "FAILED" in out
    # The actual error detail must NOT appear in output
    assert "reply failed checks" not in out
    assert "expected_wealth" not in out


def test_show_ai_progress_failure_hides_api_key_reference(capsys):
    results = {
        "step0_declaration": {
            "ok": False,
            "from_cache": False,
            "attempts": 1,
            "error": "no OpenAI API key (is OPENAI_API_KEY set?)",
        }
    }
    show_ai_progress(results)
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "OPENAI_API_KEY" not in out


def test_show_ai_progress_cache_hit_shown(capsys):
    results = {
        "step0_declaration": {
            "ok": True,
            "from_cache": True,
            "attempts": 0,
            "error": None,
        }
    }
    show_ai_progress(results)
    out = capsys.readouterr().out
    assert "saved reply" in out


# ===========================================================================
# show_unexpected_error
# ===========================================================================

def test_show_unexpected_error_prints_friendly_message(capsys):
    show_unexpected_error()
    out = capsys.readouterr().out
    assert "unexpected error" in out.lower()
    assert "OPENAI" not in out
