"""Tests for match_typologies in app/logic_manager.py.

No API key required — all tests use hand-written fixture dicts.

Fixture design follows the worked example in docs/HANDOVER_sector_typologies.md:
  - shipping manager, career 2005, declared SGD 6M
  - benchmark: expected jurisdictions SG/MY/ID, max accumulation 180,000/yr
  - sources: employment (SG, Maersk), investment (CY, null), property (SG, "a friend")
"""
import pytest
from app.logic_manager import match_typologies

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

POLICY = {
    "thresholds": {
        "typology_match_min_ratio": 0.6,
        "typology_full_match_severity": 4,
        "typology_partial_match_severity": 2,
    }
}

# Benchmark from the handover worked example
BENCHMARK_BASE = {
    "expected_jurisdictions": ["SG", "MY", "ID"],
    "max_accumulation_per_year_sgd": 180_000,
}

# Sources from the handover worked example
SOURCES_SHIPPING = [
    {
        "source_type": "employment",
        "amount_sgd": None,
        "start_year": 2005,
        "end_year": None,
        "jurisdiction": "SG",
        "counterparty": "Maersk",
        "description": "Shipping employment",
    },
    {
        "source_type": "investment",
        "amount_sgd": 1_800_000,
        "start_year": 2019,
        "end_year": 2021,
        "jurisdiction": "CY",   # Cyprus — not expected
        "counterparty": None,   # unnamed
        "description": "Offshore investment",
    },
    {
        "source_type": "property",
        "amount_sgd": 900_000,
        "start_year": 2016,
        "end_year": 2016,
        "jurisdiction": "SG",
        "counterparty": "a friend",  # vague
        "description": "Property purchase",
    },
]

# Typology matching all three indicators (from handover)
TYPOLOGY_TRADE_LAUNDERING = {
    "typology_id": "trade_based_laundering_over_invoicing",
    "name": "Trade-based laundering through over-invoicing",
    "description": "Value is moved across borders by inflating invoice amounts on shipments. "
                   "The surplus accumulates in an entity outside the trade lane.",
    "indicators": [
        {"indicator": "offshore_entity_present", "weight": "core"},
        {"indicator": "counterparty_unnamed", "weight": "core"},
        {"indicator": "rapid_accumulation", "weight": "supporting"},
    ],
    "typical_documents": ["Bills of lading", "Commercial invoices", "Customs declarations"],
}


# ---------------------------------------------------------------------------
# 1. Full match (TYP-01)
# ---------------------------------------------------------------------------

def test_full_match_produces_typ01():
    """All core indicators match and ratio >= min_ratio → TYP-01, severity 4, documents attached."""
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [TYPOLOGY_TRADE_LAUNDERING]}
    findings = match_typologies(SOURCES_SHIPPING, benchmark, POLICY)

    assert len(findings) == 1
    f = findings[0]
    assert f["rule_id"] == "TYP-01"
    assert f["dimension"] == "typology"
    assert f["severity"] == 4
    assert f["documents"] == ["Bills of lading", "Commercial invoices", "Customs declarations"]
    assert "3 of 3" in f["detail"]
    assert len(f["evidence"]) == 3


def test_full_match_evidence_format():
    """Evidence strings are prefixed with their indicator name."""
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [TYPOLOGY_TRADE_LAUNDERING]}
    findings = match_typologies(SOURCES_SHIPPING, benchmark, POLICY)

    indicators_in_evidence = {e.split(":")[0] for e in findings[0]["evidence"]}
    assert "offshore_entity_present" in indicators_in_evidence
    assert "counterparty_unnamed" in indicators_in_evidence
    assert "rapid_accumulation" in indicators_in_evidence


# ---------------------------------------------------------------------------
# 2. Partial match (TYP-02)
# ---------------------------------------------------------------------------

def test_partial_match_one_core_missing():
    """Core indicator fails → partial match TYP-02, severity 2, no documents."""
    typology = {
        "typology_id": "layering_via_offshore",
        "name": "Layering via offshore entities",
        "description": "Funds are moved through offshore structures. Counterparties are concealed.",
        "indicators": [
            {"indicator": "offshore_entity_present", "weight": "core"},
            {"indicator": "rapid_accumulation", "weight": "core"},  # won't match below
        ],
        "typical_documents": ["Incorporation documents"],
    }
    # Sources: only offshore triggers; accumulation is well within limit
    sources = [
        {
            "source_type": "investment",
            "amount_sgd": 10_000,
            "start_year": 2015,
            "end_year": 2020,
            "jurisdiction": "CY",
            "counterparty": "DBS Bank",
            "description": "Small investment",
        }
    ]
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [typology]}
    findings = match_typologies(sources, benchmark, POLICY)

    assert len(findings) == 1
    f = findings[0]
    assert f["rule_id"] == "TYP-02"
    assert f["severity"] == 2
    assert f["documents"] == []
    assert "1 of 2" in f["detail"]


# ---------------------------------------------------------------------------
# 3. No match — no core indicator triggered
# ---------------------------------------------------------------------------

