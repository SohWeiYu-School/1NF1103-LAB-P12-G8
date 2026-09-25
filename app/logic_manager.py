"""Pure scoring and decision functions. No side effects, no I/O, no network."""

import logging

logger = logging.getLogger(__name__)

RELATED_PARTY_KEYWORDS = [
    "father", "mother", "brother", "sister", "son", "daughter",
    "spouse", "wife", "husband", "family", "friend", "relative",
]

_UNNAMED_COUNTERPARTY_VALUES = {"", "unknown", "not named", "a friend"}


def check_offshore_entity_present(sources: list[dict], benchmark: dict) -> tuple[bool, str]:
    """True if any wealth source sits in a jurisdiction the benchmark did not expect."""
    expected = benchmark.get("expected_jurisdictions", [])
    for source in sources:
        country = source.get("jurisdiction")
        if country and country not in expected:
            return True, f"{source.get('source_type')} held in {country}, not in expected jurisdictions"
    return False, ""


def check_counterparty_unnamed(sources: list[dict], benchmark: dict) -> tuple[bool, str]:
    """True if any source has a missing, empty or vague counterparty."""
    for source in sources:
        raw = source.get("counterparty")
        if raw is None or str(raw).lower().strip() in _UNNAMED_COUNTERPARTY_VALUES:
            return True, f"{source.get('source_type')} has unnamed counterparty"
    return False, ""


def check_rapid_accumulation(sources: list[dict], benchmark: dict) -> tuple[bool, str]:
    """True if any source accumulated faster than the benchmark's maximum annual rate."""
    limit = benchmark.get("max_accumulation_per_year_sgd")
    if limit is None:
        return False, ""
    for source in sources:
        amount = source.get("amount_sgd")
        start = source.get("start_year")
        if amount is None or start is None:
            continue
        end = source.get("end_year") or start
        rate = amount / max(1, end - start)
        if rate > limit:
            return (
                True,
                f"{source.get('source_type')} accumulated at {rate:,.0f} SGD/year "
                f"against limit of {limit:,.0f} SGD/year",
            )
    return False, ""


def check_unvalued_source_present(sources: list[dict], benchmark: dict) -> tuple[bool, str]:
    """True if any source has no stated amount."""
    for source in sources:
        if source.get("amount_sgd") is None:
            return True, f"{source.get('source_type')} has no stated amount"
    return False, ""


def check_related_party_transaction(sources: list[dict], benchmark: dict) -> tuple[bool, str]:
    """True if a source's counterparty or description mentions a family or personal relationship."""
    for source in sources:
        for field in ("counterparty", "description"):
            text = source.get(field)
            if not text:
                continue
            text_lower = str(text).lower()
            for keyword in RELATED_PARTY_KEYWORDS:
                if keyword in text_lower:
                    return True, f"{source.get('source_type')} {field} mentions '{keyword}'"
    return False, ""


INDICATOR_CHECKS = {
    "offshore_entity_present": check_offshore_entity_present,
    "counterparty_unnamed": check_counterparty_unnamed,
    "rapid_accumulation": check_rapid_accumulation,
    "unvalued_source_present": check_unvalued_source_present,
    "related_party_transaction": check_related_party_transaction,
}


def match_typologies(
    sources: list[dict],
    benchmark: dict,
    policy: dict,
) -> list[dict]:
    """Run each typology's indicator checks against the client's wealth sources.

    Returns a list of findings. A finding is produced only when at least one
    core indicator matches. Returns an empty list when nothing matches.
    """
    typologies = benchmark.get("sector_typologies")
    if not typologies:
        return []

    thresholds = policy.get("thresholds", {})
    min_ratio = thresholds.get("typology_match_min_ratio", 0.6)
    full_severity = thresholds.get("typology_full_match_severity", 4)
    partial_severity = thresholds.get("typology_partial_match_severity", 2)

    findings = []

    for typology in typologies:
        indicators = typology.get("indicators", [])
        core_items = [i for i in indicators if i.get("weight") == "core"]

        if not core_items:
            logger.warning(
                "Typology '%s' has no core indicators — treating as no match.",
                typology.get("typology_id", "unknown"),
            )
            continue

        # Run each indicator check; skip unknown names without raising.
        matched_evidence = {}
        for item in indicators:
            name = item.get("indicator")
            if name not in INDICATOR_CHECKS:
                logger.warning(
                    "Unknown indicator '%s' in typology '%s' — skipping.",
                    name,
                    typology.get("typology_id", "unknown"),
                )
                continue
            hit, evidence = INDICATOR_CHECKS[name](sources, benchmark)
            if hit:
                matched_evidence[name] = evidence

        matched_core_count = sum(
            1 for i in core_items if i.get("indicator") in matched_evidence
        )
        all_cores_matched = matched_core_count == len(core_items)
        total = len(indicators)
        ratio = len(matched_evidence) / total if total > 0 else 0.0

        if all_cores_matched and ratio >= min_ratio:
            rule_id = "TYP-01"
            severity = full_severity
            documents = typology.get("typical_documents", [])
        elif matched_core_count > 0:
            rule_id = "TYP-02"
            severity = partial_severity
            documents = []
        else:
            continue

        matched_total = len(matched_evidence)
        detail = (
            f"{typology.get('name', typology.get('typology_id', 'Unknown'))} "
            f"— {matched_total} of {total} indicators"
        )
        evidence_list = [
            f"{name}: {ev}" for name, ev in matched_evidence.items()
        ]

        findings.append({
            "rule_id": rule_id,
            "dimension": "typology",
            "detail": detail,
            "evidence": evidence_list,
            "severity": severity,
            "documents": documents,
        })

    return findings


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

    sources = declaration.get("sources", [])
    findings: list[dict] = []

    findings.extend(match_typologies(sources, benchmark, policy))

    outcome = decide_outcome(findings, policy)
    documents = build_document_requests(findings, policy)

    return {"outcome": outcome, "findings": findings, "documents": documents}
