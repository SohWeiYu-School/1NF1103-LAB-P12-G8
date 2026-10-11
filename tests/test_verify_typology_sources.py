"""Tests for logic_manager.verify_typology_sources.

verify_typology_sources returns (cleaned_typologies, warnings):
  - cleaned_typologies: typology dicts with bad source_ids/quotes removed;
    typologies with zero verified quotes are dropped entirely.
    Each kept typology carries extra metadata: corroborated (bool),
    outdated_source_ids (list[str]).
  - warnings: ONLY for removals / invalid data:
      - source_id not found in research
      - quote report_id not found
      - quote not found verbatim in excerpts
      - typology dropped (no verified quotes remain)
      - source has no valid year
    Corroboration status and outdated age are NOT warnings — they are
    stored in the returned typology dict for display.
"""

from app.logic_manager import verify_typology_sources


def _sample_policy(max_age=10):
    return {"source_max_age_years": max_age}


def _sample_report(report_id, org="FATF", year=2020, excerpts=None):
    return {
        "report_id": report_id,
        "organisation": org,
        "year": year,
        "excerpts": excerpts or ["Known excerpt text.", "Another excerpt."],
    }


def _sample_clean_research(reports, retrieved_at="2026-01-01T00:00:00Z"):
    return {
        "reports": reports,
        "api_sources": [],
        "retrieved_at": retrieved_at,
        "model": "test-model",
    }


def _sample_typology(typology_id, source_ids, source_quotes):
    return {
        "typology_id": typology_id,
        "name": "Test Typology",
        "description": "Sentence one. Sentence two.",
        "source_ids": source_ids,
        "source_quotes": source_quotes,
        "indicators": [{"indicator": "offshore_entity_present", "weight": "core"}],
        "typical_documents": ["Bank statements"],
    }


# ---------------------------------------------------------------------------
# Test 1: Valid source_id → retained in cleaned typology
# ---------------------------------------------------------------------------

