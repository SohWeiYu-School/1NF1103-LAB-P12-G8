from app.ai_manager import get_ai_data
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Shared — limits used across all features
# ---------------------------------------------------------------------------

LIMITS = {
    "total_wealth": 3,
    "liquidity": 2,
    "composition": 40,
    "velocity": 2,
    "counterparties": 1,
    "jurisdiction": 1
}

# ---------------------------------------------------------------------------
# Benchmark Section
# ---------------------------------------------------------------------------

def calculate_total_wealth(client_data, ai_data):

    declared = client_data["declared_net_worth"]
    expected = ai_data["expected_wealth"]

    result1 = declared / expected

    return result1


def calculate_liquidity(client_data, ai_data):

    declared = client_data["listed_equities"] + client_data["cash"]
    expected = ai_data["expected_liquidity"]

    result2 = declared / expected

    return result2


def calculate_composition(client_data, ai_data):

    # Property
    declared_property = client_data["property"]
    expected_property = ai_data["expected_property"]

    difference_property = abs(declared_property - expected_property) #abs - absolute value, so it removes the negative sign

    # Listed Equities
    declared_listed = client_data["listed_equities"]
    expected_listed = ai_data["expected_listed_equities"]

    difference_listed = abs(declared_listed - expected_listed)

    # Private Business
    declared_business = client_data["private_business"]
    expected_business = ai_data["expected_private_business"]

    difference_business = abs(declared_business - expected_business)

    # Cash
    declared_cash = client_data["cash"]
    expected_cash = ai_data["expected_cash"]

    difference_cash = abs(declared_cash - expected_cash)

    print("\n--- Asset Composition Check ---")
    print("Category           Declared    Expected    Difference")
    print("Property              ", declared_property, "%       ", expected_property, "%        ", difference_property, "%")
    print("Listed Equities       ", declared_listed, "%       ", expected_listed, "%        ", difference_listed, "%")
    print("Private Business      ", declared_business, "%       ", expected_business, "%        ", difference_business, "%")
    print("Cash                  ", declared_cash, "%       ", expected_cash, "%        ", difference_cash, "%")

    print("\n--- Composition Result ---")

    if difference_property <= LIMITS["composition"]:
        print("Property: PASS")
    else:
        print("Property: FAIL")

    if difference_listed <= LIMITS["composition"]:
        print("Listed Equities: PASS")
    else:
        print("Listed Equities: FAIL")

    if difference_business <= LIMITS["composition"]:
        print("Private Business: PASS")
    else:
        print("Private Business: FAIL")

    if difference_cash <= LIMITS["composition"]:
        print("Cash: PASS")
    else:
        print("Cash: FAIL")


def run_benchmark(client_data: dict):
    """Run the full benchmark check for the given client data."""

    ai_data = get_ai_data(client_data)

    if ai_data is None:
        print("\n[Benchmark] AI call failed — could not retrieve benchmark data.")
        return None

    result1 = calculate_total_wealth(client_data, ai_data)
    print("\n--- Total Wealth Check ---")
    print("Declared Net Worth:", client_data["declared_net_worth"])
    print("Expected Wealth:   ", ai_data["expected_wealth"])
    print("Ratio:             ", round(result1, 2))

    if result1 <= LIMITS["total_wealth"]:
        print("Result:             PASS")
    else:
        print("Result:             FAIL")

    result2 = calculate_liquidity(client_data, ai_data)
    print("\n--- Liquidity Check ---")
    print("Declared Liquidity:", client_data["listed_equities"] + client_data["cash"], "%")
    print("Expected Liquidity:", ai_data["expected_liquidity"], "%")
    print("Ratio:             ", round(result2, 2))

    if result2 <= LIMITS["liquidity"]:
        print("Result:             PASS")
    else:
        print("Result:             FAIL")

    calculate_composition(client_data, ai_data)

    return ai_data

    #def calculate_velocity(client_data, ai_data):


# ---------------------------------------------------------------------------
# Sector Crime Scan Section
# ---------------------------------------------------------------------------

