"""Tests for the five typology indicator check functions in app/logic_manager.py."""
import pytest
from app.logic_manager import (
    check_offshore_entity_present,
    check_counterparty_unnamed,
    check_rapid_accumulation,
    check_unvalued_source_present,
    check_related_party_transaction,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

BENCHMARK = {
    "expected_jurisdictions": ["SG", "MY", "ID"],
    "max_accumulation_per_year_sgd": 180_000,
}

NULL_SOURCE = {
    "source_type": None,
    "description": None,
    "amount_sgd": None,
    "start_year": None,
    "end_year": None,
    "jurisdiction": None,
    "counterparty": None,
}


# ---------------------------------------------------------------------------
# check_offshore_entity_present
# ---------------------------------------------------------------------------

def test_offshore_entity_present_match():
    sources = [
        {"source_type": "investment", "jurisdiction": "CY"},  # Cyprus — not expected
    ]
    matched, evidence = check_offshore_entity_present(sources, BENCHMARK)
    assert matched is True
    assert "CY" in evidence
    assert evidence != ""


def test_offshore_entity_present_no_match():
    sources = [
        {"source_type": "employment", "jurisdiction": "SG"},
        {"source_type": "property", "jurisdiction": "MY"},
    ]
    matched, evidence = check_offshore_entity_present(sources, BENCHMARK)
    assert matched is False
    assert evidence == ""


def test_offshore_entity_present_null_source_no_raise():
    matched, evidence = check_offshore_entity_present([NULL_SOURCE], BENCHMARK)
    assert matched is False


def test_offshore_entity_present_empty_benchmark():
    sources = [{"source_type": "investment", "jurisdiction": "CY"}]
    matched, evidence = check_offshore_entity_present(sources, {})
    assert matched is True


# ---------------------------------------------------------------------------
# check_counterparty_unnamed
# ---------------------------------------------------------------------------

def test_counterparty_unnamed_match_none():
    sources = [{"source_type": "investment", "counterparty": None}]
    matched, evidence = check_counterparty_unnamed(sources, BENCHMARK)
    assert matched is True
    assert evidence != ""


def test_counterparty_unnamed_match_vague_string():
    # "a friend" is explicitly listed as vague
    sources = [{"source_type": "property", "counterparty": "a friend"}]
    matched, evidence = check_counterparty_unnamed(sources, BENCHMARK)
    assert matched is True


def test_counterparty_unnamed_match_unknown():
    sources = [{"source_type": "gift", "counterparty": "Unknown"}]
    matched, evidence = check_counterparty_unnamed(sources, BENCHMARK)
    assert matched is True


def test_counterparty_unnamed_no_match():
    sources = [
        {"source_type": "employment", "counterparty": "Maersk"},
        {"source_type": "investment", "counterparty": "DBS Bank"},
    ]
    matched, evidence = check_counterparty_unnamed(sources, BENCHMARK)
    assert matched is False
    assert evidence == ""


def test_counterparty_unnamed_null_source_no_raise():
    matched, evidence = check_counterparty_unnamed([NULL_SOURCE], BENCHMARK)
    assert matched is True  # None counterparty is unnamed


# ---------------------------------------------------------------------------
# check_rapid_accumulation
# ---------------------------------------------------------------------------

def test_rapid_accumulation_match():
    # 1,800,000 over 2 years = 900,000/yr > 180,000 limit
    sources = [
        {"source_type": "investment", "amount_sgd": 1_800_000, "start_year": 2019, "end_year": 2021},
    ]
    matched, evidence = check_rapid_accumulation(sources, BENCHMARK)
    assert matched is True
    assert "900,000" in evidence or "900000" in evidence.replace(",", "")
    assert evidence != ""


def test_rapid_accumulation_no_match():
    # 360,000 over 4 years = 90,000/yr < 180,000 limit
    sources = [
        {"source_type": "employment", "amount_sgd": 360_000, "start_year": 2018, "end_year": 2022},
    ]
    matched, evidence = check_rapid_accumulation(sources, BENCHMARK)
    assert matched is False
    assert evidence == ""


def test_rapid_accumulation_no_end_year_uses_start():
    # 1,800,000 with start == end → denominator is max(1,0) = 1 → rate = 1,800,000 > 180,000
    sources = [
        {"source_type": "investment", "amount_sgd": 1_800_000, "start_year": 2020, "end_year": None},
    ]
    matched, evidence = check_rapid_accumulation(sources, BENCHMARK)
    assert matched is True


def test_rapid_accumulation_null_source_no_raise():
    matched, evidence = check_rapid_accumulation([NULL_SOURCE], BENCHMARK)
    assert matched is False


def test_rapid_accumulation_no_limit_in_benchmark():
    sources = [{"source_type": "investment", "amount_sgd": 999_999_999, "start_year": 2020, "end_year": 2021}]
    matched, evidence = check_rapid_accumulation(sources, {})
    assert matched is False


# ---------------------------------------------------------------------------
# check_unvalued_source_present
# ---------------------------------------------------------------------------

def test_unvalued_source_present_match():
    sources = [{"source_type": "employment", "amount_sgd": None}]
    matched, evidence = check_unvalued_source_present(sources, BENCHMARK)
    assert matched is True
    assert "employment" in evidence


def test_unvalued_source_present_no_match():
    sources = [
        {"source_type": "employment", "amount_sgd": 1_200_000},
        {"source_type": "property", "amount_sgd": 0},  # zero is a value
    ]
    matched, evidence = check_unvalued_source_present(sources, BENCHMARK)
    assert matched is False
    assert evidence == ""


def test_unvalued_source_present_null_source_no_raise():
    matched, evidence = check_unvalued_source_present([NULL_SOURCE], BENCHMARK)
    assert matched is True  # amount_sgd is None → unvalued


# ---------------------------------------------------------------------------
# check_related_party_transaction
# ---------------------------------------------------------------------------

def test_related_party_transaction_match_counterparty():
    sources = [{"source_type": "gift", "counterparty": "my father", "description": "cash gift"}]
    matched, evidence = check_related_party_transaction(sources, BENCHMARK)
    assert matched is True
    assert "father" in evidence


def test_related_party_transaction_match_description():
    sources = [{"source_type": "loan", "counterparty": "John Tan", "description": "Loan from spouse"}]
    matched, evidence = check_related_party_transaction(sources, BENCHMARK)
    assert matched is True
    assert "spouse" in evidence


def test_related_party_transaction_no_match():
    sources = [
        {"source_type": "employment", "counterparty": "Maersk", "description": "Salary from employer"},
        {"source_type": "investment", "counterparty": "DBS Bank", "description": "Fixed deposit"},
    ]
    matched, evidence = check_related_party_transaction(sources, BENCHMARK)
    assert matched is False
    assert evidence == ""


def test_related_party_transaction_null_source_no_raise():
    matched, evidence = check_related_party_transaction([NULL_SOURCE], BENCHMARK)
    assert matched is False
