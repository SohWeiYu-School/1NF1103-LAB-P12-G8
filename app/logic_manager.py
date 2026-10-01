"""Pure scoring and decision functions. No side effects, no I/O, no network."""


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