def _domain_is_trusted(url: str, trusted_domains: list[str]) -> bool:
    """Return True if the URL's domain matches or is a subdomain of a trusted domain."""
    netloc = urlparse(url).netloc.lower()
    for td in trusted_domains:
        if netloc == td or netloc.endswith("." + td):
            return True
    return False


def _split_org_names(org_string: str) -> set[str]:
    """Split 'FATF and Egmont Group' into {'fatf', 'egmont group'}."""
    s = org_string.replace(" and ", ",").replace("&", ",").replace("/", ",")
    return {p.strip().lower() for p in s.split(",") if p.strip()}


def verify_research(research: dict, policy: dict) -> tuple[dict, list[str]]:
    """Filter research reports by grounding, domain trust, and uniqueness.

    Pure function — no I/O, no network.

    Rules applied in order per report:
    1. URL must be in research["api_sources"] (actually retrieved by web search).
    2. URL domain must be in policy["trusted_sources"].
    3. Duplicate URLs are dropped (first kept).
    4. Surviving reports are renumbered R1, R2, ...

    Returns (clean_research_dict, warnings).
    """
    api_urls = {s["url"] for s in research.get("api_sources", [])}
    trusted = policy.get("trusted_sources", [])
    warnings: list[str] = []
    seen_urls: set[str] = set()
    kept: list[dict] = []

    for report in research.get("reports", []):
        url = report.get("url", "")
        rid = report.get("report_id", "?")

        if url not in api_urls:
            warnings.append(
                f"Removed {rid}: URL not found in web search results."
            )
            continue

        if not _domain_is_trusted(url, trusted):
            warnings.append(
                f"Removed {rid}: domain not in trusted sources."
            )
            continue

        if url in seen_urls:
            warnings.append(
                f"Removed {rid}: duplicate URL."
            )
            continue

        seen_urls.add(url)
        kept.append(report)

    renumbered = [{**r, "report_id": f"R{i}"} for i, r in enumerate(kept, 1)]

    clean_research = {
        "reports": renumbered,
        "api_sources": research.get("api_sources", []),
        "retrieved_at": research.get("retrieved_at", ""),
        "model": research.get("model", ""),
    }
    return clean_research, warnings


def verify_typology_sources(
    typologies: list[dict],
    clean_research: dict,
    policy: dict,
) -> tuple[list[dict], list[str]]:
    """Verify AI typology source citations against filtered research reports.

    Pure function — no I/O, no network.

    Checks per typology:
    1. source_ids exist in clean_research.
    2. source_quotes appear verbatim in the cited report's excerpts.
    3. corroborated: ≥2 valid reports from independent organisations.
    4. outdated: report older than policy["source_max_age_years"] relative
       to the year in clean_research["retrieved_at"] (not today's date).

    Returns (list_of_result_dicts, warnings).
    """
    report_map = {r["report_id"]: r for r in clean_research.get("reports", [])}
    max_age = policy.get("source_max_age_years", 10)

    try:
        reference_year = int(clean_research.get("retrieved_at", "")[:4])
    except (ValueError, IndexError):
        reference_year = 0

    results: list[dict] = []
    warnings: list[str] = []

    for typ in typologies:
        typ_id = typ.get("typology_id", "unknown")

        # Check 1: source_ids exist
        valid_ids: list[str] = []
        invalid_ids: list[str] = []
        for sid in typ.get("source_ids", []):
            if sid in report_map:
                valid_ids.append(sid)
            else:
                invalid_ids.append(sid)
                warnings.append(
                    f"Typology '{typ_id}': source {sid} not found in research."
                )

        # Check 2: quotes verified
        valid_quotes: list[dict] = []
        invalid_quotes: list[dict] = []
        for sq in typ.get("source_quotes", []):
            rid = sq.get("report_id", "")
            quote = sq.get("quote", "")
            if rid not in report_map:
                invalid_quotes.append({"report_id": rid, "quote": quote, "reason": "report_id not found"})
                warnings.append(
                    f"Typology '{typ_id}': quote from {rid} invalid — report not found."
                )
                continue
            excerpts = report_map[rid].get("excerpts", [])
            if any(quote in excerpt for excerpt in excerpts):
                valid_quotes.append({"report_id": rid, "quote": quote, "verified": True})
            else:
                invalid_quotes.append({"report_id": rid, "quote": quote, "reason": "quote not found in excerpts"})
                warnings.append(
                    f"Typology '{typ_id}': quote from {rid} not found verbatim in excerpts."
                )

        # Check 3: corroborated (valid ID AND verified quote)
        quote_verified_ids = {q["report_id"] for q in valid_quotes}
        fully_valid_ids = [sid for sid in valid_ids if sid in quote_verified_ids]
        org_sets = [_split_org_names(report_map[sid].get("organisation", "")) for sid in fully_valid_ids]

        corroborated = False
        for i in range(len(org_sets)):
            for j in range(i + 1, len(org_sets)):
                if org_sets[i].isdisjoint(org_sets[j]):
                    corroborated = True
                    break
            if corroborated:
                break

        # Check 4: outdated
        outdated_ids: list[str] = []
        for sid in valid_ids:
            year = report_map[sid].get("year", 0)
            if reference_year and (reference_year - year) > max_age:
                outdated_ids.append(sid)
                warnings.append(
                    f"Typology '{typ_id}': source {sid} is {reference_year - year} years old "
                    f"(limit {max_age})."
                )

        results.append({
            "typology_id": typ_id,
            "valid_source_ids": valid_ids,
            "invalid_source_ids": invalid_ids,
            "valid_quotes": valid_quotes,
            "invalid_quotes": invalid_quotes,
            "corroborated": corroborated,
            "outdated_source_ids": outdated_ids,
        })

    return results, warnings