def test_no_match_produces_no_finding():
    """When no core indicator matches, no finding is produced."""
    typology = {
        "typology_id": "some_typology",
        "name": "Some typology",
        "description": "A pattern. Another sentence.",
        "indicators": [
            {"indicator": "offshore_entity_present", "weight": "core"},
            {"indicator": "rapid_accumulation", "weight": "supporting"},
        ],
        "typical_documents": ["Bank statements"],
    }
    # All sources in expected jurisdictions, all accumulation within limit
    sources = [
        {
            "source_type": "employment",
            "amount_sgd": 500_000,
            "start_year": 2010,
            "end_year": 2020,
            "jurisdiction": "SG",
            "counterparty": "GovTech",
            "description": "Civil service salary",
        }
    ]
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [typology]}
    findings = match_typologies(sources, benchmark, POLICY)

    assert findings == []


# ---------------------------------------------------------------------------
# 4. Unknown indicator name is skipped, not raised
# ---------------------------------------------------------------------------

def test_unknown_indicator_is_skipped():
    """An indicator name outside the vocabulary is silently skipped; known ones still run."""
    typology = {
        "typology_id": "mixed_typology",
        "name": "Mixed typology",
        "description": "Has an unknown indicator. And a known one.",
        "indicators": [
            {"indicator": "offshore_entity_present", "weight": "core"},  # known, will match
            {"indicator": "invented_check_xyz", "weight": "supporting"},  # unknown, skip
        ],
        "typical_documents": ["Documents"],
    }
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [typology]}
    # Only 1 of 2 indicators is runnable; offshore will match → ratio 1/2 = 0.5 < 0.6
    # core matched but ratio fails → TYP-02
    findings = match_typologies(SOURCES_SHIPPING, benchmark, POLICY)

    assert len(findings) == 1
    assert findings[0]["rule_id"] == "TYP-02"
    # Evidence should only contain the known indicator
    assert all("invented_check_xyz" not in e for e in findings[0]["evidence"])


# ---------------------------------------------------------------------------
# 5. Benchmark with no sector_typologies key → empty list
# ---------------------------------------------------------------------------

def test_no_sector_typologies_key_returns_empty():
    """benchmark without sector_typologies key returns []."""
    benchmark = {**BENCHMARK_BASE}  # no sector_typologies
    findings = match_typologies(SOURCES_SHIPPING, benchmark, POLICY)
    assert findings == []


def test_empty_sector_typologies_returns_empty():
    """benchmark with sector_typologies=[] returns []."""
    benchmark = {**BENCHMARK_BASE, "sector_typologies": []}
    findings = match_typologies(SOURCES_SHIPPING, benchmark, POLICY)
    assert findings == []


# ---------------------------------------------------------------------------
# 6. Changing typology_match_min_ratio changes the result
#    (demonstrates our code — not the AI — controls the outcome)
# ---------------------------------------------------------------------------

def _ratio_test_setup():
    """
    Typology with 1 core + 2 supporting.
    Sources trigger: offshore (core) + counterparty (supporting).
    rapid_accumulation (supporting) does NOT trigger.
    → 2 of 3 match, ratio = 0.667.
    """
    typology = {
        "typology_id": "ratio_test_typology",
        "name": "Ratio test typology",
        "description": "Used to verify ratio threshold logic. One sentence only.",
        "indicators": [
            {"indicator": "offshore_entity_present", "weight": "core"},
            {"indicator": "counterparty_unnamed", "weight": "supporting"},
            {"indicator": "rapid_accumulation", "weight": "supporting"},
        ],
        "typical_documents": ["Test documents"],
    }
    sources = [
        {
            "source_type": "investment",
            "amount_sgd": 1_000,         # tiny — well under 180,000/yr limit
            "start_year": 2019,
            "end_year": 2021,
            "jurisdiction": "CY",        # offshore → triggers offshore check
            "counterparty": None,        # unnamed → triggers counterparty check
            "description": "Small offshore deposit",
        }
    ]
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [typology]}
    return sources, benchmark


def test_min_ratio_low_gives_full_match():
    """With min_ratio=0.6, ratio 2/3 ≈ 0.667 ≥ 0.6 and core matched → TYP-01."""
    sources, benchmark = _ratio_test_setup()
    policy = {
        "thresholds": {
            "typology_match_min_ratio": 0.6,
            "typology_full_match_severity": 4,
            "typology_partial_match_severity": 2,
        }
    }
    findings = match_typologies(sources, benchmark, policy)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "TYP-01"


def test_min_ratio_high_gives_partial_match():
    """With min_ratio=0.7, ratio 2/3 ≈ 0.667 < 0.7 → TYP-02 (core still matched)."""
    sources, benchmark = _ratio_test_setup()
    policy = {
        "thresholds": {
            "typology_match_min_ratio": 0.7,
            "typology_full_match_severity": 4,
            "typology_partial_match_severity": 2,
        }
    }
    findings = match_typologies(sources, benchmark, policy)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "TYP-02"


# ---------------------------------------------------------------------------
# 7. Malformed typology — no core indicators → no finding, no crash
# ---------------------------------------------------------------------------

def test_typology_with_no_core_indicators_produces_no_finding():
    """A typology where all indicators are 'supporting' is malformed and skipped."""
    typology = {
        "typology_id": "malformed",
        "name": "Malformed typology",
        "description": "All supporting, no core. Two sentences.",
        "indicators": [
            {"indicator": "offshore_entity_present", "weight": "supporting"},
            {"indicator": "rapid_accumulation", "weight": "supporting"},
        ],
        "typical_documents": ["N/A"],
    }
    benchmark = {**BENCHMARK_BASE, "sector_typologies": [typology]}
    findings = match_typologies(SOURCES_SHIPPING, benchmark, POLICY)
    assert findings == []
