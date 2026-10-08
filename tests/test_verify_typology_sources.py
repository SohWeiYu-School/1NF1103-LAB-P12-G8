"""Tests for logic_manager.verify_typology_sources."""

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
        "source_ids": source_ids,
        "source_quotes": source_quotes,
    }


# ---------------------------------------------------------------------------
# Test 1: Valid source_id → in valid_source_ids
# ---------------------------------------------------------------------------

def test_valid_source_id():
    report = _sample_report("R1")
    clean = _sample_clean_research([report])
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "Known excerpt text."}])

    results, _ = verify_typology_sources([typ], clean, _sample_policy())

    assert "R1" in results[0]["valid_source_ids"]
    assert results[0]["invalid_source_ids"] == []


# ---------------------------------------------------------------------------
# Test 2: Invalid source_id → in invalid_source_ids + warning
# ---------------------------------------------------------------------------

def test_invalid_source_id():
    clean = _sample_clean_research([])
    typ = _sample_typology("T1", ["R99"], [])

    results, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert "R99" in results[0]["invalid_source_ids"]
    assert any("R99" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 3: Valid quote (substring of excerpt) → verified
# ---------------------------------------------------------------------------

def test_valid_quote_verified():
    report = _sample_report("R1", excerpts=["The full excerpt with important text."])
    clean = _sample_clean_research([report])
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "important text"}])

    results, _ = verify_typology_sources([typ], clean, _sample_policy())

    assert results[0]["valid_quotes"][0]["verified"] is True
    assert results[0]["invalid_quotes"] == []


# ---------------------------------------------------------------------------
# Test 4: Invalid quote → flagged with reason
# ---------------------------------------------------------------------------

def test_invalid_quote_flagged():
    report = _sample_report("R1", excerpts=["Some excerpt."])
    clean = _sample_clean_research([report])
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote not present here"}])

    results, warnings = verify_typology_sources([typ], clean, _sample_policy())

    assert results[0]["valid_quotes"] == []
    assert results[0]["invalid_quotes"][0]["reason"] == "quote not found in excerpts"
    assert any("not found verbatim" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 5: Corroborated — 2 reports from different orgs → True
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

    results, _ = verify_typology_sources([typ], clean, _sample_policy())

    assert results[0]["corroborated"] is True


# ---------------------------------------------------------------------------
# Test 6: Not corroborated — 2 reports from same org → False
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

    results, _ = verify_typology_sources([typ], clean, _sample_policy())

    assert results[0]["corroborated"] is False


# ---------------------------------------------------------------------------
# Test 7: Org splitting — "FATF and Egmont Group" paired with "FATF" → same
#          org; paired with "MAS" → corroborated
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
    results_same, _ = verify_typology_sources([typ_same], clean_same, _sample_policy())
    assert results_same[0]["corroborated"] is False

    # R1 + R3: "fatf"/"egmont group" vs "mas" → disjoint → corroborated
    clean_diff = _sample_clean_research([r1, r3_diff])
    typ_diff = _sample_typology(
        "T2",
        ["R1", "R3"],
        [{"report_id": "R1", "quote": "quote"}, {"report_id": "R3", "quote": "quote"}],
    )
    results_diff, _ = verify_typology_sources([typ_diff], clean_diff, _sample_policy())
    assert results_diff[0]["corroborated"] is True


# ---------------------------------------------------------------------------
# Test 8: Outdated — year 2006, retrieved_at 2026 → age 20 > 10 → flagged
# ---------------------------------------------------------------------------

def test_outdated_source_flagged():
    report = _sample_report("R1", year=2006, excerpts=["quote"])
    clean = _sample_clean_research([report], retrieved_at="2026-01-01T00:00:00Z")
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote"}])

    results, warnings = verify_typology_sources([typ], clean, _sample_policy(max_age=10))

    assert "R1" in results[0]["outdated_source_ids"]
    assert any("20 years old" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 9: Not outdated — year 2020, retrieved_at 2026 → age 6 ≤ 10 → ok
# ---------------------------------------------------------------------------

def test_not_outdated():
    report = _sample_report("R1", year=2020, excerpts=["quote"])
    clean = _sample_clean_research([report], retrieved_at="2026-01-01T00:00:00Z")
    typ = _sample_typology("T1", ["R1"], [{"report_id": "R1", "quote": "quote"}])

    results, _ = verify_typology_sources([typ], clean, _sample_policy(max_age=10))

    assert results[0]["outdated_source_ids"] == []