def decide_outcome(findings: list[dict], policy: dict) -> str:
    """Count breached dimensions and return the escalation outcome."""
    thresholds = policy.get("thresholds", {})
    counts_as_breach = thresholds.get("typology_full_match_counts_as_breach", True)
    outcome_rules = policy.get("outcome_rules", {})
    serious = outcome_rules.get("serious_risk_min_breaches", 4)
    risky = outcome_rules.get("risky_min_breaches", 2)
    manual = outcome_rules.get("manual_review_min_breaches", 1)

    breach_count = 0
    for f in findings:
        if f.get("dimension") != "typology":
            breach_count += 1
        elif f.get("rule_id") == "TYP-01" and counts_as_breach:
            breach_count += 1
        # TYP-02 never counts as a breach on its own

    if breach_count >= serious:
        return "SERIOUS_RISK"
    if breach_count >= risky:
        return "RISKY"
    if breach_count >= manual:
        return "MANUAL_REVIEW"
    return "PASSED"


def build_document_requests(findings: list[dict], policy: dict) -> list[str]:
    """Collect document requests from findings, typology docs ranked last."""
    documents: list[str] = []

    # Non-typology findings first (higher priority)
    for f in findings:
        if f.get("dimension") == "typology":
            continue
        for doc in f.get("documents", []):
            if doc not in documents:
                documents.append(doc)

    # TYP-01 typology documents appended last (lower priority)
    for f in findings:
        if f.get("rule_id") == "TYP-01":
            for doc in f.get("documents", []):
                if doc not in documents:
                    documents.append(doc)

    return documents


def assess_case(
    case_input: dict,  # noqa: ARG001 — reserved for future dimension checks
    benchmark: dict | None,
    declaration: dict | None,
    policy: dict,
) -> dict:
    """Run the full assessment for a single case.

    Returns a dict with keys: outcome, findings, documents.
    """
    if benchmark is None or declaration is None:
        return {"outcome": "MANUAL_REVIEW", "findings": [], "documents": []}

    findings: list[dict] = []
    # Sector Crime Scan typology matching is being redesigned (Stage 3)

    outcome = decide_outcome(findings, policy)
    documents = build_document_requests(findings, policy)

    return {"outcome": outcome, "findings": findings, "documents": documents}


# ---------------------------------------------------------------------------
# Forecasting Section
# ---------------------------------------------------------------------------

# INSERT FORECASTING LOGIC BELOW