def test_valid_source_id_retained():
    report = _sample_report("R1")
    clean = _sample_clean_research([report])
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "Known excerpt text."}])

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    assert "R1" in cleaned[0]["source_ids"]
    assert not any("not found in research" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 2: Invalid source_id → removed from cleaned typology + warning
# ---------------------------------------------------------------------------

def test_invalid_source_id_removed():
    report = _sample_report("R1")
    clean = _sample_clean_research([report])
    # R99 doesn't exist; R1 does and has a valid quote
    typ = _sample_typology(
        "T1",
        ["R1", "R99"],
        [{"report_id": "R1", "quote": "Known excerpt text."}],
    )

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    assert "R99" not in cleaned[0]["source_ids"]
    assert "R1" in cleaned[0]["source_ids"]
    assert any("R99" in w and "not found in research" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 3: Valid quote (substring of excerpt) → retained in cleaned typology
# ---------------------------------------------------------------------------

def test_valid_quote_retained():
    report = _sample_report("R1", excerpts=["The full excerpt with important text."])
    clean = _sample_clean_research([report])
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "important text"}])

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    assert cleaned[0]["source_quotes"][0]["quote"] == "important text"
    assert not any("not found verbatim" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 4: Invalid quote → removed from cleaned output + warning
# ---------------------------------------------------------------------------

def test_invalid_quote_removed_from_output():
    report = _sample_report("R1", excerpts=["Valid excerpt text."])
    clean = _sample_clean_research([report])
    # Two quotes: one valid, one invalid
    typ = _sample_typology("T1", ["R1"], [
        {"report_id": "R1", "quote": "Valid excerpt text."},
        {"report_id": "R1", "quote": "This quote does not exist in excerpts"},
    ])

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    quotes = [q["quote"] for q in cleaned[0]["source_quotes"]]
    assert "Valid excerpt text." in quotes
    assert "This quote does not exist in excerpts" not in quotes
    assert any("not found verbatim" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 5: Typology with no valid quotes → dropped entirely + warning
# ---------------------------------------------------------------------------

def test_no_valid_quotes_drops_typology():
    report = _sample_report("R1", excerpts=["Real excerpt."])
    clean = _sample_clean_research([report])
    typ = _sample_typology(
        "T1", ["R1"], [{"report_id": "R1", "quote": "Quote not present in excerpts"}]
    )

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert cleaned == []
    assert any("dropped" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 6: Corroborated — 2 reports from different orgs → corroborated=True
# ---------------------------------------------------------------------------

def test_corroborated_different_orgs():
    r1 = _sample_report("R1", org="FATF", excerpts=["shared quote"])
    r2 = _sample_report("R2", org="MAS", excerpts=["shared quote"])
    clean = _sample_clean_research([r1, r2])
    typ = _sample_typology(
        "T1",
        ["R1", "R2"],
        [
            {"report_id": "R1", "quote": "shared quote"},
            {"report_id": "R2", "quote": "shared quote"},
        ],
    )

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    assert cleaned[0]["corroborated"] is True
    assert not any("corroborat" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 7: Not corroborated — 2 reports from same org → corroborated=False
# ---------------------------------------------------------------------------

def test_not_corroborated_same_org():
    r1 = _sample_report("R1", org="FATF", excerpts=["shared quote"])
    r2 = _sample_report("R2", org="FATF", excerpts=["shared quote"])
    clean = _sample_clean_research([r1, r2])
    typ = _sample_typology(
        "T1",
        ["R1", "R2"],
        [
            {"report_id": "R1", "quote": "shared quote"},
            {"report_id": "R2", "quote": "shared quote"},
        ],
    )

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    assert cleaned[0]["corroborated"] is False
    assert not any("corroborat" in w for w in warnings)  # not a warning


# ---------------------------------------------------------------------------
# Test 8: Org splitting — "FATF and Egmont Group" vs "FATF" → same;
#          "FATF and Egmont Group" vs "MAS" → corroborated
# ---------------------------------------------------------------------------

def test_org_splitting():
    r1 = _sample_report("R1", org="FATF and Egmont Group", excerpts=["quote"])
    r2_same = _sample_report("R2", org="FATF", excerpts=["quote"])
    r3_diff = _sample_report("R3", org="MAS", excerpts=["quote"])

    # R1 + R2 share "fatf" → not corroborated
    clean_same = _sample_clean_research([r1, r2_same])
    typ_same = _sample_typology(
        "T1",
        ["R1", "R2"],
        [{"report_id": "R1", "quote": "quote"}, {"report_id": "R2", "quote": "quote"}],
    )
    cleaned_same, _ = verify_typology_sources([typ_same], clean_same, _sample_policy())
    assert cleaned_same[0]["corroborated"] is False

    # R1 + R3: "fatf"/"egmont group" vs "mas" → disjoint → corroborated
    clean_diff = _sample_clean_research([r1, r3_diff])
    typ_diff = _sample_typology(
        "T2",
        ["R1", "R3"],
        [{"report_id": "R1", "quote": "quote"}, {"report_id": "R3", "quote": "quote"}],
    )
    cleaned_diff, _ = verify_typology_sources([typ_diff], clean_diff, _sample_policy())
    assert cleaned_diff[0]["corroborated"] is True


# ---------------------------------------------------------------------------
# Test 9: Outdated — year 2006, retrieved_at 2026 → in outdated_source_ids,
#          NOT a warning
# ---------------------------------------------------------------------------

def test_outdated_source_tracked_not_warned():
    report = _sample_report("R1", year=2006, excerpts=["quote"])
    clean = _sample_clean_research([report], retrieved_at="2026-01-01T00:00:00Z")
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote"}])

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy(max_age=10))

    assert len(cleaned) == 1
    assert "R1" in cleaned[0]["outdated_source_ids"]
    assert not any("years old" in w for w in warnings)  # outdated is not a warning


# ---------------------------------------------------------------------------
# Test 10: Not outdated — year 2020, retrieved_at 2026 → outdated_source_ids=[]
# ---------------------------------------------------------------------------

def test_not_outdated():
    report = _sample_report("R1", year=2020, excerpts=["quote"])
    clean = _sample_clean_research([report], retrieved_at="2026-01-01T00:00:00Z")
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote"}])

    cleaned, _ = verify_typology_sources([typ], clean, _sample_policy(max_age=10))

    assert len(cleaned) == 1
    assert cleaned[0]["outdated_source_ids"] == []


# ---------------------------------------------------------------------------
# Test 11: Normalisation — excerpt has curly quotes, quote uses straight → match
# ---------------------------------------------------------------------------

def test_normalisation_curly_quotes_match():
    report = _sample_report(
        "R1", excerpts=["\u201cImportant finding\u201d noted here."]
    )
    clean = _sample_clean_research([report])
    typ = _sample_typology(
        "T1", ["R1"], [{"report_id": "R1", "quote": '"Important finding" noted here.'}]
    )

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert len(cleaned) == 1
    assert not any("not found verbatim" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 12: Unknown age — year=0 → outdated_source_ids=[], "no valid year" warning
# ---------------------------------------------------------------------------

def test_unknown_age_not_counted_as_outdated():
    report = _sample_report("R1", year=0, excerpts=["quote"])
    clean = _sample_clean_research([report], retrieved_at="2026-01-01T00:00:00Z")
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote"}])

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy(max_age=10))

    assert len(cleaned) == 1
    assert cleaned[0]["outdated_source_ids"] == []
    assert any("no valid year" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 13: Missing year (None) → also treated as unknown age
# ---------------------------------------------------------------------------

def test_missing_year_treated_as_unknown():
    report = {"report_id": "R1", "organisation": "FATF", "excerpts": ["quote"]}  # no "year"
    clean = _sample_clean_research([report], retrieved_at="2026-01-01T00:00:00Z")
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote"}])

    cleaned, warnings = verify_typology_sources([typ], clean, _sample_policy(max_age=10))

    assert len(cleaned) == 1
    assert cleaned[0]["outdated_source_ids"] == []
    assert any("no valid year" in w for w in warnings)
