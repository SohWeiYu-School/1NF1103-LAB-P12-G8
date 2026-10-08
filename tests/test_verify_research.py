"""Tests for logic_manager.verify_research."""

from app.logic_manager import verify_research


def _sample_policy(trusted=None):
    return {
        "trusted_sources": trusted or ["fatf-gafi.org", "mas.gov.sg"],
    }


def _sample_report(report_id, url, org="FATF", year=2020):
    return {
        "report_id": report_id,
        "url": url,
        "organisation": org,
        "year": year,
        "title": "Test Report",
        "summary": "Summary",
        "excerpts": ["Excerpt one.", "Excerpt two."],
    }


def _sample_research(reports, api_sources=None):
    if api_sources is None:
        api_sources = [{"url": r["url"]} for r in reports]
    return {
        "reports": reports,
        "api_sources": api_sources,
        "retrieved_at": "2025-01-01T00:00:00Z",
        "model": "test-model",
    }


# ---------------------------------------------------------------------------
# Test 1: URL not in api_sources → removed + warning
# ---------------------------------------------------------------------------

def test_url_not_in_api_sources_removed():
    report = _sample_report("R1", "https://fatf-gafi.org/report.pdf")
    research = _sample_research([report], api_sources=[])  # empty api_sources
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    assert clean["reports"] == []
    assert any("R1" in w and "web search" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 2: Domain not trusted → removed + warning
# ---------------------------------------------------------------------------

def test_untrusted_domain_removed():
    report = _sample_report("R1", "https://untrusted-site.com/report.pdf")
    research = _sample_research([report])
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    assert clean["reports"] == []
    assert any("R1" in w and "trusted" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 3: Duplicate URL → second removed
# ---------------------------------------------------------------------------

def test_duplicate_url_second_removed():
    url = "https://fatf-gafi.org/report.pdf"
    r1 = _sample_report("R1", url)
    r2 = _sample_report("R2", url)
    research = _sample_research([r1, r2], api_sources=[{"url": url}])
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    assert len(clean["reports"]) == 1
    assert any("R2" in w and "duplicate" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test 4: IDs renumbered after filtering (R1, R2, R3 with R2 removed → R1, R2)
# ---------------------------------------------------------------------------

def test_ids_renumbered_after_filtering():
    r1 = _sample_report("R1", "https://fatf-gafi.org/r1.pdf")
    r2 = _sample_report("R2", "https://untrusted.com/r2.pdf")  # will be removed
    r3 = _sample_report("R3", "https://mas.gov.sg/r3.pdf")
    research = _sample_research(
        [r1, r2, r3],
        api_sources=[
            {"url": "https://fatf-gafi.org/r1.pdf"},
            {"url": "https://untrusted.com/r2.pdf"},
            {"url": "https://mas.gov.sg/r3.pdf"},
        ],
    )
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    ids = [r["report_id"] for r in clean["reports"]]
    assert ids == ["R1", "R2"]


# ---------------------------------------------------------------------------
# Test 5: All valid → nothing removed, no warnings
# ---------------------------------------------------------------------------

def test_all_valid_nothing_removed():
    r1 = _sample_report("R1", "https://fatf-gafi.org/r1.pdf")
    r2 = _sample_report("R2", "https://mas.gov.sg/r2.pdf")
    research = _sample_research([r1, r2])
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    assert len(clean["reports"]) == 2
    assert warnings == []


# ---------------------------------------------------------------------------
# Test 6: Empty reports → empty result, no warnings
# ---------------------------------------------------------------------------

def test_empty_reports():
    research = _sample_research([], api_sources=[])
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    assert clean["reports"] == []
    assert warnings == []


# ---------------------------------------------------------------------------
# Test 7: Subdomain www.fatf-gafi.org matches trusted fatf-gafi.org
# ---------------------------------------------------------------------------

def test_subdomain_matches_trusted():
    report = _sample_report("R1", "https://www.fatf-gafi.org/report.pdf")
    research = _sample_research([report])
    policy = _sample_policy()

    clean, warnings = verify_research(research, policy)

    assert len(clean["reports"]) == 1
    assert warnings == []
