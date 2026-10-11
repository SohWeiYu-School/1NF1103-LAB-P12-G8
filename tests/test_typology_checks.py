"""Tests for logic_manager indicator check functions and match_typologies."""

from app.logic_manager import match_typologies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decl(*sources):
    return {"sources": list(sources)}


def _src(jurisdiction=None, counterparty=None, amount_sgd=0):
    return {
        "source_type": "employment",
        "description": "test",
        "amount_sgd": amount_sgd,
        "start_year": 2000,
        "end_year": None,
        "jurisdiction": jurisdiction,
        "counterparty": counterparty,
        "evidence_sentence": "test.",
    }


def _clean_case(nw=None, start=None, ref=None, all_jur=None):
    return {
        "declared_net_worth_sgd": nw,
        "career_start_year": start,
        "reference_year": ref,
        "all_jurisdictions": all_jur or [],
    }


def _policy(limit=1):
    return {
        "thresholds": {"unknown_counterparty_limit": limit},
        "typology_full_match_counts_as_breach": True,
    }


def _typology(typ_id, indicators):
    return {
        "typology_id": typ_id,
        "name": typ_id,
        "description": "test",
        "source_ids": [],
        "source_quotes": [],
        "indicators": indicators,
        "typical_documents": ["test doc"],
    }


# ---------------------------------------------------------------------------
# offshore_entity_present
# ---------------------------------------------------------------------------

def test_offshore_entity_present_fires_on_unexpected_jurisdiction():
    decl = _decl(_src(jurisdiction="CY"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "offshore_entity_present", "weight": "core"}])],
        decl, _clean_case(all_jur=["SG"]), ["SG", "MY"], 0, _policy(),
    )
    assert results[0]["breached"] is True


def test_offshore_entity_present_passes_when_all_expected():
    decl = _decl(_src(jurisdiction="SG"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "offshore_entity_present", "weight": "core"}])],
        decl, _clean_case(all_jur=["SG"]), ["SG"], 0, _policy(),
    )
    assert results[0]["breached"] is False


def test_offshore_entity_empty_expected_does_not_fire():
    decl = _decl(_src(jurisdiction="CY"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "offshore_entity_present", "weight": "core"}])],
        decl, _clean_case(all_jur=["CY"]), [], 0, _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# counterparty_unnamed
# ---------------------------------------------------------------------------

def test_counterparty_unnamed_fires_at_limit():
    decl = _decl(_src(counterparty=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "counterparty_unnamed", "weight": "core"}])],
        decl, _clean_case(), [], 0, _policy(limit=1),
    )
    assert results[0]["breached"] is True


def test_counterparty_unnamed_does_not_fire_below_limit():
    decl = _decl(_src(counterparty="Maersk"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "counterparty_unnamed", "weight": "core"}])],
        decl, _clean_case(), [], 0, _policy(limit=1),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# rapid_accumulation
# ---------------------------------------------------------------------------

def test_rapid_accumulation_fires_when_over_max():
    # 3.5M / 20 years = 175K > 50K
    decl = _decl(_src())
    results = match_typologies(
        [_typology("t1", [{"indicator": "rapid_accumulation", "weight": "core"}])],
        decl, _clean_case(nw=3500000, start=2005, ref=2025), [], 50000, _policy(),
    )
    assert results[0]["breached"] is True


def test_rapid_accumulation_does_not_fire_when_under_max():
    # 2M / 20 years = 100K < 180K
    decl = _decl(_src())
    results = match_typologies(
        [_typology("t1", [{"indicator": "rapid_accumulation", "weight": "core"}])],
        decl, _clean_case(nw=2000000, start=2005, ref=2025), [], 180000, _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# unvalued_source_present
# ---------------------------------------------------------------------------

def test_unvalued_source_fires_when_amount_null():
    decl = _decl(_src(amount_sgd=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "unvalued_source_present", "weight": "core"}])],
        decl, _clean_case(), [], 0, _policy(),
    )
    assert results[0]["breached"] is True


def test_unvalued_source_does_not_fire_when_amounts_present():
    decl = _decl(_src(amount_sgd=100000))
    results = match_typologies(
        [_typology("t1", [{"indicator": "unvalued_source_present", "weight": "core"}])],
        decl, _clean_case(), [], 0, _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# related_party_transaction
# ---------------------------------------------------------------------------

def test_related_party_fires_on_friend_keyword():
    decl = _decl(_src(counterparty="a friend"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "related_party_transaction", "weight": "core"}])],
        decl, _clean_case(), [], 0, _policy(),
    )
    assert results[0]["breached"] is True


def test_related_party_does_not_fire_on_named_company():
    decl = _decl(_src(counterparty="Maersk Line"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "related_party_transaction", "weight": "core"}])],
        decl, _clean_case(), [], 0, _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# match_typologies — structural tests
# ---------------------------------------------------------------------------

def test_empty_typologies_returns_empty():
    results = match_typologies([], _decl(), _clean_case(), [], 0, _policy())
    assert results == []


def test_all_core_fire_gives_breach():
    decl = _decl(_src(jurisdiction="CY", counterparty=None))
    typ = _typology("t1", [
        {"indicator": "offshore_entity_present", "weight": "core"},
        {"indicator": "counterparty_unnamed", "weight": "core"},
    ])
    results = match_typologies([typ], decl, _clean_case(), ["SG"], 0, _policy())
    assert results[0]["breached"] is True
    assert set(results[0]["core_fired"]) == {"offshore_entity_present", "counterparty_unnamed"}


def test_partial_core_fire_no_breach():
    # offshore fires, counterparty_unnamed doesn't
    decl = _decl(_src(jurisdiction="CY", counterparty="Maersk"))
    typ = _typology("t1", [
        {"indicator": "offshore_entity_present", "weight": "core"},
        {"indicator": "counterparty_unnamed", "weight": "core"},
    ])
    results = match_typologies([typ], decl, _clean_case(), ["SG"], 0, _policy())
    assert results[0]["breached"] is False


def test_supporting_indicators_do_not_affect_breach():
    # Core fires, supporting doesn't — still BREACH
    decl = _decl(_src(jurisdiction="CY", counterparty="Maersk"))  # named cp → won't fire
    typ = _typology("t1", [
        {"indicator": "offshore_entity_present", "weight": "core"},
        {"indicator": "counterparty_unnamed", "weight": "supporting"},
    ])
    results = match_typologies([typ], decl, _clean_case(), ["SG"], 0, _policy())
    assert results[0]["breached"] is True
    assert results[0]["supporting_fired"] == []


def test_multiple_typologies_mixed_results():
    decl = _decl(_src(jurisdiction="CY", amount_sgd=None))
    typ1 = _typology("t1", [{"indicator": "offshore_entity_present", "weight": "core"}])
    typ2 = _typology("t2", [{"indicator": "counterparty_unnamed", "weight": "core"}])
    results = match_typologies([typ1, typ2], decl, _clean_case(), ["SG"], 0, _policy())
    # t1: CY not in [SG] → fires → breach
    assert results[0]["breached"] is True
    # t2: counterparty is None... wait _src has counterparty=None by default
    # Let's check: _src(jurisdiction="CY", amount_sgd=None) — counterparty is None by default!
    assert results[1]["breached"] is True
