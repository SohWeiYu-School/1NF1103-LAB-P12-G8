"""Tests for logic_manager indicator check functions and match_typologies."""

from app.logic_manager import match_typologies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decl(*sources):
    return {"sources": list(sources)}


def _src(source_type="employment", counterparty=None, amount_sgd=0, occupation_mismatch=False):
    return {
        "source_type": source_type,
        "description": "test",
        "amount_sgd": amount_sgd,
        "start_year": 2000,
        "end_year": None,
        "jurisdiction": "SG",
        "counterparty": counterparty,
        "evidence_sentence": "test.",
        "occupation_mismatch": occupation_mismatch,
    }


def _clean_case():
    return {}


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
# gift_or_inheritance_unnamed
# ---------------------------------------------------------------------------

def test_gift_unnamed_fires_on_unnamed_inheritance():
    decl = _decl(_src(source_type="inheritance", counterparty=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "gift_or_inheritance_unnamed", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is True


def test_gift_unnamed_fires_on_unnamed_gift():
    decl = _decl(_src(source_type="gift", counterparty=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "gift_or_inheritance_unnamed", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is True


def test_gift_unnamed_does_not_fire_when_counterparty_named():
    decl = _decl(_src(source_type="inheritance", counterparty="Estate of John Doe"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "gift_or_inheritance_unnamed", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is False


def test_gift_unnamed_does_not_fire_on_employment():
    # Employment with no counterparty should not trigger this indicator
    decl = _decl(_src(source_type="employment", counterparty=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "gift_or_inheritance_unnamed", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# counterparty_unnamed
# ---------------------------------------------------------------------------

def test_counterparty_unnamed_fires_at_limit():
    decl = _decl(_src(counterparty=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "counterparty_unnamed", "weight": "core"}])],
        decl, _clean_case(), _policy(limit=1),
    )
    assert results[0]["breached"] is True


def test_counterparty_unnamed_does_not_fire_below_limit():
    decl = _decl(_src(counterparty="Maersk"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "counterparty_unnamed", "weight": "core"}])],
        decl, _clean_case(), _policy(limit=1),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# source_contradicts_occupation
# ---------------------------------------------------------------------------

def test_source_contradicts_occupation_fires_when_flagged():
    decl = _decl(_src(occupation_mismatch=True))
    results = match_typologies(
        [_typology("t1", [{"indicator": "source_contradicts_occupation", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is True


def test_source_contradicts_occupation_does_not_fire_when_not_flagged():
    decl = _decl(_src(occupation_mismatch=False))
    results = match_typologies(
        [_typology("t1", [{"indicator": "source_contradicts_occupation", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# unvalued_source_present
# ---------------------------------------------------------------------------

def test_unvalued_source_fires_when_amount_null():
    decl = _decl(_src(amount_sgd=None))
    results = match_typologies(
        [_typology("t1", [{"indicator": "unvalued_source_present", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is True


def test_unvalued_source_does_not_fire_when_amounts_present():
    decl = _decl(_src(amount_sgd=100000))
    results = match_typologies(
        [_typology("t1", [{"indicator": "unvalued_source_present", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# related_party_transaction
# ---------------------------------------------------------------------------

def test_related_party_fires_on_friend_keyword():
    decl = _decl(_src(counterparty="a friend"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "related_party_transaction", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is True


def test_related_party_does_not_fire_on_named_company():
    decl = _decl(_src(counterparty="Maersk Line"))
    results = match_typologies(
        [_typology("t1", [{"indicator": "related_party_transaction", "weight": "core"}])],
        decl, _clean_case(), _policy(),
    )
    assert results[0]["breached"] is False


# ---------------------------------------------------------------------------
# match_typologies — structural tests
# ---------------------------------------------------------------------------

def test_empty_typologies_returns_empty():
    results = match_typologies([], _decl(), _clean_case(), _policy())
    assert results == []


def test_all_core_fire_gives_breach():
    decl = _decl(_src(source_type="gift", counterparty=None, occupation_mismatch=True))
    typ = _typology("t1", [
        {"indicator": "gift_or_inheritance_unnamed", "weight": "core"},
        {"indicator": "source_contradicts_occupation", "weight": "core"},
    ])
    results = match_typologies([typ], decl, _clean_case(), _policy())
    assert results[0]["breached"] is True
    assert set(results[0]["core_fired"]) == {"gift_or_inheritance_unnamed", "source_contradicts_occupation"}


def test_partial_core_fire_no_breach():
    # gift fires, source_contradicts_occupation doesn't
    decl = _decl(_src(source_type="gift", counterparty=None, occupation_mismatch=False))
    typ = _typology("t1", [
        {"indicator": "gift_or_inheritance_unnamed", "weight": "core"},
        {"indicator": "source_contradicts_occupation", "weight": "core"},
    ])
    results = match_typologies([typ], decl, _clean_case(), _policy())
    assert results[0]["breached"] is False


def test_supporting_indicators_do_not_affect_breach():
    # Core fires, supporting doesn't — still BREACH
    decl = _decl(_src(source_type="gift", counterparty=None))
    typ = _typology("t1", [
        {"indicator": "gift_or_inheritance_unnamed", "weight": "core"},
        {"indicator": "counterparty_unnamed", "weight": "supporting"},
    ])
    results = match_typologies([typ], decl, _clean_case(), _policy(limit=99))
    assert results[0]["breached"] is True
    assert results[0]["supporting_fired"] == []


def test_multiple_typologies_mixed_results():
    decl = _decl(_src(source_type="gift", counterparty=None, amount_sgd=None))
    typ1 = _typology("t1", [{"indicator": "gift_or_inheritance_unnamed", "weight": "core"}])
    typ2 = _typology("t2", [{"indicator": "counterparty_unnamed", "weight": "core"}])
    results = match_typologies([typ1, typ2], decl, _clean_case(), _policy())
    # gift with no counterparty → both fire
    assert results[0]["breached"] is True
    assert results[1]["breached"] is True
