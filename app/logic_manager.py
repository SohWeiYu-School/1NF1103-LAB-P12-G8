from app.ai_manager import get_ai_data

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

    #def calculate_velocity(client_data, ai_data):


# ---------------------------------------------------------------------------
# Sector Crime Scan Section
# ---------------------------------------------------------------------------

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
