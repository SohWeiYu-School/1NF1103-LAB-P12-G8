"""
logic_manager.py - the logic layer: every calculation and every decision.

The sections run in this order for a new client:

    1. Benchmark          run_benchmark: declared figures vs the AI's single expected
                          values (total wealth, liquidity, composition) -> PASS / FAIL
    2. Sector Crime Scan  decide_outcome, build_document_requests, assess_typology_case
    3. Forecasting        (runs after the benchmark)
       Onboarding (assess_onboarding):
         Types: T1 is the honest person; T2+ are standard crime categories (trade-based money
         laundering, sanctions evasion, ...) each tied to the job/asset/source that enables it.
         1. Back-test every type: build each type's wealth, year by year, from the forecasts
         2. Fit score per type: does his declared wealth, asset values and purchases fit it?
         3. Affordability per type: could he have paid for each purchase that year?
         4. Checks that don't depend on the type: asset values, asset income, countries,
            declared sources with no amount
         5. Stability test: re-run the decision with every forecast 10% lower and higher
         6. Forward paths, event impacts, divergence dates, monitoring ranges,
            ranked documents, next review date
       Review (assess_review):
         Which scenario happened, growth vs each type's forecast, payments vs each type's
         forecast, income that rose while its market fell, and a running score per type.

The forecasting section calls no AI: it only does arithmetic on the forecasts and decides
with fixed rules from config/policy.json, so the same forecast and the same client always
give the same answer.

Outcomes: PASSED, MANUAL_REVIEW, RISKY, SERIOUS_RISK.
"""

import copy
import datetime
import logging
import re

from app import utilities
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


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

    prop_failed     = difference_property  > LIMITS["composition"]
    listed_failed   = difference_listed    > LIMITS["composition"]
    business_failed = difference_business  > LIMITS["composition"]
    cash_failed     = difference_cash      > LIMITS["composition"]

    return [
        {"rule_id": "COMPOSITION_PROPERTY",  "failed": prop_failed,
         "detail": f"Property: declared {declared_property}%, expected {expected_property}%, diff {difference_property}%"},
        {"rule_id": "COMPOSITION_EQUITIES",  "failed": listed_failed,
         "detail": f"Listed equities: declared {declared_listed}%, expected {expected_listed}%, diff {difference_listed}%"},
        {"rule_id": "COMPOSITION_BUSINESS",  "failed": business_failed,
         "detail": f"Private business: declared {declared_business}%, expected {expected_business}%, diff {difference_business}%"},
        {"rule_id": "COMPOSITION_CASH",      "failed": cash_failed,
         "detail": f"Cash: declared {declared_cash}%, expected {expected_cash}%, diff {difference_cash}%"},
    ]

#=============================================================================#
def calculate_velocity(client_data, ai_data):

    actual = client_data["biggest_wealth_jump"]
    years = client_data["wealth_jump_years"]
    expected = ai_data["expected_velocity"]

    actual_velocity = actual / years
    result3 = actual_velocity / expected

    return result3

#=============================================================================#
def calculate_counterparties(declared, expected):
    unknown = []

    for item in declared:
        if item not in expected:
            unknown.append(item)

    return unknown

#=============================================================================#
def clean_country(country):
    if country == "SG":
        return "Singapore"
    elif country == "MY":
        return "Malaysia"
    elif country == "ID":
        return "Indonesia"
    elif country == "HK":
        return "Hong Kong"
    elif country == "AE":
        return "United Arab Emirates"

    return country

def calculate_jurisdiction(declared, expected):
    unknown = []

    for country in declared:
        country = clean_country(country)

        if country not in expected:
            unknown.append(country)

    return unknown


def crime_scan_input(record: dict) -> dict:
    """The crime scan input from a saved client record (used by both the CLI and the web app).
    Raises KeyError if the record has no career or no source of wealth declaration."""
    career = record["career"]
    sow = record["sow_declaration"]
    return {
        "client_ref":           record["client_ref"],
        "occupation":           career["occupation"],
        "industry":             career["industry"],
        "age":                  utilities.whole_number(career["age"]),
        "career_start_year":    utilities.whole_number(career["career_start_year"]),
        "country":              career["country_of_residence"],
        "country_of_residence": career["country_of_residence"],
        "declaration_text":     sow if isinstance(sow, str) else (sow or {}).get("declaration_text", ""),
    }


def _whole_percent(value) -> int | None:
    """A share of wealth as a whole-number percent: 0.45 (stored fraction) or 45 -> 45."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value * 100) if value <= 1.0 else round(value)


def benchmark_input(record: dict, case_input: dict) -> tuple[dict, list[str]]:
    """The benchmark's input built from a saved client record - the CLI and the web app both use this,
    so the same client always gets the same benchmark. Composition is in whole percents (45 = 45%),
    the unit run_benchmark and the benchmark prompt expect.
    Returns (input, names of the required values that are missing)."""
    claims = record.get("claims") or {}
    split = claims.get("asset_composition") or {}
    worth = claims.get("declared_net_worth")
    jump = claims.get("biggest_wealth_jump")
    data = {
        **case_input,
        "declared_net_worth":      utilities.whole_number(worth),
        "listed_equities":         _whole_percent(split.get("listed_equities")),
        "cash":                    _whole_percent(split.get("cash")),
        "property":                _whole_percent(split.get("property")),
        "private_business":        _whole_percent(split.get("private_business")),
        "biggest_wealth_jump":     float(jump) if isinstance(jump, (int, float)) else None,
        "wealth_jump_years":       float(claims.get("wealth_jump_years") or 0.0),
        "declared_counterparties": list(claims.get("declared_counterparties") or []),
        "declared_countries":      list(claims.get("wealth_countries") or []),
    }
    missing = [key for key in ("declared_net_worth", "listed_equities", "cash", "property", "private_business")
               if data[key] is None]
    return data, missing


def run_benchmark(client_data: dict, ai_data: dict):
    """Run all six benchmark checks for a client."""

    if ai_data is None:
        return None

    findings = []

    # 1. TOTAL WEALTH CHECK
    result1 = calculate_total_wealth(client_data, ai_data)
    tw_failed = result1 > LIMITS["total_wealth"]
    findings.append({"rule_id": "TOTAL_WEALTH", "failed": tw_failed,
                     "detail": f"Declared {client_data['declared_net_worth']:,}, expected {ai_data['expected_wealth']:,}, ratio {round(result1, 2)}"})

    # 2. LIQUIDITY CHECK
    result2 = calculate_liquidity(client_data, ai_data)
    liq_failed = result2 > LIMITS["liquidity"]
    findings.append({"rule_id": "LIQUIDITY", "failed": liq_failed,
                     "detail": f"Declared {client_data['listed_equities'] + client_data['cash']}%, expected {ai_data['expected_liquidity']}%, ratio {round(result2, 2)}"})

    # 3. ASSET COMPOSITION CHECK
    findings += calculate_composition(client_data, ai_data)

    # 4. VELOCITY CHECK
    jump = client_data.get("biggest_wealth_jump")
    years = client_data.get("wealth_jump_years")
    expected_velocity = ai_data.get("expected_velocity")

    if (
        jump is not None
        and years is not None
        and years > 0
        and expected_velocity is not None
        and expected_velocity > 0
    ):
        actual_velocity = jump / years
        result3 = actual_velocity / expected_velocity
        vel_failed = result3 > LIMITS["velocity"]
        findings.append({"rule_id": "VELOCITY", "failed": vel_failed,
                         "detail": f"Actual velocity {round(actual_velocity, 2)}, expected {expected_velocity}, ratio {round(result3, 2)}"})
    else:
        findings.append({"rule_id": "VELOCITY", "failed": False,
                         "detail": "Skipped — missing or invalid wealth timeline data"})

    # 5. COUNTERPARTIES CHECK
    declared_counterparties = client_data.get("declared_counterparties", [])
    expected_counterparties = ai_data.get("expected_counterparties", [])
    unknown_counterparties = calculate_counterparties(declared_counterparties, expected_counterparties)
    cp_failed = len(unknown_counterparties) > LIMITS["counterparties"]
    findings.append({"rule_id": "COUNTERPARTIES", "failed": cp_failed,
                     "detail": f"Unknown counterparties: {unknown_counterparties or 'none'}"})

    # 6. JURISDICTION CHECK
    declared_countries = client_data.get("declared_countries", [])
    expected_countries = ai_data.get("expected_jurisdiction", [])
    unknown_countries = calculate_jurisdiction(declared_countries, expected_countries)
    jur_failed = len(unknown_countries) > LIMITS["jurisdiction"]
    findings.append({"rule_id": "JURISDICTION", "failed": jur_failed,
                     "detail": f"Unknown countries: {unknown_countries or 'none'}"})

    return {"findings": findings}

    
# ---------------------------------------------------------------------------
# Crime Scan Section — Research & Source Verification
# ---------------------------------------------------------------------------

# Filter AI research reports: keep only grounded, trusted-domain, non-duplicate URLs.
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

    clean_research = {
        "reports": kept,
        "api_sources": research.get("api_sources", []),
        "retrieved_at": research.get("retrieved_at", ""),
        "model": research.get("model", ""),
    }
    return clean_research, warnings


# Drop typology quotes not found verbatim in cited reports; drop typologies with nothing left.
def verify_typology_sources(
    typologies: list[dict],
    clean_research: dict,
    policy: dict,
) -> tuple[list[dict], list[str]]:
    """Verify and clean AI typology source citations against filtered research reports.

    Pure function — no I/O, no network.

    For each typology:
    1. source_ids not found in clean_research are removed.
    2. source_quotes whose quote does not appear (after normalisation) in the
       cited report's excerpts are removed.
    3. Typologies with no verified quotes left are dropped entirely.
    4. corroborated: warns if fewer than 2 independent-org sources remain.
    5. outdated: warns if a source exceeds policy[source_max_age_years].
    6. unknown_age: reports with missing/invalid year are noted, not outdated.

    Returns (cleaned_typologies, warnings).
    """
    report_map = {r["report_id"]: r for r in clean_research.get("reports", [])}
    max_age = policy.get("source_max_age_years", 10)

    try:
        reference_year = int(clean_research.get("retrieved_at", "")[:4])
    except (ValueError, IndexError):
        reference_year = 0

    cleaned: list[dict] = []
    warnings: list[str] = []

    for typ in typologies:
        typ_id = typ.get("typology_id", "unknown")

        # Check 1: source_ids exist — keep only valid ones
        valid_ids: list[str] = []
        for sid in typ.get("source_ids", []):
            if sid in report_map:
                valid_ids.append(sid)
            else:
                warnings.append(f"Typology '{typ_id}': source {sid} not found in research.")

        # Check 2: quotes verified (with normalisation) — keep only verified ones
        valid_quotes: list[dict] = []
        for sq in typ.get("source_quotes", []):
            rid = sq.get("report_id", "")
            quote = sq.get("quote", "")
            if rid not in report_map:
                warnings.append(
                    f"Typology '{typ_id}': quote from {rid} invalid — report not found."
                )
                continue
            excerpts = report_map[rid].get("excerpts", [])
            norm_quote = _normalise_text(quote)
            if any(norm_quote in _normalise_text(e) for e in excerpts):
                valid_quotes.append({"report_id": rid, "quote": quote})
            else:
                warnings.append(
                    f"Typology '{typ_id}': quote from {rid} not found verbatim in excerpts."
                )

        # Drop typology if no verified quotes remain
        if not valid_quotes:
            warnings.append(f"Typology '{typ_id}': dropped — no verified quotes remain.")
            continue

        # Check 3: corroborated (valid ID + verified quote, independent orgs)
        quote_ids = {q["report_id"] for q in valid_quotes}
        grounded_ids = [sid for sid in valid_ids if sid in quote_ids]
        org_sets = [
            _split_org_names(report_map[sid].get("organisation", ""))
            for sid in grounded_ids
        ]
        corroborated = any(
            org_sets[i].isdisjoint(org_sets[j])
            for i in range(len(org_sets))
            for j in range(i + 1, len(org_sets))
        )
        # Check 4: outdated and unknown age
        # Outdated is stored in the result for display — NOT a warning (informational only).
        # Missing/invalid year IS a warning (data quality problem).
        outdated_ids: list[str] = []
        for sid in valid_ids:
            year = report_map[sid].get("year")
            if not isinstance(year, int) or year <= 0:
                warnings.append(
                    f"Typology '{typ_id}': source {sid} has no valid year (age check skipped)."
                )
            elif reference_year and (reference_year - year) > max_age:
                outdated_ids.append(sid)

        cleaned.append({
            **typ,
            "source_ids": valid_ids,
            "source_quotes": valid_quotes,
            "corroborated": corroborated,
            "outdated_source_ids": outdated_ids,
        })

    return cleaned, warnings


# Normalise text for quote matching — lowercase, collapse whitespace, unify smart quotes and dashes.
def _normalise_text(text: str) -> str:
    """Lowercase, collapse whitespace, unify quote marks and dashes for substring matching."""
    text = text.lower()
    for ch in '\u201c\u201d':   # " "  → "
        text = text.replace(ch, '"')
    for ch in '\u2018\u2019':   # ' '  → '
        text = text.replace(ch, "'")
    text = text.replace('\u2013', '-').replace('\u2014', '-')   # en/em dash → -
    return re.sub(r'\s+', ' ', text).strip()


# Check if a URL's domain is in the trusted sources list (including subdomains).
def _domain_is_trusted(url: str, trusted_domains: list[str]) -> bool:
    """Return True if the URL's domain matches or is a subdomain of a trusted domain."""
    netloc = urlparse(url).netloc.lower()
    for td in trusted_domains:
        if netloc == td or netloc.endswith("." + td):
            return True
    return False


# Split a combined organisation string into individual names for corroboration checks.
def _split_org_names(org_string: str) -> set[str]:
    """Split 'FATF and Egmont Group' into {'fatf', 'egmont group'}."""
    s = org_string.replace(" and ", ",").replace("&", ",").replace("/", ",")
    return {p.strip().lower() for p in s.split(",") if p.strip()}


# ---------------------------------------------------------------------------
# # Crime Scan Section — Typologies Check
# ---------------------------------------------------------------------------

_RELATED_PARTY_KEYWORDS = [
    "associate", "family", "relative", "friend",
    "brother", "sister", "spouse", "wife", "husband",
    "son", "daughter", "parent", "mother", "father",
    "partner", "in-law",
]

# Orchestrate the full crime scan: normalise case → match typologies → count breaches.
def assess_typology_case(
    case_input: dict,
    typology_response: dict | None,
    declaration: dict | None,
    policy: dict,
) -> dict:
    """Run the full Sector Crime Scan assessment for a single case.

    Returns a dict with keys: outcome, findings, documents, match_results.
    """

    # If either AI call failed entirely (returned None), skip assessment
    if typology_response is None or declaration is None:
        return {"breach_count": 0, "match_count": 0, "match_results": []}

    # Normalise the client record so country fields and int fields are clean
    clean_case, _ = prepare_case_for_matching(case_input)
    typologies = typology_response.get("sector_typologies", [])

    # Run all 5 indicators against each typology and collect match results
    match_results = match_typologies(typologies, declaration, clean_case, policy)

    # Count full breaches (critical/high/medium) and all matches including partial/weak
    breach_count = sum(1 for mr in match_results if mr["breached"])
    match_count = sum(1 for mr in match_results if mr["category"] != "no_match")
    return {"breach_count": breach_count, "match_count": match_count, "match_results": match_results}


# Fire if any gift or inheritance source has no named counterparty (anonymous gift/bequest).
def _check_gift_or_inheritance_unnamed(declaration: dict) -> bool:
    """True if any gift or inheritance source has no counterparty named."""
    gift_types = {"inheritance", "gift"}
    return any(
        s.get("source_type") in gift_types and s.get("counterparty") is None
        for s in declaration.get("sources", [])
    )


# Fire if too many declaration sources have no named counterparty (policy threshold).
def _check_counterparty_unnamed(declaration: dict, policy: dict) -> bool:
    """True if the count of null-counterparty sources meets or exceeds the policy limit."""
    limit = policy.get("thresholds", {}).get("unknown_counterparty_limit", 1)
    count = sum(1 for s in declaration.get("sources", []) if s.get("counterparty") is None)
    return count >= limit


# Fire if any declared source was flagged by the declaration AI as mismatching the occupation.
def _check_source_contradicts_occupation(declaration: dict) -> bool:
    """True if the AI marked any source as inconsistent with the client's occupation."""
    return any(s.get("occupation_mismatch") is True for s in declaration.get("sources", []))


# Fire if any declared wealth source has no SGD amount attached.
def _check_unvalued_source(declaration: dict) -> bool:
    """True if any declaration source has amount_sgd = null.
    A salary, bonus or savings source tied to one of his jobs is left out: the forecast works
    out what that job paid, so it doesn't need an amount (crime_declaration marks these)."""
    return any(s.get("amount_sgd") is None and not s.get("amount_explained_by_forecast")
               for s in declaration.get("sources", []))


# Fire if any counterparty name contains a related-party keyword (family, associate, etc.).
def _check_related_party(declaration: dict) -> bool:
    """True if any counterparty contains a related-party keyword."""
    for src in declaration.get("sources", []):
        cp = src.get("counterparty")
        if cp and any(kw in cp.lower() for kw in _RELATED_PARTY_KEYWORDS):
            return True
    return False


# Map fired indicator counts to a severity category.
def _categorise_match(core_fired: list, core: list, supporting_fired: list) -> str:
    all_core = len(core_fired) == len(core) and len(core) > 0
    n_sup = len(supporting_fired)
    if all_core:
        if n_sup >= 2:
            return "critical"
        if n_sup == 1:
            return "high"
        return "medium"
    if n_sup >= 2:
        return "partial"
    if n_sup >= 1:
        return "weak_signal"
    return "no_match"


# Run all 5 indicators against the client; a typology breaches when every core indicator fires.
def match_typologies(
    typologies: list[dict],
    declaration: dict,
    clean_case: dict,
    policy: dict,
) -> list[dict]:
    """Match each typology's indicators against client data. Pure function.

    A typology breaches when ALL its core indicators fire.
    Returns a list of match result dicts, one per typology.
    """
    indicator_results = {
        "gift_or_inheritance_unnamed":    _check_gift_or_inheritance_unnamed(declaration),
        "counterparty_unnamed":           _check_counterparty_unnamed(declaration, policy),
        "source_contradicts_occupation":  _check_source_contradicts_occupation(declaration),
        "unvalued_source_present":        _check_unvalued_source(declaration),
        "related_party_transaction":      _check_related_party(declaration),
    }

    matches: list[dict] = []
    for typ in typologies:
        indicators = typ.get("indicators", [])
        core = [i["indicator"] for i in indicators if i.get("weight") == "core"]
        supporting = [i["indicator"] for i in indicators if i.get("weight") == "supporting"]
        core_fired = [i for i in core if indicator_results.get(i, False)]
        supporting_fired = [i for i in supporting if indicator_results.get(i, False)]
        breached = len(core_fired) == len(core) and len(core) > 0
        category = _categorise_match(core_fired, core, supporting_fired)

        matches.append({
            "typology_id": typ.get("typology_id", ""),
            "name": typ.get("name", ""),
            "source_ids": typ.get("source_ids", []),
            "breached": breached,
            "category": category,
            "core_indicators": core,
            "core_fired": core_fired,
            "supporting_indicators": supporting,
            "supporting_fired": supporting_fired,
            "typical_documents": typ.get("typical_documents", []),
        })
    return matches


# Count breached dimensions and map to an escalation outcome (PASSED → SERIOUS_RISK).
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


# Build a deduplicated list of required documents; non-typology findings take priority.
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


# ---------------------------------------------------------------------------
# # Crime Scan Section — data normalisation helpers
# ---------------------------------------------------------------------------

_COUNTRY_TO_ISO = {
    "singapore": "SG",
    "malaysia": "MY",
    "hong kong": "HK",
    "united arab emirates": "AE",
    "uae": "AE",
    "dubai": "AE",
    "abu dhabi": "AE",
    "australia": "AU",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "united states": "US",
    "usa": "US",
    "united states of america": "US",
    "indonesia": "ID",
    "thailand": "TH",
    "philippines": "PH",
    "china": "CN",
    "japan": "JP",
    "india": "IN",
    "south korea": "KR",
    "korea": "KR",
    "taiwan": "TW",
    "vietnam": "VN",
    "myanmar": "MM",
    "cambodia": "KH",
    "switzerland": "CH",
    "germany": "DE",
    "france": "FR",
    "netherlands": "NL",
    "luxembourg": "LU",
    "cayman islands": "KY",
    "british virgin islands": "VG",
    "bvi": "VG",
    "bermuda": "BM",
    "jersey": "JE",
    "guernsey": "GG",
    "isle of man": "IM",
    "panama": "PA",
    "seychelles": "SC",
    "mauritius": "MU",
    "liechtenstein": "LI",
    "monaco": "MC",
    "canada": "CA",
    "new zealand": "NZ",
    "ireland": "IE",
    "bahrain": "BH",
    "qatar": "QA",
    "saudi arabia": "SA",
    "oman": "OM",
    "kuwait": "KW",
    "nigeria": "NG",
    "south africa": "ZA",
    "kenya": "KE",
    "brazil": "BR",
    "mexico": "MX",
    "cyprus": "CY",
    "malta": "MT",
}


# Convert a country name or short code to ISO 3166-1 alpha-2, with a warning if unrecognised.
def _to_iso(value, warnings: list) -> str | None:
    """Convert a country name or code to ISO 3166-1 alpha-2. Pure, no I/O.

    - None/empty → None (no warning)
    - Already 2 uppercase letters → return as-is
    - Lookup in _COUNTRY_TO_ISO (case-insensitive) → return code
    - 2 letters, not uppercase → uppercase (e.g. "sg" → "SG")
    - Unrecognized → keep as-is + warning
    """
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    if len(stripped) == 2 and stripped.isalpha() and stripped.isupper():
        return stripped
    lookup = _COUNTRY_TO_ISO.get(stripped.lower())
    if lookup:
        return lookup
    if len(stripped) == 2 and stripped.isalpha():
        return stripped.upper()
    warnings.append(f"Unknown country '{stripped}' — could not convert to ISO-2.")
    return stripped


# Safely cast a value to int, returning default if None or conversion fails.
def _safe_int(value, default=None):
    """Convert value to int if possible. Returns default on None or failure. Pure."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


# Normalise a client record (ISO codes, int fields, all_jurisdictions) ready for typology matching.
def prepare_case_for_matching(record: dict) -> tuple[dict, list[str]]:
    """Normalize a client record for typology matching. Pure function.

    Returns (normalized_record, warnings).

    1. Coerces age, career_start_year to int.
    2. Converts country/country_of_residence to ISO-2.
    3. Builds all_jurisdictions: sorted deduplicated list of ISO-2 codes from
       country, country_of_residence, wealth_countries, career_timeline,
       asset_timeline, and declarations.wealth_countries.
    4. Extracts declared_net_worth_sgd from whichever location has it.
    5. Extracts reference_year from record or date fields.
    Does NOT mutate the original record or any sub-dicts.
    """
    warnings: list[str] = []
    out = {**record}

    out["age"] = _safe_int(record.get("age"))
    out["career_start_year"] = _safe_int(record.get("career_start_year"))
    out["country"] = _to_iso(record.get("country"), warnings)
    out["country_of_residence"] = _to_iso(record.get("country_of_residence"), warnings)

    # declared_net_worth_sgd — check multiple possible field locations
    raw_nw = record.get("declared_net_worth_sgd")
    if raw_nw is None:
        raw_nw = (record.get("declarations") or {}).get("declared_net_worth")
    if raw_nw is None:
        raw_nw = (record.get("client_declarations") or {}).get("declared_net_worth")
    out["declared_net_worth_sgd"] = _safe_int(raw_nw)

    # reference_year — explicit field, or extracted from date fields
    ref = record.get("reference_year")
    if ref is None:
        date_str = (
            record.get("date_profile_created")
            or (record.get("client_profile") or {}).get("date_profile_created")
            or (record.get("declarations") or {}).get("date_declared")
            or (record.get("client_declarations") or {}).get("date_declared")
        )
        if date_str:
            ref = _safe_int(str(date_str)[:4])
    out["reference_year"] = _safe_int(ref)

    all_iso: set[str] = set()

    if out["country"]:
        all_iso.add(out["country"])
    if out["country_of_residence"]:
        all_iso.add(out["country_of_residence"])

    # Top-level wealth_countries (golden case format)
    for c in record.get("wealth_countries", []):
        code = _to_iso(c, warnings)
        if code:
            all_iso.add(code)

    for entry in record.get("career_timeline", []):
        code = _to_iso(entry.get("country"), warnings)
        if code:
            all_iso.add(code)

    for entry in record.get("asset_timeline", []):
        code = _to_iso(entry.get("country"), warnings)
        if code:
            all_iso.add(code)

    decl = record.get("declarations", {})
    for c in (decl or {}).get("wealth_countries", []):
        code = _to_iso(c, warnings)
        if code:
            all_iso.add(code)

    out["all_jurisdictions"] = sorted(all_iso)
    return out, warnings


# Convert jurisdiction strings in a parsed AI declaration to ISO-2 codes.
def normalize_declaration_jurisdictions(declaration: dict) -> tuple[dict, list[str]]:
    """Post-process AI declaration output to ensure jurisdiction fields are ISO-2.

    Pure function. Called after schema validation succeeds.
    Per source:
    - Valid ISO-2 (2 uppercase letters) → kept.
    - Full name in _COUNTRY_TO_ISO → converted + warning.
    - 2 lowercase letters → uppercased + warning.
    - Unrecognizable string → set to None + warning.
    - None → kept.
    Returns (cleaned_declaration, warnings).
    """
    warnings: list[str] = []
    sources = []
    for src in declaration.get("sources", []):
        new_src = {**src}
        j = src.get("jurisdiction")
        if j is None:
            sources.append(new_src)
            continue
        stripped = str(j).strip()
        if len(stripped) == 2 and stripped.isalpha() and stripped.isupper():
            sources.append(new_src)
            continue
        lookup = _COUNTRY_TO_ISO.get(stripped.lower())
        if lookup:
            warnings.append(f"Declaration jurisdiction '{j}' converted to '{lookup}'.")
            new_src["jurisdiction"] = lookup
        elif len(stripped) == 2 and stripped.isalpha():
            upper = stripped.upper()
            warnings.append(f"Declaration jurisdiction '{j}' uppercased to '{upper}'.")
            new_src["jurisdiction"] = upper
        else:
            warnings.append(f"Declaration jurisdiction '{j}' unrecognizable — set to null.")
            new_src["jurisdiction"] = None
        sources.append(new_src)
    return {**declaration, "sources": sources}, warnings

# ===========================================================================
# Forecasting Section (runs after the benchmark)
# ===========================================================================

PASSED, MANUAL_REVIEW, RISKY, SERIOUS_RISK = "PASSED", "MANUAL_REVIEW", "RISKY", "SERIOUS_RISK"
SEVERITY = {PASSED: 0, MANUAL_REVIEW: 1, RISKY: 2, SERIOUS_RISK: 3}

# Every money result is kept at three levels.
BANDS = ("cautious", "typical", "optimistic")
# Which end of an AI range each level uses. For money coming in, cautious = low.
# For money going out (tax, living costs), cautious = high.
MONEY_IN = {"cautious": "low", "typical": "typical", "optimistic": "high"}
MONEY_OUT = {"cautious": "high", "typical": "typical", "optimistic": "low"}


# ============================================================================
# Small helpers
# ============================================================================

def band(cautious: float, typical: float, optimistic: float) -> dict:
    return {"cautious": round(cautious, 2), "typical": round(typical, 2), "optimistic": round(optimistic, 2)}


def zero_band() -> dict:
    return band(0, 0, 0)


def add_bands(first: dict, second: dict) -> dict:
    return band(*(first[b] + second[b] for b in BANDS))


def scale_band(values: dict, factor: float) -> dict:
    return band(*(values[b] * factor for b in BANDS))


def sorted_band(three_values: list[float]) -> dict:
    """Three results worked out from the low, typical and high inputs, lowest first."""
    low, middle, high = sorted(three_values)
    return band(low, middle, high)


def range_to_band(ai_range: dict) -> dict:
    """An AI {low, typical, high} range as a cautious/typical/optimistic band."""
    return band(ai_range["low"], ai_range["typical"], ai_range["high"])


def add_months(date_text: str, months: int) -> str:
    """'2026-09-24' + 3 months = '2026-12-24' (the day is clipped to the month's length)."""
    date = datetime.date.fromisoformat(date_text)
    month_index = date.month - 1 + months
    year, month = date.year + month_index // 12, month_index % 12 + 1
    first_of_next = datetime.date(year + month // 12, month % 12 + 1, 1)
    last_day = (first_of_next - datetime.timedelta(days=1)).day
    return datetime.date(year, month, min(date.day, last_day)).isoformat()


def same_country(name: str, policy: dict) -> str:
    """Lower-case a country name and map short forms (UAE, Dubai) to one spelling."""
    text = (name or "").strip().lower()
    return policy.get("country_aliases", {}).get(text, text)


def category_name(code: str) -> str:
    """'TRADE_BASED_ML' -> its readable name from the vocabulary file."""
    return utilities.vocabulary().get("crime_categories", {}).get(code, code)


def enabled_by_text(person_type: dict, record: dict) -> str:
    """What makes this type possible for him, in words: the job, asset or declared source."""
    if person_type["type_id"] == utilities.BASELINE_TYPE:
        return "everything he declared"
    parts = []
    jobs = utilities.career_positions(record)
    assets = record.get("investments", [])
    job_index = person_type.get("enabled_by_position_index")
    asset_index = person_type.get("enabled_by_asset_index")
    if job_index is not None and 0 <= job_index < len(jobs):
        job = jobs[job_index]
        parts.append(f"job {job_index} ({job.get('title') or job.get('occupation') or 'position'}, "
                     f"{job.get('country', '')})")
    if asset_index is not None and 0 <= asset_index < len(assets):
        parts.append(f"asset {asset_index} ({assets[asset_index].get('asset_type', 'asset')})")
    if person_type.get("enabled_by_source_id"):
        parts.append(f"declared source {person_type['enabled_by_source_id']}")
    return " and ".join(parts) or "not stated"


def asset_category(asset_type: str, policy: dict) -> str:
    """property / listed_equities / private_business, by keyword lookup."""
    text = (asset_type or "").lower()
    for category, keywords in policy["asset_categories"].items():
        if any(word in text for word in keywords):
            return category
    return "other"


# ============================================================================
# 1. Markets (from Call 1)
# ============================================================================

def market_history(forecast: dict) -> dict:
    """{market_id: {year: % change or None}}"""
    return {market_id: {row["year"]: row["change_pct"] for row in series["history"]}
            for market_id, series in forecast["markets"]["series"].items()}


def market_outlook(forecast: dict) -> dict:
    """{market_id: {year: {scenario: {low, typical, high}}}}"""
    return {market_id: {row["year"]: {s: row[s] for s in utilities.SCENARIOS} for row in series["outlook"]}
            for market_id, series in forecast["markets"]["series"].items()}


def scenario_chances(forecast: dict) -> dict:
    """{year: {scenario: share}} with the shares adding up to exactly 1."""
    chances = {}
    for row in forecast["markets"]["scenario_chances"]:
        total = sum(row[s] for s in utilities.SCENARIOS) or 1
        chances[row["year"]] = {s: row[s] / total for s in utilities.SCENARIOS}
    return chances


def market_change(history: dict, outlook: dict, market_id: str, year: int, scenario: str,
                  level: str, shocks: dict | None = None) -> float:
    """A market's % change in a year: the recalled history, otherwise the scenario forecast."""
    past = history.get(market_id, {})
    if year in past:
        change = float(past[year] or 0.0)   # a year the AI couldn't find counts as no change
    elif year in outlook.get(market_id, {}):
        change = float(outlook[market_id][year][scenario][level])
    else:
        change = 0.0
    if shocks and (market_id, year) in shocks:
        change += shocks[(market_id, year)][level]
    return change


# ============================================================================
# 2. Savings from his jobs (from Call 2)
# ============================================================================

def pay_total(pay: dict, level: str) -> float:
    return pay["base_pay"][level] + pay["bonus"][level] + pay["equity_or_profit_share"][level]


def career_savings(forecast: dict, start: int, end: int) -> list[dict]:
    """Year by year: total pay, after tax, living costs, saved, growth and savings so far."""
    pay_by_year = {}
    for job in sorted(forecast["earnings"]["positions"], key=lambda p: p["position_index"]):
        for row in job["yearly"]:
            pay_by_year[row["year"]] = row

    savings = {b: 0.0 for b in BANDS}
    rows = []
    for year in range(start, end + 1):
        row = pay_by_year.get(year)
        if row is None:
            logger.warning("No pay forecast for %d, counting it as a year with no pay", year)
            rows.append({"year": year, "total_pay": zero_band(), "after_tax": zero_band(),
                         "living_costs": zero_band(), "saved": zero_band(), "savings": band(*savings.values())})
            continue
        parts = {"total_pay": {}, "after_tax": {}, "living_costs": {}, "saved": {}}
        for b in BANDS:
            gross = pay_total(row, MONEY_IN[b])
            after_tax = gross * (1 - row["tax_rate_pct"][MONEY_OUT[b]] / 100)
            costs = row["living_costs"][MONEY_OUT[b]]
            growth = savings[b] * row["savings_return_pct"][MONEY_IN[b]] / 100
            savings[b] += after_tax - costs + growth
            parts["total_pay"][b], parts["after_tax"][b] = gross, after_tax
            parts["living_costs"][b], parts["saved"][b] = costs, after_tax - costs
        rows.append({"year": year, **{key: band(*(values[b] for b in BANDS)) for key, values in parts.items()},
                     "savings": band(*(savings[b] for b in BANDS))})
    return rows


# ============================================================================
# 3. Assets (Call 2 behaviour + Call 1 markets)
# ============================================================================

def asset_path(asset: dict, model: dict, history: dict, outlook: dict, end_year: int,
               last_year: int, scenario: str, shocks: dict | None = None) -> dict:
    """Value and income of one asset every year from purchase to last_year."""
    bought = int(asset["year_acquired"])
    past_yields = {row["year"]: row["yield_pct"] for row in model["income_yield_by_year"]}
    future_yield = model["future_income_yield_pct"][scenario]

    results = {}
    for level in utilities.RANGE_KEYS:      # one path using all the low inputs, one typical, one high
        value = float(asset["price_paid"])
        values, incomes = {}, {}
        for year in range(bought, last_year + 1):
            if year > bought:
                change = sum(exposure["sensitivity"][level]
                             * market_change(history, outlook, exposure["market_id"], year, scenario, level, shocks)
                             for exposure in model["exposures"])
                value = max(value * (1 + (change + model["own_growth_pct"][level]) / 100), 0.0)
            yield_pct = past_yields.get(year, future_yield) if year <= end_year else future_yield
            values[year] = value
            incomes[year] = value * yield_pct[level] / 100
        results[level] = (values, incomes)

    years = range(bought, last_year + 1)
    return {"values": {y: sorted_band([results[level][0][y] for level in utilities.RANGE_KEYS]) for y in years},
            "income": {y: sorted_band([results[level][1][y] for level in utilities.RANGE_KEYS]) for y in years}}


def all_asset_paths(record: dict, forecast: dict, last_year: int, scenario: str,
                    shocks: dict | None = None) -> list[dict]:
    history, outlook = market_history(forecast), market_outlook(forecast)
    _, end = utilities.career_years(record)
    models = sorted(forecast["earnings"]["assets"], key=lambda a: a["asset_index"])
    return [asset_path(asset, model, history, outlook, end, last_year, scenario, shocks)
            for asset, model in zip(record.get("investments", []), models)]


def income_up_to(path: dict, year: int) -> dict:
    """Total income an asset produced up to and including a year."""
    total = zero_band()
    for income_year, income in path["income"].items():
        if income_year <= year:
            total = add_bands(total, income)
    return total


# ============================================================================
# 4. Wealth of every type, past years (back-test)
# ============================================================================

def extra_money_up_to(behaviour: dict, year: int) -> dict:
    """A type's extra money (Call 3) added up to and including a year."""
    total = zero_band()
    for row in behaviour["extra_money_history"]:
        if row["year"] <= year:
            total = add_bands(total, range_to_band(row["amount"]))
    return total


def honest_wealth(record: dict, savings_rows: list[dict], paths: list[dict], year: int) -> dict:
    """Wealth of the as-declared version: savings + gain on each asset + its income so far."""
    savings = next(row["savings"] for row in savings_rows if row["year"] == year)
    total = dict(savings)
    for asset, path in zip(record.get("investments", []), paths):
        if int(asset["year_acquired"]) > year:
            continue
        gain = band(*(path["values"][year][b] - asset["price_paid"] for b in BANDS))
        total = add_bands(total, add_bands(gain, income_up_to(path, year)))
    return total


def type_wealth_now(record: dict, forecast: dict, savings_rows: list[dict], paths: list[dict]) -> dict:
    """{type_id: wealth band today} = honest wealth + that type's extra money so far."""
    _, end = utilities.career_years(record)
    honest = honest_wealth(record, savings_rows, paths, end)
    return {type_id: add_bands(honest, extra_money_up_to(behaviour, end))
            for type_id, behaviour in forecast["types"]["behaviour"].items()}


# ============================================================================
# 5. Onboarding checks
# ============================================================================

def check_asset_values(record: dict, forecast: dict, paths: list[dict], end: int,
                       policy: dict, scale: float = 1.0) -> list[dict]:
    """Is each declared asset value within what its markets explain?"""
    tolerance = policy["tolerance"]["asset_value"]
    models = sorted(forecast["earnings"]["assets"], key=lambda a: a["asset_index"])
    findings = []
    for index, (asset, path, model) in enumerate(zip(record.get("investments", []), paths, models)):
        expected = scale_band(path["values"][end], scale)
        ceiling = expected["optimistic"] * (1 + tolerance)
        declared = asset["current_declared_value"]
        findings.append({
            "rule_id": "ASSET_VALUE", "asset_index": index, "category": asset_category(asset["asset_type"], policy),
            "failed": declared > ceiling, "declared": declared, "expected": expected, "ceiling": round(ceiling),
            "valuation_basis": model["valuation_basis"],
            "detail": f"Asset {index} ({asset['asset_type']}): declared {declared:,.0f}, "
                      f"markets explain about {expected['typical']:,.0f} (up to {ceiling:,.0f})"})
    return findings


def check_asset_income(record: dict, paths: list[dict], end: int, policy: dict,
                       scale: float = 1.0) -> list[dict]:
    """Is each asset's declared income within what an asset of that value normally pays?"""
    tolerance = policy["tolerance"]["asset_income"]
    findings = []
    for index, (asset, path) in enumerate(zip(record.get("investments", []), paths)):
        expected = scale_band(path["income"][end], scale)
        ceiling = expected["optimistic"] * (1 + tolerance)
        declared = asset.get("income_produced", 0)
        findings.append({
            "rule_id": "ASSET_INCOME", "asset_index": index, "category": asset_category(asset["asset_type"], policy),
            "failed": declared > ceiling, "declared": declared, "expected": expected, "ceiling": round(ceiling),
            "detail": f"Asset {index}: declared income {declared:,.0f}, expected about "
                      f"{expected['typical']:,.0f} (up to {ceiling:,.0f})"})
    return findings


def check_affordability(record: dict, savings_rows: list[dict], paths: list[dict], behaviour: dict,
                        policy: dict, scale: float = 1.0) -> list[dict]:
    """For one type: could he pay the cash part of each purchase from what he had that year?"""
    loan_share = policy["loan_to_value_max"]
    savings_by_year = {row["year"]: row["savings"] for row in savings_rows}
    assets = record.get("investments", [])
    order = sorted(range(len(assets)), key=lambda i: (int(assets[i]["year_acquired"]), i))

    rows, spent = [], 0.0
    for position, index in enumerate(order):
        asset = assets[index]
        year = int(asset["year_acquired"])
        method = asset.get("payment_method", "cash")
        needed = asset["price_paid"] * (1 - loan_share) if method == "loan" else asset["price_paid"]
        available = savings_by_year.get(year, zero_band())
        for earlier in order[:position]:
            available = add_bands(available, income_up_to(paths[earlier], year))
        available = add_bands(available, extra_money_up_to(behaviour, year))
        available = scale_band(band(*(available[b] - spent for b in BANDS)), scale)
        rows.append({"asset_index": index, "year": year, "payment_method": method, "needed": round(needed),
                     "available": available, "affordable": needed <= available["optimistic"]})
        spent += needed
    return rows


def check_jurisdictions(record: dict, forecast: dict, policy: dict) -> dict:
    """Countries he named with nothing behind them, and linked countries on a risk list."""
    behind = {same_country(job.get("country", ""), policy) for job in utilities.career_positions(record)}
    behind |= {same_country(asset.get("country", ""), policy) for asset in record.get("investments", [])}
    behind |= {same_country(source["country"], policy) for source in forecast["declaration"]["claimed_sources"]}
    named = {same_country(country, policy) for country in record.get("claims", {}).get("wealth_countries", [])}
    unexplained = sorted(named - behind)
    listed = sorted(j["country"] for j in forecast["markets"]["jurisdictions"]
                    if j["listings"] and same_country(j["country"], policy) in behind | named)
    return {"rule_id": "JURISDICTION", "failed": bool(unexplained or listed),
            "unexplained_countries": unexplained, "listed_countries": listed,
            "detail": f"Named countries with no job, asset or declared source: {unexplained or 'none'}. "
                      f"Linked countries on a risk list: {listed or 'none'}."}


def check_declared_sources(forecast: dict, policy: dict) -> list[dict]:
    """Declared sources with no amount and no job or asset behind them (e.g. 'some advisory work').
    Salary, bonuses and savings are explained by the career forecast, so they don't count."""
    explained_by_career = set(policy.get("self_explained_source_types", []))
    findings = []
    for source in forecast["declaration"]["claimed_sources"]:
        unquantified = (source["amount"] is None and source["linked_position_index"] is None
                        and source["linked_asset_index"] is None
                        and source["source_type"] not in explained_by_career)
        years = f"{source['start_year'] or '?'}-{source['end_year'] or '?'}"
        findings.append({
            "rule_id": "UNQUANTIFIED_SOURCE", "source_id": source["source_id"],
            "source_type": source["source_type"], "failed": unquantified,
            "detail": f"{source['source_id']} {source['description']} ({years}, {source['country']}): "
                      + ("no amount and no job or asset behind it" if unquantified else "backed"),
            "quote": source["quote"]})
    return findings


def fit_for_type(type_id: str, wealth_now: dict, affordability: list[dict], asset_checks: list[dict],
                 declared: float, policy: dict, scale: float = 1.0) -> dict:
    """How well his declared figures fit one type. Fit score 0-1."""
    tolerance = policy["tolerance"]["net_worth"]
    weights = policy["fit"]
    wealth = scale_band(wealth_now, scale)
    upper = wealth["optimistic"] * (1 + tolerance)
    lower = wealth["cautious"] * (1 - tolerance)
    # Anyone can have spent money, so the as-declared type has no lower limit. A dishonest
    # type that brought in extra money would leave him at least this rich.
    net_worth_fits = declared <= upper and (type_id == utilities.BASELINE_TYPE or declared >= lower)
    affordable = sum(1 for row in affordability if row["affordable"])
    affordable_share = affordable / len(affordability) if affordability else 1.0
    assets_share = (sum(1 for check in asset_checks if not check["failed"]) / len(asset_checks)
                    if asset_checks else 1.0)
    score = (weights["weight_net_worth"] * net_worth_fits + weights["weight_affordability"] * affordable_share
             + weights["weight_assets"] * assets_share)
    return {"type_id": type_id, "fits": net_worth_fits and score >= weights["minimum_fit"],
            "fit_score": round(score, 3), "net_worth_fits": net_worth_fits, "wealth_now": wealth,
            "purchases_affordable": f"{affordable} of {len(affordability)}",
            "fits_at_typical": declared <= wealth["typical"] * (1 + policy["tolerance"]["typical_band"])}


def decide_onboarding(fits: dict, t1_affordability_breach: bool, other_failures: list[str]) -> str:
    """The onboarding rule. fits = {type_id: fit result}."""
    t1 = fits[utilities.BASELINE_TYPE]
    others_fit = [t for t, result in fits.items() if t != utilities.BASELINE_TYPE and result["fits"]]
    if not t1["fits"] and others_fit:
        return SERIOUS_RISK          # only a dishonest version explains him
    if not t1["fits"]:
        return RISKY                 # no forecast explains him
    if not t1["fits_at_typical"] or t1_affordability_breach:
        return RISKY                 # honest only at the optimistic edge, or a purchase he couldn't afford
    if other_failures:
        return MANUAL_REVIEW
    return PASSED


def stable_outcome(outcome: str, outcomes_when_shifted: dict) -> tuple[str, bool]:
    """If moving the forecasts 10% changes the answer, the case is borderline and a person
    must look at it, so it goes to at least Manual Review."""
    borderline = len(set(outcomes_when_shifted.values())) > 1
    if borderline and SEVERITY[outcome] < SEVERITY[MANUAL_REVIEW]:
        outcome = MANUAL_REVIEW
    return outcome, borderline


def evaluate(record: dict, forecast: dict, savings_rows: list[dict], paths: list[dict],
             policy: dict, scale: float = 1.0) -> dict:
    """Run every onboarding check with all money forecasts multiplied by `scale`."""
    _, end = utilities.career_years(record)
    declared = record["claims"]["declared_net_worth"]
    behaviours = forecast["types"]["behaviour"]
    wealth_now = type_wealth_now(record, forecast, savings_rows, paths)

    # an asset the AI couldn't describe is held at its purchase price and not checked
    unmodelled = {a["asset_index"] for a in forecast["earnings"].get("fallback_assets", [])}
    asset_values = [c for c in check_asset_values(record, forecast, paths, end, policy, scale)
                    if c["asset_index"] not in unmodelled]
    asset_income = [c for c in check_asset_income(record, paths, end, policy, scale)
                    if c["asset_index"] not in unmodelled]
    asset_checks = asset_values + asset_income
    affordability = {t: check_affordability(record, savings_rows, paths, b, policy, scale)
                     for t, b in behaviours.items()}
    fits = {t: fit_for_type(t, wealth_now[t], affordability[t], asset_checks, declared, policy, scale)
            for t in behaviours}

    jurisdiction = check_jurisdictions(record, forecast, policy)
    sources = check_declared_sources(forecast, policy)
    t1_breach = not all(row["affordable"] for row in affordability[utilities.BASELINE_TYPE])
    other_failures = [c["rule_id"] for c in asset_checks + [jurisdiction] + sources if c["failed"]]
    outcome = decide_onboarding(fits, t1_breach, other_failures)
    return {"outcome": outcome, "fits": fits, "affordability": affordability,
            "asset_checks": asset_checks, "jurisdiction": jurisdiction, "sources": sources,
            "t1_affordability_breach": t1_breach}


# ============================================================================
# 6. The future: forward paths, events, divergence
# ============================================================================

def forward_paths(record: dict, forecast: dict, savings_rows: list[dict], scenario: str,
                  shocks: dict | None = None, event_changes: dict | None = None) -> dict:
    """
    Wealth of every type for the current year and each forecast year in one scenario.
    event_changes = {type_id: (year, extra money change)} adds an event reaction.
    """
    _, end = utilities.career_years(record)
    years = utilities.future_years(record)
    paths = all_asset_paths(record, forecast, years[-1], scenario, shocks)
    future_pay = {row["year"]: row for row in forecast["earnings"]["future_pay"]["yearly"]}

    honest_now = honest_wealth(record, savings_rows, paths, end)
    cash = {b: honest_now[b] - sum(p["values"][end][b] for p in paths) for b in BANDS}
    honest = {end: honest_now}
    for year in years:
        row = future_pay[year]
        for b in BANDS:
            income = pay_total(row[scenario], MONEY_IN[b]) + sum(p["income"][year][b] for p in paths)
            after_tax = income * (1 - row["tax_rate_pct"][MONEY_OUT[b]] / 100)
            growth = cash[b] * row["savings_return_pct"][MONEY_IN[b]] / 100
            cash[b] += after_tax - row["living_costs"][MONEY_OUT[b]] + growth
        honest[year] = band(*(cash[b] + sum(p["values"][year][b] for p in paths) for b in BANDS))

    result = {}
    for type_id, behaviour in forecast["types"]["behaviour"].items():
        extra = extra_money_up_to(behaviour, end)
        forecast_rows = {row["year"]: row for row in behaviour["extra_money_forecast"]}
        points = {end: add_bands(honest[end], extra)}
        for year in years:
            extra = add_bands(extra, range_to_band(forecast_rows[year][scenario]))
            if event_changes and type_id in event_changes and event_changes[type_id][0] == year:
                extra = add_bands(extra, range_to_band(event_changes[type_id][1]))
            points[year] = add_bands(honest[year], extra)
        result[type_id] = points
    return result


def expected_paths(record: dict, forecast: dict, savings_rows: list[dict]) -> dict:
    """Each type's wealth path weighted by the AI's chance of each scenario."""
    chances = scenario_chances(forecast)
    by_scenario = {s: forward_paths(record, forecast, savings_rows, s) for s in utilities.SCENARIOS}
    expected = {}
    for type_id in forecast["types"]["behaviour"]:
        expected[type_id] = {}
        for year in by_scenario["steady"][type_id]:
            weights = chances.get(year, {"steady": 1.0, "boom": 0.0, "downturn": 0.0})
            expected[type_id][year] = band(*(sum(weights[s] * by_scenario[s][type_id][year][b]
                                                 for s in utilities.SCENARIOS) for b in BANDS))
    return expected


def event_impacts(record: dict, forecast: dict, savings_rows: list[dict]) -> list[dict]:
    """For each event and type: change in 2030 wealth if it happens, and weighted by its chance."""
    last_year = utilities.future_years(record)[-1]
    without = forward_paths(record, forecast, savings_rows, "steady")
    rows = []
    for event in forecast["events"]["events"]:
        shocks = {(s["market_id"], event["year"]): s["change_pct"] for s in event["shocks"]}
        changes = {}
        for type_id, reactions in forecast["events"]["reactions"].items():
            for reaction in reactions["reactions"]:
                if reaction["event_id"] == event["event_id"]:
                    changes[type_id] = (event["year"], reaction["extra_money_change"])
        with_event = forward_paths(record, forecast, savings_rows, "steady", shocks, changes)
        chance = event["probability_pct"]["typical"] / 100
        for type_id in with_event:
            impact = with_event[type_id][last_year]["typical"] - without[type_id][last_year]["typical"]
            rows.append({"event_id": event["event_id"], "event": event["name"], "year": event["year"],
                         "type_id": type_id, "impact": round(impact), "chance": round(chance, 3),
                         "weighted_impact": round(impact * chance)})
    return rows


def divergence_years(paths: dict) -> list[dict]:
    """First forecast year each type's range stops overlapping the as-declared type's range."""
    baseline = paths[utilities.BASELINE_TYPE]
    rows = []
    for type_id, points in paths.items():
        if type_id == utilities.BASELINE_TYPE:
            continue
        first = None
        for year in sorted(points)[1:]:
            a, b = baseline[year], points[year]
            if a["cautious"] > b["optimistic"] or b["cautious"] > a["optimistic"]:
                first = year
                break
        rows.append({"type_id": type_id, "distinguishable_from": first})
    return rows


def monitoring_ranges(forecast: dict, year: int, policy: dict) -> dict:
    """What normal account activity looks like next year, from the as-declared type's signals."""
    tolerance = policy["tolerance"]["signal_amount"]
    ranges = {}
    for signal in forecast["types"]["behaviour"][utilities.BASELINE_TYPE]["signals"]:
        if not signal["first_year"] <= year <= signal["last_year"]:
            continue
        code = signal["signal_code"]
        count_max = round(signal["count_per_year"]["high"] * (1 + tolerance), 2)
        amount_max = round(signal["amount_per_year"]["high"] * (1 + tolerance))
        if code in ranges:
            count_max = max(count_max, ranges[code]["count_max"])
            amount_max = max(amount_max, ranges[code]["amount_max"])
        ranges[code] = {"count_max": count_max, "amount_max": amount_max}
    return ranges


def rank_documents(forecast: dict, candidates: list[str]) -> list[dict]:
    """Documents ordered by how many candidate types each one would rule out."""
    pool = set(candidates)
    covers: dict[str, set] = {}
    for person_type in forecast["types"]["person_types"]:
        if person_type["type_id"] not in pool:
            continue
        for document in person_type["rule_out_documents"]:
            ruled_out = ({person_type["type_id"]} | set(document["also_rules_out"])) & pool
            covers.setdefault(document["document_code"], set()).update(ruled_out)
    ranked = sorted(covers.items(), key=lambda item: (-len(item[1]), item[0]))
    return [{"document_code": code, "rules_out": sorted(types)} for code, types in ranked]


def document_request(failures: list[dict], forecast: dict, candidates: list[str], policy: dict) -> list[dict]:
    """Documents for failed checks first, then the ones that rule out the most types."""
    names = utilities.vocabulary().get("document_codes", {})
    lookup = policy.get("breach_documents", {})
    request, seen = [], set()

    def add(code: str, reason: str) -> None:
        if code in names and code not in seen:
            seen.add(code)
            request.append({"document_code": code, "document": names[code], "reason": reason})

    for failure in failures:
        key = failure["rule_id"]
        if key in ("ASSET_VALUE", "ASSET_INCOME"):
            key = f"{key}.{failure['category']}"
        elif key == "AFFORDABILITY":
            key = f"AFFORDABILITY.{failure['payment_method']}"
        elif key == "UNQUANTIFIED_SOURCE":
            key = f"UNQUANTIFIED_SOURCE.{failure['source_type']}"
        for code in lookup.get(key, []):
            add(code, failure["detail"])
    if len(candidates) > 1:
        for document in rank_documents(forecast, candidates):
            add(document["document_code"], f"rules out {', '.join(document['rules_out'])}")
    return request[:policy["max_documents"]]


def data_quality(forecast: dict) -> dict:
    """How many market figures the AI could only give from memory or not at all."""
    figures = [row for series in forecast["markets"]["series"].values() for row in series["history"]]
    unverified = [row for row in figures
                  if row["change_pct"] is None or "unverified" in row["source_name"].lower()]
    gaps = list(forecast["markets"]["data_gaps"])
    for series in forecast["markets"]["series"].values():
        gaps += series["data_gaps"]
    return {"market_figures": len(figures),
            "unverified_share": round(len(unverified) / len(figures), 3) if figures else 0.0,
            "data_gaps": sorted(set(gaps))}


# ============================================================================
# Onboarding: the full assessment
# ============================================================================

def assess_onboarding(record: dict, forecast: dict, policy: dict | None = None) -> dict:
    """Measure the client against the forecasts and decide. No AI involved."""
    policy = policy or utilities.policy()
    start, end = utilities.career_years(record)
    years = utilities.future_years(record)
    savings_rows = career_savings(forecast, start, end)
    paths = all_asset_paths(record, forecast, end, "steady")

    # The decision, plus the stability test with every forecast 10% lower and higher
    shift = policy["stability_shift"]
    main = evaluate(record, forecast, savings_rows, paths, policy)
    stability = {f"{-shift:+.0%}": evaluate(record, forecast, savings_rows, paths, policy, 1 - shift)["outcome"],
                 "+0%": main["outcome"],
                 f"{shift:+.0%}": evaluate(record, forecast, savings_rows, paths, policy, 1 + shift)["outcome"]}
    outcome, borderline = stable_outcome(main["outcome"], stability)

    # Type table and starting scores (every type starts equal; only the fit separates them)
    names = {t["type_id"]: t for t in forecast["types"]["person_types"]}
    total_fit = sum(f["fit_score"] for f in main["fits"].values()) or 1.0
    type_table = []
    for type_id, fit in main["fits"].items():
        behaviour = forecast["types"]["behaviour"][type_id]
        person_type = names[type_id]
        type_table.append({**fit, "category": person_type["category"],
                           "category_name": category_name(person_type["category"]),
                           "name": person_type["name"], "route": person_type["route"],
                           "enabled_by": enabled_by_text(person_type, record),
                           "why_it_applies": person_type.get("why_it_applies", ""),
                           "route_years": f"{behaviour['route_open_from']}-{behaviour['route_open_to']}",
                           "score": round(fit["fit_score"] / total_fit, 3)})

    # Reasons in plain words
    t1 = main["fits"][utilities.BASELINE_TYPE]
    declared = record["claims"]["declared_net_worth"]
    others_fit = [t for t, f in main["fits"].items() if t != utilities.BASELINE_TYPE and f["fits"]]
    reasons = [f"Declared net worth {declared:,.0f}; the honest version explains "
               f"{t1['wealth_now']['cautious']:,.0f} to {t1['wealth_now']['optimistic']:,.0f}."]
    if not t1["fits"]:
        reasons.append("The honest version does not fit his figures.")
    if others_fit:
        reasons.append("Crime types that do fit: " + "; ".join(
            f"{t} {category_name(names[t]['category'])} via {enabled_by_text(names[t], record)} "
            f"({forecast['types']['behaviour'][t]['route_open_from']}-"
            f"{forecast['types']['behaviour'][t]['route_open_to']})" for t in others_fit) + ".")
    for row in main["affordability"][utilities.BASELINE_TYPE]:
        if not row["affordable"]:
            reasons.append(f"{row['year']}: he needed {row['needed']:,.0f} in cash but the honest "
                           f"version had at most {row['available']['optimistic']:,.0f}.")
    failures = [c for c in main["asset_checks"] + [main["jurisdiction"]] + main["sources"] if c["failed"]]
    reasons += [c["detail"] for c in failures]
    if borderline:
        reasons.append(f"Borderline: the outcome changes when the forecasts move 10% ({stability}).")

    # Documents: for the failed checks, then to tell the fitting types apart
    affordability_failures = [{"rule_id": "AFFORDABILITY", "payment_method": row["payment_method"],
                               "detail": f"{row['year']} purchase of asset {row['asset_index']}"}
                              for row in main["affordability"][utilities.BASELINE_TYPE] if not row["affordable"]]
    if not t1["fits"]:
        affordability_failures.insert(0, {"rule_id": "NET_WORTH", "detail": reasons[0]})
    candidates = [utilities.BASELINE_TYPE] + others_fit
    if len(candidates) == 1 and outcome != PASSED:
        candidates = [t for t, b in forecast["types"]["behaviour"].items() if b["route_open_to"] >= end]
    documents = []
    if outcome != PASSED:
        documents = document_request(affordability_failures + failures, forecast, candidates, policy)

    # The future
    expected = expected_paths(record, forecast, savings_rows)
    divergence = divergence_years(expected)
    watch_dates = sorted(
        [{"date": f"{d['distinguishable_from']}-01-01", "what": f"{utilities.BASELINE_TYPE} and {d['type_id']} "
          "can be told apart"} for d in divergence if d["distinguishable_from"]]
        + [{"date": f"{e['year']}-01-01", "what": f"{e['event_id']} {e['name']} could happen"}
           for e in forecast["events"]["events"]],
        key=lambda w: (w["date"], w["what"]))
    scheduled = add_months(record["current_date"], policy["review_interval_months"][outcome])
    first_divergence = [w["date"] for w in watch_dates if "told apart" in w["what"] and w["date"] > record["current_date"]]

    return {
        "case_ref": record["client_ref"],
        "assessed_on": record["current_date"],
        "policy_version": policy["policy_version"],
        "prompt_versions": forecast.get("prompt_versions", {}),
        "outcome": outcome,
        "routing": policy["routing"][outcome],
        "reasons": reasons,
        "borderline": borderline,
        "stability": stability,
        "declared_net_worth": declared,
        "types": type_table,
        "affordability": main["affordability"][utilities.BASELINE_TYPE],
        "asset_checks": main["asset_checks"],
        "jurisdiction": main["jurisdiction"],
        "declared_sources": main["sources"],
        "country_notes": [{"country": j["country"], "listings": j["listings"], "summary": j["summary"],
                           "source_name": j["source_name"]} for j in forecast["markets"]["jurisdictions"]],
        "markets_used": [{"market_id": m["market_id"], "name": m["name"], "country": m["country"],
                          "indicator": m["indicator"]} for m in forecast["markets"]["relevant_markets"]],
        "career_savings": savings_rows,
        "career_outlook": forecast["earnings"]["future_pay"]["career_outlook"],
        "forward_paths": {t: [{"year": y, **v} for y, v in points.items()] for t, points in expected.items()},
        "event_impacts": event_impacts(record, forecast, savings_rows),
        "divergence": divergence,
        "monitoring_ranges": monitoring_ranges(forecast, years[0], policy),
        "documents": documents,
        "watch_dates": watch_dates,
        "next_review_date": min([scheduled] + first_divergence),
        "data_quality": data_quality(forecast),
    }


# ============================================================================
# Periodic review
# ============================================================================

def detect_scenario(market_moves: list[dict], forecast: dict, year: int) -> str:
    """The scenario whose forecast is closest to what the markets actually did."""
    outlook = market_outlook(forecast)
    chances = scenario_chances(forecast).get(year, {s: 1 / 3 for s in utilities.SCENARIOS})
    distance = {}
    for scenario in utilities.SCENARIOS:
        gaps = [abs(move["change_pct"] - outlook[move["market_id"]][year][scenario]["typical"])
                for move in market_moves
                if move["change_pct"] is not None and year in outlook.get(move["market_id"], {})]
        distance[scenario] = sum(gaps) / len(gaps) if gaps else None
    known = [s for s in utilities.SCENARIOS if distance[s] is not None]
    if not known:
        return max(utilities.SCENARIOS, key=lambda s: chances[s])
    # closest first; a tie goes to the scenario the AI thought more likely
    return min(known, key=lambda s: (distance[s], -chances[s]))


def payments_in_sgd(inflows: list[dict]) -> list[dict]:
    """Each payment converted to SGD: amount x exchange rate x times received."""
    rows = []
    for inflow in inflows:
        total = None
        if inflow["amount"] is not None and inflow["fx_rate_to_sgd"] is not None:
            total = round(inflow["amount"] * inflow["fx_rate_to_sgd"] * inflow["times_received"])
        rows.append({"signal_code": inflow["signal_code"], "amount_sgd": total, "currency": inflow["currency"],
                     "times_received": inflow["times_received"],
                     "counterparty_country": inflow["counterparty_country"], "quote": inflow["quote"]})
    return rows


def explains_payment(behaviour: dict, payment: dict, year: int, tolerance: float) -> bool:
    """Did this type forecast a payment like this, this year, of up to this size?"""
    for signal in behaviour["signals"]:
        if signal["signal_code"] != payment["signal_code"]:
            continue
        if not signal["first_year"] <= year <= signal["last_year"]:
            continue
        if payment["amount_sgd"] is None or payment["amount_sgd"] <= signal["amount_per_year"]["high"] * (1 + tolerance):
            return True
    return False


def income_against_market(record: dict, forecast: dict, payments: list[dict], market_moves: list[dict],
                          scenario: str, year: int, policy: dict) -> list[dict]:
    """Asset income above its forecast while a market the asset follows fell."""
    fell = {m["market_id"] for m in market_moves if m["change_pct"] is not None and m["change_pct"] < 0}
    paths = all_asset_paths(record, forecast, year, scenario)
    models = sorted(forecast["earnings"]["assets"], key=lambda a: a["asset_index"])
    tolerance = policy["tolerance"]["asset_income"]
    by_category: dict[str, dict] = {}
    for asset, model, path in zip(record.get("investments", []), models, paths):
        category = asset_category(asset["asset_type"], policy)
        entry = by_category.setdefault(category, {"ceiling": 0.0, "markets_fell": set()})
        entry["ceiling"] += path["income"][year]["optimistic"] * (1 + tolerance)
        entry["markets_fell"] |= {e["market_id"] for e in model["exposures"]
                                  if e["market_id"] in fell and e["sensitivity"]["typical"] > 0}
    hits = []
    for category, entry in by_category.items():
        code = policy["income_signal_by_category"].get(category)
        received = sum(p["amount_sgd"] or 0 for p in payments if p["signal_code"] == code)
        if received > entry["ceiling"] and entry["markets_fell"]:
            hits.append({"signal_code": code, "received": received, "ceiling": round(entry["ceiling"]),
                         "markets_fell": sorted(entry["markets_fell"])})
    return hits


def review_fit(type_id: str, forecast: dict, growth_range: dict, actual_growth: float,
               payments: list[dict], counter_hits: list[dict], market_moves: list[dict],
               events_seen: list[str], year: int, policy: dict) -> dict:
    """How well this review's evidence fits one type. Each part is 1 if it fits, lower if not."""
    behaviour = forecast["types"]["behaviour"][type_id]
    miss = policy["fit"]["miss_weight"]
    tolerance = policy["tolerance"]

    low, high = growth_range["cautious"], growth_range["optimistic"]
    wealth_fits = (low - abs(low) * tolerance["review_growth"] <= actual_growth
                   <= high + abs(high) * tolerance["review_growth"])

    explained = [p for p in payments if explains_payment(behaviour, p, year, tolerance["signal_amount"])]
    payment_share = len(explained) / len(payments) if payments else 1.0

    # Income rising while its market fell: does this type forecast money that does that?
    fell = {m["market_id"] for m in market_moves if m["change_pct"] is not None and m["change_pct"] < 0}
    rises_when_markets_fall = (not set(behaviour["linked_market_ids"]) & fell
                               or behaviour["market_fall_response"]["typical"] >= 0)
    market_fits = all(explains_payment(behaviour, {"signal_code": hit["signal_code"], "amount_sgd": hit["received"]},
                                       year, tolerance["signal_amount"]) and rises_when_markets_fall
                      for hit in counter_hits)

    observed = {p["signal_code"] for p in payments}
    shares = []
    for reaction in forecast["events"]["reactions"][type_id]["reactions"]:
        if reaction["event_id"] in events_seen and reaction["signals"]:
            shares.append(len(observed & set(reaction["signals"])) / len(reaction["signals"]))
    event_share = sum(shares) / len(shares) if shares else 1.0

    parts = {"wealth": 1.0 if wealth_fits else miss,
             "payments": max(payment_share, miss),
             "market": 1.0 if market_fits else miss,
             "events": max(event_share, miss)}
    product = 1.0
    for value in parts.values():
        product *= value
    return {"type_id": type_id, "parts": {k: round(v, 3) for k, v in parts.items()}, "fit": round(product, 4)}


def decide_review(a: bool, b: bool, c: bool, d: bool) -> str:
    """The review rule. A: wealth outside the honest range. B: another type now scores higher.
    C: income rose while its market fell. D: a payment outside the normal ranges."""
    count = sum([a, b, c])
    if count == 3:
        return SERIOUS_RISK
    if count == 2:
        return RISKY
    if count == 1 or d:
        return MANUAL_REVIEW
    return PASSED


def assess_review(record: dict, review: dict, review_data: dict, forecast: dict,
                  onboarding: dict, policy: dict | None = None) -> dict:
    """Compare what actually happened with each type's forecast and decide."""
    policy = policy or utilities.policy()
    start, onboarding_year = utilities.career_years(record)
    year = utilities.current_year(review)
    market_moves = review_data["market_moves"]
    scenario = detect_scenario(market_moves, forecast, year)

    # Growth since onboarding vs each type's forecast for the scenario that happened
    savings_rows = career_savings(forecast, start, onboarding_year)
    paths = forward_paths(record, forecast, savings_rows, scenario)
    actual_growth = review["current_wealth"] - record["claims"]["declared_net_worth"]
    growth = {t: band(*(p[year][b] - p[onboarding_year][b] for b in BANDS)) for t, p in paths.items()}

    payments = payments_in_sgd(review_data["inflows"])
    counter_hits = income_against_market(record, forecast, payments, market_moves, scenario, year, policy)

    # Payments outside the normal ranges, or from a country with no link to him
    ranges = onboarding["monitoring_ranges"]
    linked = {same_country(job.get("country", ""), policy) for job in utilities.career_positions(record)}
    linked |= {same_country(a.get("country", ""), policy) for a in record.get("investments", [])}
    linked |= {same_country(c, policy) for c in record["claims"]["wealth_countries"]}
    unusual = []
    for payment in payments:
        normal = ranges.get(payment["signal_code"])
        if normal is None:
            unusual.append(f"{payment['signal_code']}: not expected for the honest version")
        elif payment["amount_sgd"] is not None and payment["amount_sgd"] > normal["amount_max"]:
            unusual.append(f"{payment['signal_code']}: {payment['amount_sgd']:,.0f} is above {normal['amount_max']:,.0f}")
        elif payment["times_received"] > normal["count_max"]:
            unusual.append(f"{payment['signal_code']}: {payment['times_received']} payments, normal is up to {normal['count_max']}")
        if same_country(payment["counterparty_country"], policy) not in linked:
            unusual.append(f"{payment['signal_code']}: from {payment['counterparty_country']}, a country with no link to him")

    # Running score per type: previous score x this review's fit, rescaled to add up to 1
    fits = {t: review_fit(t, forecast, growth[t], actual_growth, payments, counter_hits, market_moves,
                          review_data["events_seen"], year, policy) for t in paths}
    previous = {row["type_id"]: row["score"] for row in onboarding["types"]}
    raw_scores = {t: previous.get(t, 0.0) * fits[t]["fit"] for t in fits}
    total = sum(raw_scores.values())
    scores = {t: round(v / total, 3) if total else round(1 / len(raw_scores), 3) for t, v in raw_scores.items()}

    t1_growth = growth[utilities.BASELINE_TYPE]
    tolerance = policy["tolerance"]["review_growth"]
    a = not (t1_growth["cautious"] - abs(t1_growth["cautious"]) * tolerance <= actual_growth
             <= t1_growth["optimistic"] + abs(t1_growth["optimistic"]) * tolerance)
    leader = max(scores, key=lambda t: (scores[t], t == utilities.BASELINE_TYPE))
    b = leader != utilities.BASELINE_TYPE and scores[leader] > scores[utilities.BASELINE_TYPE]
    c = bool(counter_hits)
    d = bool(unusual)
    outcome = decide_review(a, b, c, d)

    findings = [
        {"rule_id": "A_GROWTH", "failed": a,
         "detail": f"Wealth grew {actual_growth:,.0f} since onboarding; the honest version ({scenario} year) "
                   f"grows {t1_growth['cautious']:,.0f} to {t1_growth['optimistic']:,.0f}."},
        {"rule_id": "B_OTHER_TYPE_LEADS", "failed": b,
         "detail": f"Highest running score: {leader} ({scores[leader]:.0%}); honest: "
                   f"{scores[utilities.BASELINE_TYPE]:.0%}."},
        {"rule_id": "C_INCOME_AGAINST_MARKET", "failed": c,
         "detail": "; ".join(f"{h['signal_code']} {h['received']:,.0f} (forecast up to {h['ceiling']:,.0f}) "
                             f"while {', '.join(h['markets_fell'])} fell" for h in counter_hits) or "none"},
        {"rule_id": "D_UNUSUAL_PAYMENTS", "failed": d, "detail": "; ".join(unusual) or "none"},
    ]
    candidates = [t for t in scores if scores[t] >= scores[utilities.BASELINE_TYPE]]
    documents = []
    if outcome != PASSED and len(candidates) > 1:
        names = utilities.vocabulary().get("document_codes", {})
        for document in rank_documents(forecast, candidates)[:policy["max_documents"]]:
            documents.append({"document_code": document["document_code"],
                              "document": names.get(document["document_code"], ""),
                              "reason": f"rules out {', '.join(document['rules_out'])}"})

    return {
        "case_ref": record["client_ref"],
        "review_date": review["current_date"],
        "policy_version": policy["policy_version"],
        "outcome": outcome,
        "routing": policy["routing"][outcome],
        "scenario": scenario,
        "findings": findings,
        "payments": payments,
        "type_scores": [{"type_id": t, "previous": previous.get(t, 0.0), "review_fit": fits[t]["fit"],
                         "parts": fits[t]["parts"], "score": scores[t]} for t in scores],
        "documents": documents,
        "next_review_date": add_months(review["current_date"], policy["review_interval_months"][outcome]),
    }

# ============================================================================
# 4. New client from documents (CLI) - turning the AI's reading of the client's
#    documents into a client record, and finding what's still missing.
#    Pure functions: no printing, no files, no AI.
# ============================================================================

COMPOSITION_KEYS = ["property", "listed_equities", "private_business", "cash"]
PEP_STATUSES = ["None", "Domestic PEP", "Foreign PEP", "International organisation PEP",
                "Family member / close associate of a PEP"]


def _age_from_birth(date_of_birth, today: str) -> int | None:
    """Whole years between date_of_birth and today, or None if either can't be read."""
    try:
        born = datetime.date.fromisoformat(str(date_of_birth))
        day = datetime.date.fromisoformat(today)
    except (TypeError, ValueError):
        return None
    age = day.year - born.year - ((day.month, day.day) < (born.month, born.day))
    return age if 0 < age < 130 else None


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def record_from_documents(extracted: dict, client_ref: str, today: str) -> tuple[dict, list[str]]:
    """Build a client record (the same layout the New Client form saves) from the AI's
    reading of the documents. Returns (record, paths of the fields that came from the documents).

    Every job, including the current one (end_year None), goes into career_history;
    finish_document_record then copies the current job into "career".
    """
    client = extracted.get("client") or {}
    claims = extracted.get("claims") or {}
    composition = extracted.get("composition") or {}

    jobs = [{key: job.get(key) for key in ("start_year", "end_year", "occupation", "seniority", "industry",
                                           "employer_name", "employer_size", "country")}
            for job in extracted.get("jobs") or []]
    # oldest first, current job (no end year) last - the order the forecasting checks
    jobs.sort(key=lambda job: (job["start_year"] if isinstance(job["start_year"], int) else 9999,
                               job["end_year"] if isinstance(job["end_year"], int) else 9999))
    if not jobs:   # always at least one (current) job, so its details get asked for
        jobs = [{"start_year": None, "end_year": None, "occupation": None, "seniority": None,
                 "industry": None, "employer_name": None, "employer_size": None,
                 "country": client.get("country_of_residence")}]

    investments = []
    for asset in extracted.get("assets") or []:
        investments.append({
            "asset_type": asset.get("asset_type"),
            "country": asset.get("country"), "year_acquired": asset.get("year_acquired"),
            "price_paid": asset.get("price_paid_sgd"),
            "current_declared_value": asset.get("current_declared_value_sgd"),
            "payment_method": asset.get("payment_method"),
            "income_produced": asset.get("income_produced_sgd"),   # the AI gives every amount in SGD
        })

    split = {key: composition.get(key) for key in COMPOSITION_KEYS}
    if all(isinstance(v, (int, float)) for v in split.values()):
        split = {key: value / 100 for key, value in split.items()}    # AI gives percentages

    age = client.get("age") or _age_from_birth(client.get("date_of_birth"), today)
    record = {
        "client_ref": client_ref,
        "name": client.get("name"),
        "nationality": client.get("nationality"),
        "current_date": today,
        "career": {"country_of_residence": client.get("country_of_residence"), "age": age},
        "career_history": jobs,
        "investments": investments,
        "claims": {
            "declared_net_worth": claims.get("declared_net_worth_sgd"),
            "expected_aum": claims.get("expected_aum_sgd"),
            "pep_status": claims.get("pep_status"),
            "wealth_countries": list(claims.get("wealth_countries") or []),
            "asset_composition": split,
            "biggest_wealth_jump": claims.get("biggest_wealth_jump_sgd"),
            "wealth_jump_years": claims.get("wealth_jump_years"),
            "declared_counterparties": list(claims.get("declared_counterparties") or []),
        },
        "sow_declaration": extracted.get("sow_declaration"),
        "reviews": [],
        "supporting_documents": [],
    }
    found = [".".join(str(part) for part in item["path"])
             for item in _required_fields(record) if not _missing(record, item)]
    return record, found


def get_path(record: dict, path: list):
    """The value at path (e.g. ["career_history", 0, "start_year"]), or None if it isn't there."""
    value = record
    for part in path:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
            value = value[part]
        else:
            return None
    return value


def set_path(record: dict, path: list, value) -> dict:
    """Return a copy of record with the value at path replaced."""
    new = copy.deepcopy(record)
    target = new
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return new


def _field(path: list, short: str, kind: str, group: str = "", label: str = "") -> dict:
    """One form field. label is the full question (when asking); short + group are for the summary."""
    return {"path": path, "short": short, "kind": kind, "group": group,
            "label": label or (f"{group}: {short}" if group else short)}


def _job_group(i: int, job: dict) -> str:
    years = f"{job.get('start_year') or '?'}-{job.get('end_year') or 'present'}"
    return f"Job {i + 1}: {job.get('occupation') or 'job'} at {job.get('employer_name') or 'unknown employer'} ({years})"


def _asset_group(i: int, asset: dict) -> str:
    return f"Asset {i + 1}: {asset.get('asset_type') or 'asset'}"


def _required_fields(record: dict) -> list[dict]:
    """Every field the New Client form insists on, with how it should be asked for."""
    fields = [
        _field(["name"], "Full name", "words"),
        _field(["nationality"], "Nationality", "words"),
        _field(["career", "country_of_residence"], "Country of residence", "words"),
        _field(["career", "age"], "Age", "age"),
    ]
    for i, job in enumerate(record.get("career_history", [])):
        group = _job_group(i, job)
        fields += [
            _field(["career_history", i, "start_year"], "start year", "year", group),
            _field(["career_history", i, "occupation"], "job title", "label", group),
            _field(["career_history", i, "seniority"], "seniority", "label", group),
            _field(["career_history", i, "industry"], "industry", "label", group),
            _field(["career_history", i, "employer_name"], "employer", "label", group),
            _field(["career_history", i, "employer_size"], "employer size", "text", group,
                   f"{group}: employer size (e.g. 1,000-5,000 employees)"),
            _field(["career_history", i, "country"], "country", "words", group),
        ]
    for i, asset in enumerate(record.get("investments", [])):
        group = _asset_group(i, asset)
        fields += [
            _field(["investments", i, "country"], "country", "words", group),
            _field(["investments", i, "year_acquired"], "year acquired", "year", group),
            _field(["investments", i, "price_paid"], "price paid (SGD)", "money_positive", group),
            _field(["investments", i, "payment_method"], "how it was paid for", "payment", group),
            _field(["investments", i, "current_declared_value"], "current value (SGD)", "money", group),
            _field(["investments", i, "income_produced"], "yearly income (SGD)", "money", group,
                   f"{group}: yearly income (SGD, 0 if none)"),
        ]
    fields += [
        _field(["claims", "declared_net_worth"], "Declared net worth (SGD)", "money_positive"),
        _field(["claims", "expected_aum"], "Expected assets to be managed by the bank (SGD)", "money_positive"),
        _field(["claims", "pep_status"], "PEP status", "pep"),
        _field(["claims", "wealth_countries"], "Countries where wealth was generated", "countries",
               label="Countries where wealth was generated (comma-separated)"),
        _field(["claims", "asset_composition"], "Asset composition", "composition",
               label="Asset composition (must add up to 100%)"),
        _field(["sow_declaration"], "Source of wealth declaration", "sow"),
    ]
    return fields


def _missing(record: dict, field: dict) -> bool:
    value = get_path(record, field["path"])
    kind = field["kind"]
    if kind == "composition":
        split = value or {}
        values = [split.get(key) for key in COMPOSITION_KEYS]
        return any(not isinstance(v, (int, float)) for v in values) or abs(sum(values) - 1.0) > 0.005
    if kind == "sow":
        return _blank(value) or len(str(value).strip()) < 50
    if kind == "payment":
        return value not in ("cash", "loan", "asset_sale")
    if kind == "pep":
        return _blank(value)
    return _blank(value)


def missing_client_fields(record: dict) -> list[dict]:
    """The required fields still empty (or unusable) after reading the documents, in form order."""
    return [field for field in _required_fields(record) if _missing(record, field)]


def finish_document_record(record: dict) -> dict:
    """Copy the current job (the one with no end year) into "career", and the first job's start year
    into career_start_year - the same layout the New Client form saves."""
    record = copy.deepcopy(record)
    jobs = record.get("career_history", [])     # kept in document order so the summary lines up
    current = next((job for job in reversed(jobs) if job.get("end_year") in (None, "")), jobs[-1] if jobs else {})
    career = record.setdefault("career", {})
    for key in ("occupation", "seniority", "industry", "employer_name", "employer_size"):
        career[key] = current.get(key)
    starts = [job.get("start_year") for job in jobs if isinstance(job.get("start_year"), int)]
    career["career_start_year"] = min(starts) if starts else None
    return record


def check_document_record(record: dict, today: str) -> list[str]:
    """Things worth a second look in a record filled from documents. Empty list = nothing spotted."""
    warnings = []
    this_year = int(today[:4])
    age = record.get("career", {}).get("age")
    start = record.get("career", {}).get("career_start_year")
    if isinstance(age, int) and isinstance(start, int) and start < this_year - age + 14:
        warnings.append(f"Career start year {start} means the client started work before age 14.")
    for i, job in enumerate(record.get("career_history", []), start=1):
        s, e = job.get("start_year"), job.get("end_year")
        if isinstance(s, int) and isinstance(e, int) and e < s:
            warnings.append(f"Job {i} ends ({e}) before it starts ({s}).")
        if isinstance(s, int) and s > this_year:
            warnings.append(f"Job {i} starts in the future ({s}).")
    open_jobs = [job for job in record.get("career_history", []) if job.get("end_year") in (None, "")]
    if len(open_jobs) > 1:
        warnings.append(f"{len(open_jobs)} jobs have no end year; only one should be the current job.")
    for i, asset in enumerate(record.get("investments", []), start=1):
        year = asset.get("year_acquired")
        if isinstance(year, int) and isinstance(start, int) and year < start:
            warnings.append(f"Asset {i} was bought in {year}, before the client's career started ({start}).")
    claims = record.get("claims", {})
    worth, aum = claims.get("declared_net_worth"), claims.get("expected_aum")
    if isinstance(worth, (int, float)) and isinstance(aum, (int, float)) and aum > worth:
        warnings.append("Expected assets with the bank are more than the declared net worth.")
    held = sum(a.get("current_declared_value") or 0 for a in record.get("investments", [])
               if isinstance(a.get("current_declared_value"), (int, float)))
    if isinstance(worth, (int, float)) and held > worth * 1.2:
        warnings.append(f"The listed assets are worth {held:,.0f}, well above the declared net worth {worth:,.0f}.")
    warnings += _split_against_assets(record)
    return warnings


# asset_type starts with the kind of asset ("Property - Condo"), as the form, the CLI and the documents auto-fill write it
_ASSET_KIND_PREFIX = {"property": "property", "listed equities": "listed_equities",
                       "private business": "private_business"}


def _split_against_assets(record: dict) -> list[str]:
    """Does the declared wealth split agree with the assets listed? Catches a value or a percentage
    read wrongly from the documents (or typed wrongly). Only checks kinds of asset that are listed."""
    claims = record.get("claims", {})
    worth = claims.get("declared_net_worth")
    split = claims.get("asset_composition") or {}
    if not isinstance(worth, (int, float)) or worth <= 0:
        return []
    totals = {}
    for asset in record.get("investments", []):
        kind = str(asset.get("asset_type") or "").lower()
        value = asset.get("current_declared_value")
        key = next((k for prefix, k in _ASSET_KIND_PREFIX.items() if kind.startswith(prefix)), None)
        if key and isinstance(value, (int, float)):
            totals[key] = totals.get(key, 0) + value
    warnings = []
    for key, listed in totals.items():
        share = split.get(key)
        if not isinstance(share, (int, float)):
            continue
        declared = share * worth
        if abs(listed - declared) > 0.15 * max(declared, listed):
            label = key.replace("_", " ")
            warnings.append(f"The {label} assets listed are worth {listed:,.0f}, but the declared split puts "
                            f"{share:.0%} ({declared:,.0f}) of the net worth in {label}.")
    return warnings


def _optional_fields(record: dict) -> list[dict]:
    """Fields shown in the summary that the form doesn't insist on."""
    fields = [_field(["career_history", i, "end_year"], "end year (blank = current job)", "year", _job_group(i, job))
              for i, job in enumerate(record.get("career_history", []))]
    return fields + [
        _field(["claims", "biggest_wealth_jump"], "Biggest single-period wealth increase (SGD)", "money"),
        _field(["claims", "wealth_jump_years"], "Over how many years", "money"),
        _field(["claims", "declared_counterparties"], "Known counterparties", "countries"),
    ]


SUMMARY_SECTIONS = {"name": "Client", "nationality": "Client", "career": "Client",
                    "career_history": "Career history", "investments": "Assets",
                    "claims": "Financial claims", "sow_declaration": "Source of wealth declaration"}


def summarise_document_record(record: dict, doc_record: dict) -> list[dict]:
    """One row per field for the officer to check: its value and where it came from.

    source: "documents" (read from the documents, unchanged), "edited" (read from the documents,
    then changed by the officer), "entered" (typed by the officer) or "blank".
    """
    rows = []
    fields = _required_fields(record) + _optional_fields(record)
    order = {name: i for i, name in enumerate(SUMMARY_SECTIONS.values())}
    for field in fields:
        value = get_path(record, field["path"])
        from_docs = get_path(doc_record, field["path"])
        if _blank(value) or (field["kind"] == "composition" and _missing(record, field)):
            source = "blank"
        elif _blank(from_docs) or (field["kind"] == "composition" and _missing(doc_record, field)):
            source = "entered"
        elif from_docs == value:
            source = "documents"
        else:
            source = "edited"
        rows.append({"section": SUMMARY_SECTIONS.get(field["path"][0], "Other"), "group": field["group"],
                     "label": field["short"], "kind": field["kind"], "value": value, "source": source,
                     "path": field["path"]})
    rows.sort(key=lambda row: (order.get(row["section"], 99),
                              row["path"][1] if len(row["path"]) > 2 and isinstance(row["path"][1], int) else -1))
    return rows


CAREER_JOB_KEYS = ["occupation", "seniority", "industry", "employer_name", "employer_size"]


def sync_after_edit(before: dict, after: dict) -> dict:
    """After the officer edits the record, keep "career" and the current job in career_history the same.
    If the profile screen changed "career", copy that into the current job; otherwise copy the
    (possibly edited) current job into "career"."""
    after = copy.deepcopy(after)
    jobs = after.get("career_history", [])
    open_jobs = [i for i, job in enumerate(jobs) if job.get("end_year") in (None, "")]
    changed = any((before.get("career") or {}).get(k) != (after.get("career") or {}).get(k)
                  for k in CAREER_JOB_KEYS)
    if changed and open_jobs:
        for key in CAREER_JOB_KEYS:
            jobs[open_jobs[-1]][key] = after["career"].get(key)
    return finish_document_record(after)

# ============================================================================
# COMBINED ASSESSMENT - one verdict from the crime scan, the benchmark and the forecast
# ============================================================================
# The three features test the same set of "possible stories" for the client: the forecast's
# person types (T1 = his wealth came exactly as declared, T2+ = crime stories).
#
#   Crime scan   is a crime story a known pattern for his industry, and did its warning
#                signs fire in his declaration?            (typologies -> person types)
#   Forecast     could the money add up under each story?  (pay, assets, the economy)
#   Benchmark    is he unusual next to peers in the same economy, and does the honest
#                story explain it?                         (economy -> benchmark)
#
# combined_verdict() turns all three into ONE verdict with reasons, and verdict_report()
# turns that into the report the command line prints and the web app shows (same text).
# Everything here is pure: no files, no network, no printing.

VERDICT_LABELS = {PASSED: "PASSED", MANUAL_REVIEW: "MANUAL REVIEW", RISKY: "RISKY", SERIOUS_RISK: "SERIOUS RISK"}

# Crime-scan match levels where every core warning sign fired
FULL_MATCH = ("critical", "high", "medium")
WEAK_MATCH = ("partial", "weak_signal")
MATCH_LABELS = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "partial": "PARTIAL",
                "weak_signal": "WEAK", "no_match": "NO MATCH"}

# The six themes a finding can belong to (used to show where the checks agree)
THEMES = {
    "wealth":    "Wealth too high for his career",
    "purchases": "Purchases he couldn't afford",
    "people":    "Unknown or unnamed people giving money",
    "countries": "Money from risky or unexpected countries",
    "sources":   "Sources that don't match his job",
    "pattern":   "Matches a known crime pattern",
}

# The crime scan's warning signs, which theme each belongs to, and how to say it
SIGN_THEMES = {"gift_or_inheritance_unnamed": "people", "counterparty_unnamed": "people",
               "related_party_transaction": "people", "source_contradicts_occupation": "sources",
               "unvalued_source_present": "sources"}
SIGN_LABELS = {"gift_or_inheritance_unnamed": "gift or inheritance from someone unnamed",
               "counterparty_unnamed": "money from unnamed people",
               "related_party_transaction": "money from a related party",
               "source_contradicts_occupation": "a source that doesn't fit his job",
               "unvalued_source_present": "a source with no amount"}

BENCHMARK_LABELS = {
    "TOTAL_WEALTH": "Total wealth", "UNEXPLAINED_GAP": "Unexplained wealth",
    "VELOCITY": "Wealth growth speed", "JUMP_VS_ECONOMY": "Wealth jump vs economy",
    "LIQUIDITY": "Liquidity", "AUM_FUNDING": "Money brought to the bank",
    "COMPOSITION_PROPERTY": "Asset mix: property", "COMPOSITION_EQUITIES": "Asset mix: listed shares",
    "COMPOSITION_BUSINESS": "Asset mix: private business", "COMPOSITION_CASH": "Asset mix: cash",
    "COUNTERPARTIES": "Counterparties", "JURISDICTION": "Countries", "PEP_STATUS": "PEP status",
}
BENCHMARK_THEMES = {"COUNTERPARTIES": "people", "JURISDICTION": "countries", "PEP_STATUS": "pattern"}
# Benchmark checks worked out from the forecast's honest story, so they can't "agree" with it
FORECAST_DERIVED = ("UNEXPLAINED_GAP", "PEP_STATUS", "PEER_BENCHMARK")
COMPOSITION_RULES = {"COMPOSITION_PROPERTY": "property", "COMPOSITION_EQUITIES": "listed_equities",
                     "COMPOSITION_BUSINESS": "private_business", "COMPOSITION_CASH": "cash"}

# Status of one check: failed (counts), weak (a loose end), explained (failed on its own, but
# another feature explains it, so it doesn't count), passed, skipped (couldn't run), info.
COUNTS = ("failed",)


def _sgd(value) -> str:
    """12345.6 -> 'S$12,346'. Anything that isn't a number -> 'n/a'."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"-S${-value:,.0f}" if value < 0 else f"S${value:,.0f}"


def _check(feature: str, check: str, label: str, theme: str, status: str, detail: str,
           explanation: str = "", group: str = "") -> dict:
    """One finding in the shared shape all three features use."""
    return {"feature": feature, "check": check, "label": label, "theme": theme, "status": status,
            "detail": detail, "explanation": explanation, "group": group or label}


# ---------------------------------------------------------------------------
# 1. The client record -> the forecasting case (the same for the CLI and the web app)
# ---------------------------------------------------------------------------

_PRESENT_WORDS = ("", "present", "now", "current", "-", "none")


def _read_amount(text) -> float | None:
    """'1,650,000', 'S$1.65m', '410k', 30 -> a number; None if it can't be read."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = str(text or "").strip().lower().replace(",", "").replace(" ", "")
    for prefix in ("sgd", "s$", "$"):
        cleaned = cleaned.removeprefix(prefix)
    multiplier = 1.0
    for suffix, factor in (("million", 1e6), ("mil", 1e6), ("m", 1e6), ("k", 1e3), ("%", 1.0)):
        if cleaned.endswith(suffix):
            cleaned, multiplier = cleaned[: -len(suffix)], factor
            break
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _read_year(text) -> int | None:
    value = _read_amount(text)
    return int(value) if value is not None and 1900 <= value <= 2100 else None


def _read_payment(text) -> str | None:
    """'Cash', 'mortgage', 'bank loan', 'sold my flat' -> cash / loan / asset_sale."""
    words = str(text or "").lower()
    if any(w in words for w in ("loan", "mortgage", "financ", "borrow")):
        return "loan"
    if any(w in words for w in ("sale", "sold", "proceeds")):
        return "asset_sale"
    if "cash" in words or "savings" in words:
        return "cash"
    return None


def forecasting_case(record: dict, today: str) -> tuple[dict | None, list[str]]:
    """Turn a saved client record (from the web form, the command line or the documents
    auto-fill) into the case every part of the assessment uses, dated today.

    Returns (case, []) when everything can be read, otherwise (None, what's missing).
    The command line then asks the officer for what's missing (io_manager.case_from_client_record);
    the web app asks the officer to complete the record. Both assess the same case.
    """
    missing: list[str] = []
    career = record.get("career") or {}
    claims = record.get("claims") or {}
    this_year = int(today[:4])

    def text(value, label: str) -> str:
        found = str(value or "").strip()
        if not found:
            missing.append(label)
        return found

    age = _read_amount(career.get("age"))
    if age is None or not 18 <= age <= 120:
        missing.append("age")

    jobs = []
    for number, entry in enumerate(record.get("career_history") or [], start=1):
        label = f"job {number}"
        start = _read_year(entry.get("start_year"))
        if start is None or start > this_year:
            missing.append(f"{label} start year")
        end_text = "" if entry.get("end_year") is None else str(entry.get("end_year")).strip().lower()
        end = None if end_text in _PRESENT_WORDS else _read_year(entry.get("end_year"))
        if end_text not in _PRESENT_WORDS and (end is None or end > this_year or (start and end < start)):
            missing.append(f"{label} end year")
        jobs.append({
            "start_year": start, "end_year": end,
            "occupation": text(entry.get("occupation"), f"{label} job title"),
            "seniority": text(entry.get("seniority"), f"{label} seniority"),
            "industry": text(entry.get("industry"), f"{label} industry"),
            "employer_name": text(entry.get("employer_name"), f"{label} employer"),
            "employer_size": text(entry.get("employer_size"), f"{label} employer size"),
            "country": text(entry.get("country"), f"{label} country"),
        })
    jobs.sort(key=lambda job: job["start_year"] or 0)

    # The command line keeps the current job in "career" only: it started when the last job
    # ended, or when his career started if there were no earlier jobs.
    if not jobs or jobs[-1]["end_year"] is not None:
        started = jobs[-1]["end_year"] if jobs else _read_year(career.get("career_start_year"))
        if started is None:
            missing.append("current job start year")
        jobs.append({
            "start_year": started, "end_year": None,
            "occupation": text(career.get("occupation"), "current job title"),
            "seniority": text(career.get("seniority"), "current seniority"),
            "industry": text(career.get("industry"), "current industry"),
            "employer_name": text(career.get("employer_name"), "current employer"),
            "employer_size": text(career.get("employer_size"), "current employer size"),
            "country": text(career.get("country_of_residence"), "country of current job"),
        })

    investments = []
    for number, entry in enumerate(record.get("investments") or [], start=1):
        name = str(entry.get("asset_type") or "").strip() or f"Asset {number}"
        if str(entry.get("year_sold") or "").strip():
            continue                                   # sold assets are left out of the forecast
        currency = str(entry.get("currency") or "SGD").strip().upper()
        if currency not in ("", "SGD", "S$"):
            missing.append(f"{name}: amounts in SGD (recorded in {currency})")
        value = entry.get("current_declared_value")
        value = entry.get("current_value") if value in (None, "") else value
        income = entry.get("income_produced")
        income = entry.get("yearly_income") if income in (None, "") else income
        year = _read_year(entry.get("year_acquired"))
        price = _read_amount(entry.get("price_paid"))
        current = _read_amount(value)
        produced = _read_amount(income if income not in (None, "") else 0)
        method = _read_payment(entry.get("payment_method"))
        for ok, label in ((year is not None and year <= this_year, "year bought"),
                          (price is not None and price > 0, "price paid"),
                          (current is not None and current >= 0, "current value"),
                          (produced is not None and produced >= 0, "yearly income"),
                          (method is not None, "how it was paid for")):
            if not ok:
                missing.append(f"{name}: {label}")
        investments.append({"asset_type": name, "country": text(entry.get("country"), f"{name}: country"),
                            "year_acquired": year, "price_paid": price, "current_declared_value": current,
                            "payment_method": method, "income_produced": produced})

    typed = claims.get("asset_composition") or {}
    split = {key: _read_amount(typed.get(key)) for key in COMPOSITION_KEYS}
    if all(v is not None for v in split.values()) and 95 <= sum(split.values()) <= 105:
        split = {k: v / 100 for k, v in split.items()}          # typed as percentages
    if any(v is None for v in split.values()) or not 0.95 <= sum(split.values()) <= 1.05:
        missing.append("asset composition adding up to 100%")

    worth = _read_amount(claims.get("declared_net_worth"))
    aum = _read_amount(claims.get("expected_aum"))
    if worth is None or worth <= 0:
        missing.append("declared net worth")
    if aum is None or aum <= 0:
        missing.append("expected assets with the bank")
    countries = [str(c).strip() for c in claims.get("wealth_countries") or [] if str(c).strip()]
    if not countries:
        missing.append("countries where the wealth came from")
    jump = _read_amount(claims.get("biggest_wealth_jump"))
    jump_years = _read_amount(claims.get("wealth_jump_years"))
    sow = text(record.get("sow_declaration"), "source of wealth declaration")
    if sow and len(sow) < 50:
        missing.append("source of wealth declaration of at least 50 characters")

    case = {
        "client_ref": record.get("client_ref"),
        "current_date": today,
        "name": text(record.get("name"), "name"),
        "nationality": text(record.get("nationality"), "nationality"),
        "career": {
            "occupation": jobs[-1]["occupation"], "seniority": jobs[-1]["seniority"],
            "industry": jobs[-1]["industry"], "employer_name": jobs[-1]["employer_name"],
            "employer_size": jobs[-1]["employer_size"],
            "country_of_residence": text(career.get("country_of_residence"), "country of residence"),
            "age": int(age) if age is not None else None,
            "career_start_year": jobs[0]["start_year"],
        },
        "career_history": jobs,
        "investments": investments,
        "claims": {
            "declared_net_worth": worth, "expected_aum": aum, "asset_composition": split,
            "wealth_countries": countries, "pep_status": text(claims.get("pep_status"), "PEP status"),
            # not needed by the forecast, but the benchmark uses them
            "biggest_wealth_jump": jump if jump and jump > 0 else None,
            "wealth_jump_years": jump_years if jump_years and jump_years > 0 else None,
            "declared_counterparties": [str(c).strip() for c in claims.get("declared_counterparties") or []
                                        if str(c).strip()],
        },
        "sow_declaration": sow,
        "reviews": [],
    }
    missing = list(dict.fromkeys(missing))
    return (None, missing) if missing else (case, [])


# ---------------------------------------------------------------------------
# 2. Links between the features
# ---------------------------------------------------------------------------

def crime_declaration(declaration: dict | None, policy: dict | None = None) -> tuple[dict | None, list[str]]:
    """The declaration the forecast read (Step 0), as the crime scan's warning-sign rows.

    The declaration is read by the AI ONCE; this only reshapes it. A salary, bonus or savings
    source tied to one of his jobs needs no amount: the forecast works out what the job paid.
    Returns ({"sources": [...]}, warnings) or (None, warnings) if there is no declaration.
    """
    if not declaration:
        return None, []
    policy = policy or utilities.policy()
    explained_by_career = set(policy.get("self_explained_source_types", []))
    warnings, rows = [], []
    for source in declaration.get("claimed_sources", []):
        kind = source.get("source_type")
        if kind == "inheritance_gift":
            kind = "inheritance" if "inherit" in str(source.get("description", "")).lower() else "gift"
        read = "counterparty" in source
        rows.append({
            "source_id": source.get("source_id"),
            "source_type": kind,
            "description": source.get("description", ""),
            "amount_sgd": source.get("amount"),
            "start_year": source.get("start_year"),
            "end_year": source.get("end_year"),
            "jurisdiction": source.get("country"),
            # an older saved reading has no counterparty: don't count it as "unnamed"
            "counterparty": source.get("counterparty") if read else "(not read)",
            "evidence_sentence": source.get("quote", ""),
            "occupation_mismatch": bool(source.get("occupation_mismatch", False)),
            "amount_explained_by_forecast": (source.get("source_type") in explained_by_career
                                             and source.get("linked_position_index") is not None),
        })
        if not read and not warnings:
            warnings.append("The saved declaration reading has no counterparties; run the assessment "
                            "again with a fresh reading to check for unnamed people.")
    return {"sources": rows}, warnings


def economy_brief(markets: dict | None) -> dict | None:
    """What the benchmark AI may see of the forecast's economy (Call 1): each market's yearly %
    change and each country's risk listings. Nothing about the client's own figures."""
    if not markets or not markets.get("series"):
        return None
    return {
        "markets": [{"name": m["name"], "country": m["country"], "indicator": m["indicator"],
                     "yearly_change_pct": {str(h["year"]): h["change_pct"]
                                           for h in markets["series"].get(m["market_id"], {}).get("history", [])}}
                    for m in markets.get("relevant_markets", [])],
        "country_listings": [{"country": j["country"], "listings": j["listings"]}
                             for j in markets.get("jurisdictions", [])],
    }


# ---------------------------------------------------------------------------
# 2b. The documents layer: what the submitted documents themselves show
# ---------------------------------------------------------------------------
# The client record holds what the client DECLARED (the standard form). The documents layer
# holds what his documents SHOW, each fact with the file it came from. The two are compared,
# and every contradiction is evidence for the verdict, like a benchmark or crime-scan finding.

def document_evidence(extracted: dict | None, files: list[str] | None = None,
                      policy: dict | None = None) -> dict:
    """The AI's reading of every submitted document, cleaned into the documents layer:
    {"files", "documented_pay": [{year, amount, source_file}], "findings": [...], "notes": [...]}.
    Anything the reading left out is just empty, so older readings still work."""
    policy = policy or utilities.policy()
    kinds = policy["verdict"]["document_evidence"]
    evidence = (extracted or {}).get("evidence") or {}
    pay = []
    for row in evidence.get("documented_pay") or []:
        year, amount = _read_year(row.get("year")), _read_amount(row.get("employment_income_sgd"))
        if year and amount and amount > 0 and year not in [p["year"] for p in pay]:
            pay.append({"year": year, "amount": amount, "source_file": row.get("source_file")})
    findings = []
    for row in evidence.get("findings") or []:
        kind = row.get("kind") if row.get("kind") in kinds else "other"
        summary = str(row.get("summary") or "").strip()
        if not summary:
            continue
        findings.append({"kind": kind, "summary": summary, "amount_sgd": _read_amount(row.get("amount_sgd")),
                         "per": row.get("per"), "counterparty": row.get("counterparty"),
                         "country": row.get("country"), "source_files": list(row.get("source_files") or [])})
    order = list(kinds)
    findings.sort(key=lambda f: (not kinds[f["kind"]]["serious"], order.index(f["kind"])))
    return {"files": list(files or []), "read": extracted is not None,
            "documented_pay": sorted(pay, key=lambda p: p["year"]), "findings": findings,
            "notes": [str(n) for n in (extracted or {}).get("notes") or []]}


def calibrate_pay(case: dict, forecast: dict, documented_pay: list[dict], policy: dict) -> tuple[dict, list[dict]]:
    """Use the pay his documents show (tax notices, employment letter) instead of the AI's guess.

    For each job with documented years, every year of that job's forecast pay is scaled by
    documented / AI-typical over those years (the current job's future pay too), so the honest
    story is built on his real pay. The AI never sees these figures; only this arithmetic does.
    Returns (adjusted forecast copy, one row per adjusted job)."""
    settings = policy["verdict"]["pay_calibration"]
    documented = {row["year"]: row for row in documented_pay or []}
    if not documented:
        return forecast, []
    forecast = copy.deepcopy(forecast)
    _, end = utilities.career_years(case)
    jobs = utilities.career_positions(case)
    adjusted = []
    for position in forecast["earnings"]["positions"]:
        index = position["position_index"]
        if not 0 <= index < len(jobs):
            continue
        job = jobs[index]
        first, last = int(job["start_year"]), utilities.position_end(job, end)
        years = [row for row in position["yearly"] if row["year"] in documented and first <= row["year"] <= last]
        guessed = sum(pay_total(row, "typical") for row in years)
        actual = sum(documented[row["year"]]["amount"] for row in years)
        if not years or guessed <= 0:
            continue
        ratio = min(max(actual / guessed, 1 / settings["max_ratio"]), settings["max_ratio"])
        if abs(ratio - 1) < settings["min_change"]:
            continue
        for row in position["yearly"]:
            for part in ("base_pay", "bonus", "equity_or_profit_share"):
                row[part] = {k: v * ratio for k, v in row[part].items()}
        if job.get("end_year") in (None, ""):
            for row in forecast["earnings"]["future_pay"]["yearly"]:
                for scenario in utilities.SCENARIOS:
                    for part in ("base_pay", "bonus", "equity_or_profit_share"):
                        row[scenario][part] = {k: v * ratio for k, v in row[scenario][part].items()}
        shown = years[-1]["year"]
        adjusted.append({"position_index": index, "job": f"{job.get('occupation')} at {job.get('employer_name')}",
                         "ratio": round(ratio, 2), "year": shown, "ai_pay": round(pay_total(
                             next(r for r in years if r["year"] == shown), "typical") / ratio),
                         "documented_pay": documented[shown]["amount"],
                         "source_file": documented[shown].get("source_file")})
    return forecast, adjusted


def document_checks(evidence: dict | None, types: list[dict], policy: dict) -> list[dict]:
    """The documents layer as findings in the shared shape: each fact the declaration leaves out
    or contradicts, and which crime stories it supports."""
    if not evidence or not evidence.get("files"):
        return [_check("documents", "NO_DOCUMENTS", "Supporting documents", "pattern", "info",
                       "No supporting documents were submitted, so nothing was checked against them.")]
    if not evidence.get("read"):
        return [_check("documents", "DOCUMENTS_UNREAD", "Supporting documents", "pattern", "skipped",
                       f"{len(evidence['files'])} document(s) couldn't be read.")]
    kinds = policy["verdict"]["document_evidence"]
    checks = []
    for finding in evidence["findings"]:
        setting = kinds[finding["kind"]]
        stories = [t for t in types if t["category"] in setting["supports"]]
        check = _check("documents", finding["kind"].upper(), setting["label"], setting["theme"],
                       "failed" if setting["serious"] else "weak", finding["summary"],
                       ("Supports " + "; ".join(f"{t['type_id']} {t['name']}" for t in stories) + ".") if stories else "",
                       group="Documents")
        check["sources"] = finding["source_files"]
        check["supports"] = setting["supports"]
        checks.append(check)
    if not checks:
        checks.append(_check("documents", "DOCUMENTS_AGREE", "Supporting documents", "pattern", "passed",
                             f"The {len(evidence['files'])} document(s) agree with his declaration.",
                             group="Documents"))
    return checks


def type_patterns(forecast: dict | None, assessment: dict | None, crime_result: dict | None,
                  typologies: list[dict] | None) -> list[dict]:
    """One row per crime person type: does its money fit (forecast), which researched pattern it
    was built from, and how many of that pattern's warning signs fired (crime scan)."""
    if not forecast or not assessment:
        return []
    matches = {m["typology_id"]: m for m in (crime_result or {}).get("match_results", [])}
    by_id = {t.get("typology_id"): t for t in typologies or []}
    fits = {row["type_id"]: row for row in assessment.get("types", [])}
    rows = []
    for person_type in forecast["types"]["person_types"]:
        type_id = person_type["type_id"]
        if type_id == utilities.BASELINE_TYPE:
            continue
        fit = fits.get(type_id, {})
        typology_id = person_type.get("typology_id")
        if typology_id not in by_id:   # older replies: link by category, as ai_manager.link_typologies does
            typology_id = next((t.get("typology_id") for t in typologies or []
                                if t.get("crime_category") == person_type.get("category")), None)
        match = matches.get(typology_id, {})
        typology = by_id.get(typology_id, {})
        rows.append({
            "type_id": type_id, "name": person_type["name"], "category": person_type["category"],
            "category_name": category_name(person_type["category"]),
            "fits": bool(fit.get("fits")), "fit_score": fit.get("fit_score"),
            "route_years": fit.get("route_years", ""), "enabled_by": fit.get("enabled_by", ""),
            "typology_id": typology_id, "typology_name": typology.get("name"),
            "match": match.get("category") if match else None,
            "fired": list(match.get("core_fired", [])) + list(match.get("supporting_fired", [])) if match else [],
            "quote": (typology.get("source_quotes") or [{}])[0] if typology else {},
            "source_ids": typology.get("source_ids", []),
        })
    return rows


# ---------------------------------------------------------------------------
# 3. The honest story's numbers (shared by the benchmark cross-checks and the forecast checks)
# ---------------------------------------------------------------------------

def honest_numbers(case: dict, forecast: dict, assessment: dict, policy: dict) -> dict:
    """T1's fit, wealth today, liquid wealth, wealth by year and peak pay."""
    start, end = utilities.career_years(case)
    savings_rows = career_savings(forecast, start, end)
    paths = all_asset_paths(case, forecast, end, "steady")
    t1 = next(row for row in assessment["types"] if row["type_id"] == utilities.BASELINE_TYPE)
    now = honest_wealth(case, savings_rows, paths, end)
    held = {b: sum(p["values"][end][b] for p in paths) for b in BANDS}
    # honest_wealth counts each asset as bought outright from savings. In cash terms, a loan
    # only took the deposit out of savings, and an asset sale paid with the sale's proceeds.
    not_from_savings = sum(asset["price_paid"] * policy["loan_to_value_max"] if asset.get("payment_method") == "loan"
                           else asset["price_paid"] if asset.get("payment_method") == "asset_sale" else 0.0
                           for asset in case.get("investments", []))
    cash = band(*(now[b] - held[b] + not_from_savings for b in BANDS))
    listed = {b: sum(p["values"][end][b] for asset, p in zip(case.get("investments", []), paths)
                     if asset_category(asset["asset_type"], policy) == "listed_equities") for b in BANDS}
    liquid = band(*(cash[b] + listed[b] for b in BANDS))
    by_year = {start - 1: zero_band()}
    for year in range(start, end + 1):
        by_year[year] = honest_wealth(case, savings_rows, paths, year)
    peak_pay = max((row["total_pay"]["typical"] for row in savings_rows), default=0.0)
    return {"t1": t1, "now": now, "cash": cash, "liquid": liquid, "by_year": by_year,
            "peak_pay": peak_pay, "paths": paths, "savings_rows": savings_rows, "start": start, "end": end,
            "models": sorted(forecast["earnings"]["assets"], key=lambda a: a["asset_index"])}


def _market_growth(history: dict, market_id: str, first: int, last: int) -> float:
    """% a market grew from the end of `first` to the end of `last` (unknown years count as 0)."""
    level = 1.0
    for year in range(first + 1, last + 1):
        level *= 1 + float(history.get(market_id, {}).get(year) or 0.0) / 100
    return (level - 1) * 100


def _best_market_stretch(markets: dict | None, years: int, start: int, end: int) -> tuple[float, int, int] | None:
    """The best `years`-long stretch of his markets during his career: (average % a year, first, last)."""
    if not markets or not markets.get("series"):
        return None
    changes = {}
    for series in markets["series"].values():
        for row in series.get("history", []):
            if row.get("change_pct") is not None:
                changes.setdefault(row["year"], []).append(row["change_pct"])
    yearly = {y: sum(v) / len(v) for y, v in changes.items() if start <= y <= end}
    if not yearly:
        return None
    years = max(1, min(years, end - start + 1))
    best = None
    for last in range(start + years - 1, end + 1):
        window = [yearly.get(y, 0.0) for y in range(last - years + 1, last + 1)]
        average = sum(window) / years
        if best is None or average > best[0]:
            best = (round(average, 1), last - years + 1, last)
    return best


def _best_honest_jump(honest: dict, years: int) -> tuple[float, int, int]:
    """The most the honest story's wealth rose over any `years`-long stretch: (gain, first, last)."""
    by_year = honest["by_year"]
    first_year, last_year = honest["start"], honest["end"]
    years = max(1, min(years, last_year - first_year + 1))
    best = None
    for last in range(first_year + years - 1, last_year + 1):
        gain = by_year[last]["optimistic"] - by_year[last - years]["optimistic"]
        if best is None or gain > best[0]:
            best = (gain, last - years + 1, last)
    return best


def _jump_years(case: dict) -> tuple[float | None, int | None]:
    claims = case.get("claims", {})
    jump, years = claims.get("biggest_wealth_jump"), claims.get("wealth_jump_years")
    if not isinstance(jump, (int, float)) or jump <= 0 or not isinstance(years, (int, float)) or years <= 0:
        return None, None
    return float(jump), max(1, round(years))


def is_pep(case: dict, policy: dict) -> bool:
    status = str(case.get("claims", {}).get("pep_status") or "").strip().lower()
    return status not in policy["verdict"]["no_pep_values"]


# ---------------------------------------------------------------------------
# 4. The checks of each feature, in the shared shape (with the cross-checks)
# ---------------------------------------------------------------------------

def benchmark_checks(case: dict, bench_input: dict | None, ai_data: dict | None, findings: list[dict] | None,
                     honest: dict | None, assessment: dict | None, declaration: dict | None,
                     markets: dict | None, types: list[dict], policy: dict) -> list[dict]:
    """The peer benchmark's checks, each cross-checked against the forecast: a failure the
    honest forecast explains is marked "explained" and doesn't count towards the verdict."""
    settings = policy["verdict"]
    claims = case.get("claims", {})
    worth = claims.get("declared_net_worth") or 0
    split = claims.get("asset_composition") or {}
    liquid_share = (split.get("listed_equities") or 0) + (split.get("cash") or 0)
    declared_liquid = liquid_share * worth
    t1 = honest["t1"] if honest else None
    checks = []
    by_rule = {f["rule_id"]: f for f in findings or []}
    not_checked = "Couldn't be cross-checked: the forecast didn't finish."

    def failed_or_explained(failed: bool, explained: bool | None, yes: str, no: str) -> tuple[str, str]:
        if not failed:
            return "passed", ""
        if explained is None:
            return "failed", not_checked
        return ("explained", yes) if explained else ("failed", no)

    if findings is None:
        checks.append(_check("benchmark", "PEER_BENCHMARK", "Peer benchmark", "wealth", "skipped",
                             "The peer benchmark couldn't run, so declared figures weren't compared with peers."))

    # Total wealth vs peers
    if "TOTAL_WEALTH" in by_rule:
        expected = ai_data["expected_wealth"]
        ratio = worth / expected if expected else 0
        status, why = failed_or_explained(
            by_rule["TOTAL_WEALTH"]["failed"], t1["net_worth_fits"] if t1 else None,
            f"Explained: the honest forecast reaches {_sgd(t1['wealth_now']['cautious'])} to "
            f"{_sgd(t1['wealth_now']['optimistic'])}, which "
            + ("covers it." if worth <= t1["wealth_now"]["optimistic"] else
               f"is within the {policy['tolerance']['net_worth']:.0%} tolerance.") if t1 else "",
            f"Not explained: the honest forecast reaches at most {_sgd(t1['wealth_now']['optimistic'])}." if t1 else "")
        checks.append(_check("benchmark", "TOTAL_WEALTH", BENCHMARK_LABELS["TOTAL_WEALTH"], "wealth", status,
                             f"Declared {_sgd(worth)} vs about {_sgd(expected)} for peers in this economy "
                             f"({ratio:.1f}x; flagged above {LIMITS['total_wealth']}x)", why))

    # NEW: how much the honest story can't explain, in money and in years of his pay
    if honest:
        reach = t1["wealth_now"]["optimistic"]
        gap = worth - reach
        over = worth > reach * (1 + policy["tolerance"]["net_worth"])
        years = gap / honest["peak_pay"] if honest["peak_pay"] > 0 else None
        if over:
            detail = (f"{_sgd(gap)} of the declared {_sgd(worth)} is beyond the most the honest story reaches "
                      f"({_sgd(reach)})" + (f": about {years:.0f} years of his peak pay "
                                            f"({_sgd(honest['peak_pay'])} a year)" if years else ""))
        elif worth > reach:
            detail = (f"The honest story reaches up to {_sgd(reach)}, within the "
                      f"{policy['tolerance']['net_worth']:.0%} tolerance of the declared {_sgd(worth)}.")
        else:
            detail = f"The honest story reaches up to {_sgd(reach)}, which covers the declared {_sgd(worth)}."
        checks.append(_check("benchmark", "UNEXPLAINED_GAP", BENCHMARK_LABELS["UNEXPLAINED_GAP"], "wealth",
                             "failed" if over else "passed", detail))
    else:
        checks.append(_check("benchmark", "UNEXPLAINED_GAP", BENCHMARK_LABELS["UNEXPLAINED_GAP"], "wealth",
                             "skipped", "Needs the honest forecast, which didn't finish."))

    # Wealth growth speed vs peers, cross-checked with the honest path
    jump, jump_years = _jump_years(case)
    velocity_failed = velocity_status = None
    if "VELOCITY" in by_rule:
        finding = by_rule["VELOCITY"]
        if finding["detail"].startswith("Skipped") or jump is None:
            checks.append(_check("benchmark", "VELOCITY", BENCHMARK_LABELS["VELOCITY"], "wealth", "skipped",
                                 "No biggest wealth jump was recorded, so growth speed wasn't checked."))
        else:
            velocity_failed = finding["failed"]
            expected = ai_data["expected_velocity"]
            per_year = jump / jump_years
            explained = None
            if honest:
                best = _best_honest_jump(honest, jump_years)
                explained = jump <= best[0] * (1 + policy["tolerance"]["net_worth"])
            status, why = failed_or_explained(
                velocity_failed, explained,
                f"Explained: the honest story could gain up to {_sgd(best[0])} in {best[1]}-{best[2]}."
                if honest else "",
                f"Not explained: the honest story gains at most {_sgd(best[0])} in any {jump_years}-year "
                f"stretch (best {best[1]}-{best[2]})." if honest else "")
            velocity_status = status
            checks.append(_check("benchmark", "VELOCITY", BENCHMARK_LABELS["VELOCITY"], "wealth", status,
                                 f"Biggest jump {_sgd(jump)} over {jump_years} year(s) = {_sgd(per_year)} a year vs "
                                 f"about {_sgd(expected)} a year for peers ({per_year / expected if expected else 0:.1f}x; "
                                 f"flagged above {LIMITS['velocity']}x)", why))

    # NEW: was the economy strong enough for such a jump?
    if jump is None:
        checks.append(_check("benchmark", "JUMP_VS_ECONOMY", BENCHMARK_LABELS["JUMP_VS_ECONOMY"], "wealth",
                             "skipped", "No biggest wealth jump was recorded."))
    else:
        start, end = utilities.career_years(case)
        best = _best_market_stretch(markets, jump_years, start, end)
        if best is None or velocity_failed is None:
            checks.append(_check("benchmark", "JUMP_VS_ECONOMY", BENCHMARK_LABELS["JUMP_VS_ECONOMY"], "wealth",
                                 "skipped", "Needs the economy forecast and the peer growth speed."))
        else:
            average, first, last = best
            boom = settings["booming_market_pct"]
            note = "The jump's own years aren't recorded, so his markets' best stretch is used."
            if not velocity_failed:
                status, detail = "passed", (f"Within the peer range. His markets' best {jump_years}-year stretch "
                                            f"was {first}-{last} ({average:+.1f}% a year).")
            elif average >= boom:
                status, detail = "explained", (f"Faster than peers, but his markets boomed in {first}-{last} "
                                               f"({average:+.1f}% a year), which can explain it. {note}")
            elif velocity_status == "explained":
                status, detail = "explained", (f"Faster than peers and his markets never boomed (best {jump_years}-"
                                               f"year stretch {first}-{last}, {average:+.1f}% a year), but the "
                                               f"honest story's own growth explains the jump (see growth speed).")
            else:
                status, detail = "failed", (f"Faster than peers, and his markets never boomed: their best "
                                            f"{jump_years}-year stretch was {first}-{last} at {average:+.1f}% a year "
                                            f"(a boom is {boom:g}% or more). {note}")
            checks.append(_check("benchmark", "JUMP_VS_ECONOMY", BENCHMARK_LABELS["JUMP_VS_ECONOMY"], "wealth",
                                 status, detail))

    # Liquidity vs peers, cross-checked with the honest story's cash and shares
    if "LIQUIDITY" in by_rule:
        declared_pct = bench_input["listed_equities"] + bench_input["cash"]
        explained = (declared_liquid <= honest["liquid"]["optimistic"] * (1 + settings["aum_tolerance"])
                     if honest else None)
        status, why = failed_or_explained(
            by_rule["LIQUIDITY"]["failed"], explained,
            f"Explained: the honest story leaves him up to {_sgd(honest['liquid']['optimistic'])} in cash and "
            f"shares, enough for the {_sgd(declared_liquid)} he declares." if honest else "",
            f"Not explained: he declares {_sgd(declared_liquid)} in cash and shares; the honest story leaves "
            f"at most {_sgd(honest['liquid']['optimistic'])}." if honest else "")
        checks.append(_check("benchmark", "LIQUIDITY", BENCHMARK_LABELS["LIQUIDITY"], "wealth", status,
                             f"{declared_pct}% of his wealth in cash and listed shares vs about "
                             f"{ai_data['expected_liquidity']:g}% for peers ("
                             + (f"{declared_pct / ai_data['expected_liquidity']:.1f}x; " if ai_data['expected_liquidity'] else "")
                             + f"flagged above {LIMITS['liquidity']}x)", why))

    # NEW: can he fund the money he says he'll bring to the bank?
    aum = claims.get("expected_aum")
    if isinstance(aum, (int, float)) and aum > 0:
        tolerance = 1 + settings["aum_tolerance"]
        short_own = aum > declared_liquid * tolerance
        short_honest = honest is not None and aum > honest["liquid"]["optimistic"] * tolerance
        detail = f"Plans to bring {_sgd(aum)}; by his own split he holds {_sgd(declared_liquid)} in cash and shares"
        if honest:
            detail += f", and the honest story leaves him up to {_sgd(honest['liquid']['optimistic'])}"
        checks.append(_check("benchmark", "AUM_FUNDING", BENCHMARK_LABELS["AUM_FUNDING"], "wealth",
                             "failed" if short_own or short_honest else "passed", detail + "."))
        checks[-1]["independent"] = short_own        # short by his OWN figures, not only by the forecast

    # Asset mix vs peers, cross-checked with his listed assets and their markets
    history = market_history({"markets": markets}) if markets and markets.get("series") else {}
    asset_checks = {c["asset_index"]: c for c in (assessment or {}).get("asset_checks", [])
                    if c["rule_id"] == "ASSET_VALUE"}
    models = {m["asset_index"]: m for m in (honest or {}).get("models", [])}
    for rule, key in COMPOSITION_RULES.items():
        if rule not in by_rule:
            continue
        declared_pct = bench_input[key]
        expected_pct = ai_data[f"expected_{key}"]
        declared_amount = (split.get(key) or 0) * worth
        explained, yes, no = None, "", ""
        if honest:
            if key == "cash":
                reach = honest["cash"]["optimistic"]
                explained = 0 < declared_amount <= reach * (1 + settings["composition_tolerance"])
                yes = f"Explained: the honest story leaves him up to {_sgd(reach)} in cash."
                no = f"Not explained: the honest story leaves him at most {_sgd(reach)} in cash."
            else:
                indexes = [i for i, a in enumerate(case.get("investments", []))
                           if asset_category(a["asset_type"], policy) == key]
                listed = sum(case["investments"][i]["current_declared_value"] for i in indexes)
                values_ok = all(not asset_checks.get(i, {}).get("failed") for i in indexes)
                close = listed > 0 and abs(listed - declared_amount) <= settings["composition_tolerance"] * max(
                    listed, declared_amount)
                explained = bool(indexes) and close and values_ok
                market_note = ""
                if indexes and history:
                    first = case["investments"][indexes[0]]
                    model = models.get(indexes[0])
                    if model and model["exposures"]:
                        top = max(model["exposures"], key=lambda e: e["sensitivity"]["typical"])
                        growth = _market_growth(history, top["market_id"], int(first["year_acquired"]), honest["end"])
                        market_note = f" ({top['market_id']} {growth:+.0f}% since {first['year_acquired']})"
                yes = (f"Explained: his {key.replace('_', ' ')} assets ({_sgd(listed)}) match that share and their "
                       f"markets explain their values{market_note}.")
                no = ("Not explained: " + (f"his {key.replace('_', ' ')} assets add up to {_sgd(listed)}, not "
                                           f"{_sgd(declared_amount)}" if indexes else
                                           f"no {key.replace('_', ' ')} assets are listed")
                      + ("" if values_ok else ", and their declared values are above what their markets explain")
                      + ".")
        status, why = failed_or_explained(by_rule[rule]["failed"], explained, yes, no)
        checks.append(_check("benchmark", rule, BENCHMARK_LABELS[rule], "wealth", status,
                             f"Declared {declared_pct}% vs about {expected_pct:g}% for peers "
                             f"(off by {abs(declared_pct - expected_pct):g} points; flagged above "
                             f"{LIMITS['composition']})", why, group="Asset mix"))

    # Counterparties not expected for his career, cross-checked with his declaration
    if "COUNTERPARTIES" in by_rule:
        words = settings["service_provider_words"]
        providers = [c for c in bench_input.get("declared_counterparties", [])
                     if any(re.search(rf"\b{re.escape(w)}\b", c.lower()) for w in words)]
        unknown = calculate_counterparties([c for c in bench_input.get("declared_counterparties", [])
                                            if c not in providers], ai_data.get("expected_counterparties", []))
        by_rule["COUNTERPARTIES"] = {**by_rule["COUNTERPARTIES"], "failed": len(unknown) > LIMITS["counterparties"]}
        sources = (declaration or {}).get("claimed_sources", [])

        def backed(name: str) -> bool:
            low = name.lower()
            return any((source.get("linked_position_index") is not None
                        or source.get("linked_asset_index") is not None)
                       and source.get("counterparty")
                       and (low in source["counterparty"].lower() or source["counterparty"].lower() in low)
                       for source in sources)
        explained = all(backed(name) for name in unknown) if declaration else None
        status, why = failed_or_explained(
            by_rule["COUNTERPARTIES"]["failed"], explained,
            "Explained: each one is named in his declaration as linked to one of his jobs or assets.",
            "Not explained: not every one is linked to a job or asset in his declaration.")
        checks.append(_check("benchmark", "COUNTERPARTIES", BENCHMARK_LABELS["COUNTERPARTIES"], "people", status,
                             ((f"{len(unknown)} not usual for his career: {', '.join(unknown)} "
                               f"(flagged when more than {LIMITS['counterparties']})") if unknown
                              else "All usual for his career.")
                             + (f" Banks, brokers and law firms are left out: {', '.join(providers)}." if providers else ""),
                             why))

    # Countries not expected for his career, cross-checked with the forecast's countries
    if "JURISDICTION" in by_rule:
        unknown = calculate_jurisdiction(bench_input.get("declared_countries", []),
                                         ai_data.get("expected_jurisdiction", []))
        explained = None
        if assessment:
            juris = assessment["jurisdiction"]
            flagged = {same_country(c, policy) for c in juris["unexplained_countries"] + juris["listed_countries"]}
            explained = not any(same_country(c, policy) in flagged for c in unknown)
        status, why = failed_or_explained(
            by_rule["JURISDICTION"]["failed"], explained,
            "Explained: each has a job, asset or declared source behind it, and none is on a risk list.",
            "Not explained: at least one has nothing behind it or is on a risk list.")
        checks.append(_check("benchmark", "JURISDICTION", BENCHMARK_LABELS["JURISDICTION"], "countries", status,
                             (f"{len(unknown)} not usual for his career: {', '.join(unknown)} "
                              f"(flagged when more than {LIMITS['jurisdiction']})") if unknown
                             else "All usual for his career.", why))

    # NEW: PEP status (a PEP always needs a person to look, and a public-office crime story matters more)
    status_text = str(claims.get("pep_status") or "None")
    pep = is_pep(case, policy)
    pep_types = [t for t in types if t["fits"] and t["category"] in settings["pep_crime_categories"]]
    checks.append(_check(
        "benchmark", "PEP_STATUS", BENCHMARK_LABELS["PEP_STATUS"], "pattern", "failed" if pep else "passed",
        f"{status_text}" + (" - a politically exposed person needs enhanced due diligence" if pep else " - not a PEP"),
        ("A public-office crime story also fits his money: "
         + "; ".join(f"{t['type_id']} {t['name']}" for t in pep_types) + ".") if pep and pep_types else ""))
    # Which failures are evidence of their own (from peers or his own figures), rather than the
    # forecast's honest story seen again: only these can be the "second feature" that agrees.
    for item in checks:
        item.setdefault("independent", item["check"] not in FORECAST_DERIVED)
        item["optional"] = item["status"] == "skipped" and item["detail"].startswith("No biggest wealth jump")
    return checks


def describe_piece(case: dict, piece: str | None) -> str:
    """A forecast piece's name in words: 'call2_earnings.asset.0' -> 'asset 1 (Property - Condo, Bishan)'."""
    piece = piece or ""
    parts = piece.split(".")
    assets, jobs = case.get("investments", []), utilities.career_positions(case)
    if len(parts) == 3 and parts[1] == "asset" and parts[2].isdigit() and int(parts[2]) < len(assets):
        return f"asset {int(parts[2]) + 1} ({assets[int(parts[2])].get('asset_type', 'asset')})"
    if len(parts) == 3 and parts[1] == "position" and parts[2].isdigit() and int(parts[2]) < len(jobs):
        job = jobs[int(parts[2])]
        return f"the pay for job {int(parts[2]) + 1} ({job.get('occupation')} at {job.get('employer_name')})"
    names = {"step0_declaration": "reading the declaration", "call1_markets.list": "choosing the markets",
             "call2_earnings.future_pay": "his future pay", "call3_types.list": "the list of person types",
             "call4_events.list": "the future events"}
    if piece in names:
        return names[piece]
    if len(parts) == 3 and parts[0] == "call1_markets":
        return f"the {parts[2]} market figures"
    if len(parts) == 3 and parts[0].startswith("call3_types"):
        return f"how person type {parts[2]} behaves"
    if len(parts) == 3 and parts[0].startswith("call4_events"):
        return f"how person type {parts[2]} reacts to events"
    return piece or "an early step"


def forecast_checks(case: dict, assessment: dict | None, honest: dict | None, types: list[dict],
                    failed_step: str | None, policy: dict, unmodelled: list[dict] | None = None) -> list[dict]:
    """The forecast's checks in the shared shape, plus the two new ones (wealth jump, liquid wealth).
    failed_step: why the forecast stopped, in words (if it did)."""
    if not assessment or not honest:
        return [_check("forecast", "FORECAST", "Forecast", "wealth", "skipped",
                       f"The forecast stopped at {failed_step or 'an early step'}, so the stories couldn't be tested.")]
    worth = case["claims"]["declared_net_worth"]
    t1 = honest["t1"]
    wealth = t1["wealth_now"]
    checks = []
    if not t1["fits"]:
        status, note = "failed", "It doesn't reach the declared wealth."
    elif not t1["fits_at_typical"]:
        status, note = "failed", "It only reaches the declared wealth at the optimistic edge."
    else:
        status, note = "passed", "That covers the declared wealth."
    checks.append(_check("forecast", "NET_WORTH_FIT", "Honest story (T1)", "wealth", status,
                         f"Explains {_sgd(wealth['cautious'])} to {_sgd(wealth['optimistic'])} today (typical "
                         f"{_sgd(wealth['typical'])}); declared {_sgd(worth)}. {note}"))

    assets = case.get("investments", [])
    for row in assessment["affordability"]:
        asset = assets[row["asset_index"]] if row["asset_index"] < len(assets) else {}
        checks.append(_check(
            "forecast", "AFFORDABILITY", f"{row['year']} purchase: {asset.get('asset_type', 'asset')}", "purchases",
            "passed" if row["affordable"] else "failed",
            f"Needed {_sgd(row['needed'])} ({row['payment_method'].replace('_', ' ')}); the honest story had up to "
            f"{_sgd(row['available']['optimistic'])}", group="Purchases"))
    for check in assessment["asset_checks"]:
        asset = assets[check["asset_index"]] if check["asset_index"] < len(assets) else {}
        is_value = check["rule_id"] == "ASSET_VALUE"
        checks.append(_check(
            "forecast", check["rule_id"],
            f"{asset.get('asset_type', 'Asset')}: {'value' if is_value else 'income'}", "wealth",
            "failed" if check["failed"] else "passed",
            (f"Declared {_sgd(check['declared'])}; its markets explain about {_sgd(check['expected']['typical'])} "
             f"(up to {_sgd(check['ceiling'])})") if is_value else
            (f"Declared {_sgd(check['declared'])} a year; normal is about {_sgd(check['expected']['typical'])} "
             f"(up to {_sgd(check['ceiling'])})"),
            group="Asset values" if is_value else "Asset income"))
    for missing in unmodelled or []:
        asset = assets[missing["asset_index"]] if missing["asset_index"] < len(assets) else {}
        checks.append(_check("forecast", "ASSET_NOT_MODELLED", f"{asset.get('asset_type', 'Asset')}: not modelled",
                             "wealth", "skipped",
                             f"The AI's answers about this asset failed the sense checks ({missing['error']}), so it is "
                             f"held at its purchase price {_sgd(asset.get('price_paid'))} and its value and income "
                             "weren't checked.", group="Asset values"))
    juris = assessment["jurisdiction"]
    checks.append(_check("forecast", "JURISDICTION", "Countries behind his wealth", "countries",
                         "failed" if juris["failed"] else "passed",
                         f"With nothing behind them: {', '.join(juris['unexplained_countries']) or 'none'}. "
                         f"On a risk list: {', '.join(juris['listed_countries']) or 'none'}."))
    for source in assessment["declared_sources"]:
        checks.append(_check("forecast", "UNQUANTIFIED_SOURCE", f"Declared source {source['source_id']}", "sources",
                             "failed" if source["failed"] else "passed", source["detail"], group="Declared sources"))

    # NEW: could the honest path make his biggest wealth jump?
    jump, jump_years = _jump_years(case)
    if jump is not None:
        gain, first, last = _best_honest_jump(honest, jump_years)
        fits = jump <= gain * (1 + policy["tolerance"]["net_worth"])
        checks.append(_check("forecast", "WEALTH_JUMP", "Wealth jump vs honest path", "wealth",
                             "passed" if fits else "failed",
                             f"Declared jump {_sgd(jump)} over {jump_years} year(s); the honest story gains at most "
                             f"{_sgd(gain)} in any {jump_years}-year stretch (best {first}-{last})"))
    # NEW: cash and shares the honest story leaves him today (used by "Money brought to the bank")
    liquid = honest["liquid"]
    checks.append(_check("forecast", "LIQUID_WEALTH", "Liquid wealth today", "wealth", "info",
                         f"The honest story leaves him {_sgd(liquid['cautious'])} to {_sgd(liquid['optimistic'])} "
                         f"in cash and listed shares"))
    checks.append(_check("forecast", "STABILITY", "Stability", "wealth",
                         "weak" if assessment["borderline"] else "passed",
                         "Moving every forecast 10% changes the result, so it is borderline."
                         if assessment["borderline"] else "The result holds when every forecast moves 10%."))
    for row in types:
        checks.append(_check("forecast", "CRIME_STORY", f"{row['type_id']} {row['name']}", "pattern", "info",
                             f"{'Fits' if row['fits'] else 'Does not fit'} his money (fit {row['fit_score'] or 0:.2f}"
                             f"{', open ' + row['route_years'] if row['route_years'] else ''})",
                             group="Crime stories"))
    return checks


def crime_checks(crime_result: dict | None, research: dict | None, typologies: list[dict] | None,
                 types: list[dict], forecast_ran: bool) -> list[dict]:
    """The crime scan's patterns in the shared shape: how well each one matches, and which
    person type was built from it."""
    if crime_result is None:
        return [_check("crime", "CRIME_SCAN", "Crime scan", "pattern", "skipped",
                       "The warning signs couldn't be checked (no typologies or no declaration reading).")]
    built = {t["typology_id"]: t for t in types if t["typology_id"]}
    quotes = {t.get("typology_id"): (t.get("source_quotes") or [{}])[0] for t in typologies or []}
    checks = []
    for match in crime_result.get("match_results", []):
        category = match.get("category", "no_match")
        fired = match.get("core_fired", []) + match.get("supporting_fired", [])
        status = "failed" if category in FULL_MATCH else "weak" if category in WEAK_MATCH else "passed"
        signs = ", ".join(f"{SIGN_LABELS.get(s, s)} ({'core' if s in match.get('core_fired', []) else 'supporting'})"
                          for s in fired) or "no warning signs fired"
        person = built.get(match["typology_id"])
        if person:
            link = (f"Built into {person['type_id']} ({person['name']}), which "
                    f"{'fits' if person['fits'] else 'does not fit'} his money.")
        elif forecast_ran:
            link = "No person type uses it: none of his jobs, assets or sources opens this channel."
        else:
            link = ""
        quote = quotes.get(match["typology_id"], {})
        sources = ", ".join(match.get("source_ids", []))
        detail = f"{MATCH_LABELS.get(category, category)} match: {signs}"
        explanation = link + (f' "{quote["quote"]}" [{quote.get("report_id", "")}]' if quote.get("quote") else "")
        check = _check("crime", "TYPOLOGY", f"{match.get('name') or match['typology_id']}"
                       + (f" [{sources}]" if sources else ""), "pattern", status, detail, explanation.strip(),
                       group="Patterns")
        check["signs"] = fired
        check["match"] = category
        check["typology_id"] = match["typology_id"]
        checks.append(check)
    for person in types:
        if not person["typology_id"]:
            checks.append(_check("crime", "UNBACKED_TYPE", f"{person['type_id']} {person['name']}", "pattern", "info",
                                 "Not backed by research: built from his background only.", group="Crime stories"))
    if not crime_result.get("match_results"):
        reports = len((research or {}).get("reports", []))
        checks.append(_check("crime", "NO_PATTERNS", "Known patterns", "pattern", "info",
                             f"No verified patterns for his industry ({reports} research report(s) found)."))
    return checks


# ---------------------------------------------------------------------------
# 5. One verdict
# ---------------------------------------------------------------------------

def _agreement(checks: list[dict]) -> list[dict]:
    """Group the findings that count (failed, or a crime warning sign) by theme, most agreement first."""
    themes: dict = {}
    for check in checks:
        if check["status"] not in ("failed", "weak"):
            continue
        if check["feature"] == "crime" and check.get("signs"):
            keys = {SIGN_THEMES.get(sign, "pattern") for sign in check["signs"]}
            if check["status"] == "failed":
                keys.add("pattern")
        elif check["status"] == "failed":
            keys = {check["theme"]}
        else:
            continue
        for key in keys:
            entry = themes.setdefault(key, {"theme": key, "title": THEMES[key], "features": [], "lines": []})
            if check["feature"] not in entry["features"]:
                entry["features"].append(check["feature"])
            detail = check["detail"]
            if check["feature"] == "crime" and key != "pattern":   # only the warning signs of this theme
                detail = "warning signs fired: " + ", ".join(
                    SIGN_LABELS.get(sign, sign) for sign in check["signs"] if SIGN_THEMES.get(sign) == key)
            entry["lines"].append(f"{check['feature'].capitalize()}: {check['label']} - {detail}")
            entry.setdefault("short", []).append(f"{check['feature'].capitalize()}: {check['label']}")
    order = list(THEMES)
    return sorted(themes.values(), key=lambda t: (-len(t["features"]), order.index(t["theme"])))


def _documents(assessment: dict | None, bench: list[dict], crime: list[dict], typologies: list[dict] | None,
               case: dict, policy: dict, docs: list[dict] | None = None) -> list[dict]:
    """Documents to ask for: first for what his own documents contradict, then the forecast's (failed
    checks, then the ones that rule out the most stories), the benchmark's failed checks, and the
    matched patterns' typical documents."""
    names = utilities.vocabulary().get("document_codes", {})
    settings = policy["verdict"]
    wanted, seen = [], set()

    def add(document: str, reason: str) -> None:
        key = document.strip().lower()
        if key and key not in seen:
            seen.add(key)
            wanted.append({"document": document, "reason": reason})

    kinds = settings["document_evidence"]
    for check in docs or []:
        if check["status"] in ("failed", "weak") and check["check"].lower() in kinds:
            for code in kinds[check["check"].lower()]["documents"]:
                if code in names:
                    add(names[code], f"His documents show: {check['label'].lower()}")
    for document in (assessment or {}).get("documents", []):
        add(document["document"], document["reason"])
    for check in bench:
        if check["status"] != "failed":
            continue
        rule = "COMPOSITION" if check["check"].startswith("COMPOSITION") else check["check"]
        for code in settings["benchmark_documents"].get(rule, []):
            if code in names:
                add(names[code], f"Benchmark check failed: {check['label'].lower()}")
    by_id = {t.get("typology_id"): t for t in typologies or []}
    for check in crime:
        if check["check"] == "TYPOLOGY" and check["status"] in ("failed", "weak"):
            name = check["label"].split(" [")[0]
            for document in by_id.get(check["typology_id"], {}).get("typical_documents", []):
                add(document, f"Matches the known pattern: {name}")
    return wanted[:settings["max_documents"]]


def combined_verdict(case: dict, parts: dict, policy: dict | None = None) -> dict:
    """ONE verdict from the crime scan, the benchmark and the forecast.

    parts: research, typologies (verified list), crime_result, declaration (forecast Step 0),
           benchmark_input, benchmark_ai, benchmark_findings, forecast, forecast_assessment,
           markets (Call 1, even if the forecast stopped later), evidence (the documents layer),
           pay_adjustments (calibrate_pay), failed_step, not_run, notes

    The ladder (config/policy.json "verdict" holds the numbers):
      SERIOUS RISK   the honest story can't explain his wealth AND a crime story can AND another
                     feature agrees (its warning signs fired, his documents support it, or a
                     benchmark failure the forecast can't explain); OR his documents contradict his
                     declaration in several ways AND another feature agrees. One feature alone can
                     never reach Serious Risk.
      RISKY          the honest story fails or only works at the optimistic edge, a purchase he
                     couldn't afford, a crime story that fits whose warning signs fired, money for
                     the bank he can't fund, a PEP with a public-office crime story that fits, or
                     several unexplained benchmark failures
      MANUAL REVIEW  loose ends: an unexplained benchmark failure, other forecast checks failed,
                     a borderline result, a PEP, a known pattern on its own, or a part that couldn't run
      PASSED         everything else
    """
    policy = policy or utilities.policy()
    settings = policy["verdict"]
    forecast, assessment = parts.get("forecast"), parts.get("forecast_assessment")
    honest = None
    if forecast and assessment:
        honest = honest_numbers(case, forecast, assessment, policy)
    types = type_patterns(forecast, assessment, parts.get("crime_result"), parts.get("typologies"))
    evidence = parts.get("evidence")
    docs = document_checks(evidence, types, policy)
    for t in types:                                 # which of his documents support each crime story
        t["documents"] = [c["label"] for c in docs if c["status"] == "failed" and t["category"] in c.get("supports", [])]
    bench = benchmark_checks(case, parts.get("benchmark_input"), parts.get("benchmark_ai"),
                             parts.get("benchmark_findings"), honest, assessment, parts.get("declaration"),
                             parts.get("markets"), types, policy)
    fore = forecast_checks(case, assessment, honest, types, parts.get("failed_step"), policy,
                           (forecast or {}).get("earnings", {}).get("fallback_assets"))
    for adjusted in reversed(parts.get("pay_adjustments") or []):
        fore.insert(1, _check("forecast", "PAY_FROM_DOCUMENTS", "Pay taken from his documents", "wealth", "info",
                              f"The AI estimated {_sgd(adjusted['ai_pay'])} for {adjusted['year']}; "
                              f"{adjusted['source_file'] or 'his documents'} shows {_sgd(adjusted['documented_pay'])}, "
                              f"so his pay as {adjusted['job']} was scaled x{adjusted['ratio']:g} for every year of that job."))
    crime = crime_checks(parts.get("crime_result"), parts.get("research"), parts.get("typologies"), types,
                         forecast is not None)
    checks = bench + fore + crime + docs

    triggers: list[tuple[str, str]] = []           # (level, reason)

    def flag(level: str, reason: str) -> None:
        triggers.append((level, reason))

    unexplained = [c for c in bench if c["status"] == "failed" and c.get("independent")]
    fitting = [t for t in types if t["fits"]]
    backed = [t for t in fitting if t["match"] in FULL_MATCH]
    contradicted = [c for c in docs if c["status"] == "failed"]

    if honest:
        t1 = honest["t1"]
        if not t1["fits"]:
            agrees = ([f"{t['type_id']}'s warning signs fired ({MATCH_LABELS[t['match']]} match)" for t in backed]
                      + [f"his documents support {t['type_id']} ({', '.join(t['documents']).lower()})"
                         for t in fitting if t["documents"]]
                      + [f"the benchmark's {c['label'].lower()} check failed and the forecast can't explain it"
                         for c in unexplained])
            if fitting and agrees:
                flag(SERIOUS_RISK, "The honest story can't explain his wealth, a crime story can ("
                     + "; ".join(f"{t['type_id']} {t['name']}" for t in fitting) + "), and another check agrees: "
                     + "; ".join(agrees[:3]) + ".")
            elif fitting:
                flag(RISKY, "The honest story can't explain his wealth and a crime story can ("
                     + "; ".join(f"{t['type_id']} {t['name']}" for t in fitting)
                     + "), but no other check agrees yet.")
            else:
                flag(RISKY, "No story explains his wealth: neither the honest story nor any crime story reaches "
                     f"{_sgd(case['claims']['declared_net_worth'])}.")
        elif not t1["fits_at_typical"]:
            flag(RISKY, "The honest story only reaches his declared wealth at its optimistic edge.")
        for check in fore:
            if check["check"] == "AFFORDABILITY" and check["status"] == "failed":
                flag(RISKY, f"He couldn't have afforded the {check['label'].split(':')[0]}: {check['detail']}.")
        for t in backed:
            flag(RISKY, f"{t['type_id']} {t['name']} fits his money AND its researched pattern's warning signs "
                        f"fired ({MATCH_LABELS[t['match']]} match).")
        if assessment["borderline"]:
            flag(MANUAL_REVIEW, "The result is borderline: it changes when every forecast moves 10%.")
        other = [c for c in fore if c["status"] == "failed"
                 and c["check"] in ("ASSET_VALUE", "ASSET_INCOME", "JURISDICTION", "UNQUANTIFIED_SOURCE", "WEALTH_JUMP")]
        if other:
            flag(MANUAL_REVIEW, f"{len(other)} forecast check(s) failed: "
                 + "; ".join(c["label"] for c in other[:4])
                 + (f"; and {len(other) - 4} more." if len(other) > 4 else "."))

    # What his own documents show against what he declared
    if contradicted:
        found = "; ".join(c["label"].lower() for c in contradicted)
        others = []
        if honest and not honest["t1"]["fits"]:
            others.append("the honest story can't explain his wealth")
        elif honest and not honest["t1"]["fits_at_typical"]:
            others.append("the honest story only reaches his wealth at its optimistic edge")
        others += [f"{t['type_id']} {t['name']} fits his money" for t in fitting]
        others += [f"the crime scan matched \"{c['label'].split(' [')[0]}\"" for c in crime
                   if c["check"] == "TYPOLOGY" and c["status"] == "failed"]
        others += [f"the benchmark's {c['label'].lower()} check failed" for c in unexplained]
        others += [f"he couldn't have afforded the {c['label'].split(':')[0]}" for c in fore
                   if c["check"] == "AFFORDABILITY" and c["status"] == "failed"]
        if len(contradicted) >= settings["min_serious_document_findings"] and others:
            flag(SERIOUS_RISK, f"His documents contradict his declaration ({len(contradicted)} findings: {found}), "
                               "and another check agrees: " + "; ".join(others[:3]) + ".")
        else:
            flag(RISKY, f"His documents contradict his declaration: {found}.")
    loose = [c for c in docs if c["status"] == "weak"]
    if loose:
        flag(MANUAL_REVIEW, "His documents show things to check: " + "; ".join(c["label"].lower() for c in loose) + ".")

    if any(c["check"] == "AUM_FUNDING" and c["status"] == "failed" for c in bench):
        flag(RISKY, "He plans to bring more money to the bank than his cash and shares can fund.")
    if is_pep(case, policy):
        pep_types = [t for t in fitting if t["category"] in settings["pep_crime_categories"]]
        if pep_types:
            flag(RISKY, "He is a PEP and a public-office crime story fits his money ("
                 + "; ".join(f"{t['type_id']} {t['name']}" for t in pep_types) + ").")
        else:
            flag(MANUAL_REVIEW, f"He is a PEP ({case['claims']['pep_status']}): a KYC officer must review him.")
    counted = [c for c in unexplained if c["check"] != "AUM_FUNDING"]      # AUM has its own rule above
    if len(counted) >= settings["unexplained_benchmark_for_risky"]:
        flag(RISKY, f"{len(counted)} benchmark checks failed that the forecast can't explain: "
             + ", ".join(c["label"].lower() for c in counted) + ".")
    elif counted:
        flag(MANUAL_REVIEW, "Benchmark check(s) failed that the forecast can't explain: "
             + ", ".join(c["label"].lower() for c in counted) + ".")
    built = {t["typology_id"] for t in fitting if t["typology_id"]}
    for check in crime:
        if check["check"] == "TYPOLOGY" and check.get("match") not in (None, "no_match"):
            level = settings["pattern_alone"].get(check["match"], PASSED)
            if level != PASSED and check["typology_id"] not in built:
                flag(level, f"Known pattern \"{check['label'].split(' [')[0]}\": {check['detail']}.")
    for part in parts.get("not_run", []):
        flag(MANUAL_REVIEW, f"{part} couldn't run, so a person must check what it would have covered.")

    level = max((t[0] for t in triggers), key=lambda lv: SEVERITY[lv], default=PASSED)
    reasons = []
    for trigger_level, reason in sorted(triggers, key=lambda t: -SEVERITY[t[0]]):
        if reason not in [r["text"] for r in reasons]:
            reasons.append({"level": trigger_level, "text": reason})
    summary = reasons[0]["text"] if reasons else "Every check came back clean or was explained by another feature."

    # Next review: by the verdict, sooner if the forecast says two stories can be told apart earlier
    current = case["current_date"]
    review = add_months(current, policy["review_interval_months"][level])
    for watch in (assessment or {}).get("watch_dates", []):
        if "told apart" in watch["what"] and current < watch["date"] < review:
            review = watch["date"]

    groups: dict = {}
    for check in bench + fore + docs:
        groups.setdefault((check["feature"], check["group"]), []).append(check["status"])
    clean = [group for (feature, group), statuses in groups.items()
             if statuses and all(s == "passed" for s in statuses)]

    return {
        "client_ref": case["client_ref"],
        "name": case.get("name", ""),
        "assessed_on": current,
        "policy_version": policy["policy_version"],
        "verdict": level,
        "verdict_label": VERDICT_LABELS[level],
        "next_step": policy["routing"][level],
        "next_review_date": review,
        "summary": summary,
        "reasons": reasons,
        "agreement": _agreement(checks),
        "benchmark": bench,
        "forecast": fore,
        "crime": crime,
        "document_findings": docs,
        "types": types,
        "documents": (_documents(assessment, bench, crime, parts.get("typologies"), case, policy, docs)
                      if level != PASSED else []),
        "clean": clean,
        "not_run": list(parts.get("not_run", [])),
        "notes": list(parts.get("notes", [])),
        "evidence": _evidence(case, parts, assessment, honest),
    }


# Notes from the run that are routine bookkeeping, not something the officer must look at
_ROUTINE_NOTES = ("converted to", "renumbered", "kept first")


def _evidence(case: dict, parts: dict, assessment: dict | None, honest: dict | None) -> list[dict]:
    """The detail behind the verdict, shown under "Full evidence"."""
    sections = []
    evidence = parts.get("evidence") or {}
    if evidence.get("files"):
        lines = [f"Read: {', '.join(evidence['files'])}"]
        lines += [f"Pay shown for {p['year']}: {_sgd(p['amount'])} ({p['source_file'] or 'documents'})"
                  for p in evidence.get("documented_pay", [])]
        lines += [f"{f['summary']} ({', '.join(f['source_files'])})" for f in evidence.get("findings", [])]
        if not evidence.get("findings"):            # the findings already say what the notes say
            lines += [f"Reader's note: {note}" for note in evidence.get("notes", [])]
        sections.append({"title": "What his documents show", "lines": lines})
    reports = (parts.get("research") or {}).get("reports", [])
    sections.append({"title": "Research reports", "lines": [
        f"[{r.get('report_id', '?')}] {r.get('organisation', '')}{' (' + str(r['year']) + ')' if r.get('year') else ''}"
        f" - {r.get('title', '')}" for r in reports] or ["No research reports were found."]})
    typology_lines = []
    for t in parts.get("typologies") or []:
        typology_lines.append(f"{t.get('name')} ({category_name(t.get('crime_category', ''))})")
        if t.get("description"):
            typology_lines.append(f"    {t['description']}")
    sections.append({"title": "Sector typologies", "lines": typology_lines or ["No verified typologies."]})
    if assessment and honest:
        sections.append({"title": "Person types", "lines": [
            f"{row['type_id']} {row['name']} - {'fits' if row['fits'] else 'does not fit'} "
            f"(fit {row['fit_score']:.2f}); wealth today "
            f"{_sgd(row['wealth_now']['cautious'])} to {_sgd(row['wealth_now']['optimistic'])}; route {row['route']}"
            for row in assessment["types"]]})
        documented = {p["year"]: p for p in evidence.get("documented_pay", [])}
        rows = honest["savings_rows"]
        shown = [row for i, row in enumerate(rows) if i % 5 == 0 or i == len(rows) - 1 or row["year"] in documented]
        sections.append({"title": "Career savings (typical, every 5 years)", "lines": [
            f"{row['year']}: pay {_sgd(row['total_pay']['typical'])}"
            + (f" (documents show {_sgd(documented[row['year']]['amount'])})" if row["year"] in documented else "")
            + f", saved {_sgd(row['saved']['typical'])}, savings so far {_sgd(row['savings']['typical'])}"
            for row in shown]})
        events = {}
        for row in assessment["event_impacts"]:
            events.setdefault((row["year"], row["event_id"], row["event"], row["chance"]), []).append(
                f"{row['type_id']} {'+' if row['impact'] >= 0 else ''}{_sgd(row['impact'])}")
        sections.append({"title": "Future events and how each story's 2030 wealth would change", "lines": [
            f"{year} {name} ({chance:.0%} chance): " + ", ".join(impacts)
            for (year, event_id, name, chance), impacts in sorted(events.items())] or ["No events."]})
        sections.append({"title": "When the stories can be told apart", "lines": [
            f"T1 and {d['type_id']}: " + (f"from {d['distinguishable_from']}" if d["distinguishable_from"]
                                         else "not within the forecast years (his wealth alone won't tell them apart)")
            for d in assessment["divergence"]] or ["No crime stories to tell apart."]})
        names = utilities.vocabulary().get("signal_codes", {})
        sections.append({"title": "Normal account activity next year (honest story)", "lines": [
            f"{names.get(code, code)}: up to {_sgd(r['amount_max'])} a year"
            + (f" in up to {r['count_max']:g} payments" if r["count_max"] >= 2 else "")
            for code, r in assessment["monitoring_ranges"].items()] or ["None forecast."]})
        quality = assessment["data_quality"]
        sections.append({"title": "Data quality", "lines": [
            f"{quality['market_figures']} market figures, {quality['unverified_share']:.0%} from AI memory "
            "(not checked online) or missing."] + [f"Gap: {gap}" for gap in quality["data_gaps"]]})
        sections.append({"title": "Stability test (the forecast's own result)", "lines": [
            f"Forecasts {shift}: {VERDICT_LABELS.get(outcome, outcome)}"
            for shift, outcome in assessment["stability"].items()]})
    warnings = list(dict.fromkeys(n for n in parts.get("notes", [])
                                  if not any(word in n.lower() for word in _ROUTINE_NOTES)))
    if warnings:
        sections.append({"title": "Warnings from the run", "lines": warnings})
    return sections


# ---------------------------------------------------------------------------
# 6. The report (the command line prints it, the web app shows it: the same text)
# ---------------------------------------------------------------------------

MARKS = {"failed": "fail", "weak": "weak", "explained": "explained", "passed": "pass", "skipped": "skip", "info": "info"}
# How each mark is shown, by the command line and the web app alike
REPORT_LEGEND = [
    {"mark": "fail", "symbol": "\u2717", "meaning": "counts towards the verdict"},
    {"mark": "explained", "symbol": "~", "meaning": "failed, but another feature explains it"},
    {"mark": "weak", "symbol": "!", "meaning": "loose end"},
    {"mark": "pass", "symbol": "\u2713", "meaning": "clean"},
    {"mark": "skip", "symbol": "-", "meaning": "couldn't run"},
    {"mark": "info", "symbol": "\u2022", "meaning": "information"},
]


def _items(checks: list[dict], statuses: tuple) -> list[dict]:
    return [{"mark": MARKS[c["status"]], "text": f"{c['label']}: {c['detail']}",
             "sub": [c["explanation"]] if c["explanation"] else []}
            for c in checks if c["status"] in statuses and not c.get("optional")]


# The benchmark's 9 original checks: always shown in the report, passed or not, in this order
ORIGINAL_BENCHMARK = ["TOTAL_WEALTH", "LIQUIDITY", "COMPOSITION_PROPERTY", "COMPOSITION_EQUITIES",
                      "COMPOSITION_BUSINESS", "COMPOSITION_CASH", "VELOCITY", "COUNTERPARTIES", "JURISDICTION"]


def _benchmark_items(checks: list[dict]) -> list[dict]:
    """The benchmark section: the 9 original checks every time (passed, failed, explained or not
    run, each with its numbers), then any of the newer checks that failed or need a look."""
    by_rule = {c["check"]: c for c in checks}
    items = []
    for rule in ORIGINAL_BENCHMARK:
        check = by_rule.get(rule)
        if check is None:
            items.append({"mark": "skip", "text": f"{BENCHMARK_LABELS[rule]}: couldn't run - the peer benchmark "
                                                  "didn't finish, so this wasn't compared with peers.", "sub": []})
            continue
        items.append({"mark": MARKS[check["status"]], "text": f"{check['label']}: {check['detail']}",
                      "sub": [check["explanation"]] if check["explanation"] else []})
    newer = [c for c in checks if c["check"] not in ORIGINAL_BENCHMARK and c["check"] != "PEER_BENCHMARK"]
    return items + _items(newer, ("failed", "explained", "weak", "skipped"))


def verdict_report(verdict: dict) -> dict:
    """The report as plain data: a header, short sections of marked lines, and the full evidence.
    io_manager prints it and the web app's template shows it, so both give exactly the same content.

    The benchmark section always lists its 9 original checks, passed or not. The other sections show
    only what matters: failures, what another feature explained, loose ends and what couldn't run;
    their passed checks are listed under "What came back clean"."""
    level_marks = {SERIOUS_RISK: "fail", RISKY: "fail", MANUAL_REVIEW: "weak", PASSED: "pass"}
    why = [{"mark": level_marks[r["level"]], "text": f"[{VERDICT_LABELS[r['level']]}] {r['text']}", "sub": []}
           for r in verdict["reasons"]] or [{"mark": "pass", "text": verdict["summary"], "sub": []}]
    why += [{"mark": "fail", "text": f"Checks agree - {a['title']} ({len(a['features'])} features: "
                                     f"{', '.join(a['features'])})",
             "sub": ["; ".join(dict.fromkeys(a.get("short", a["lines"])))]}
            for a in verdict["agreement"] if len(a["features"]) > 1]

    forecast = _items(verdict["forecast"], ("failed", "weak", "explained", "skipped"))
    forecast += [{"mark": "info", "text": f"{c['label']}: {c['detail']}", "sub": []} for c in verdict["forecast"]
                 if c["check"] == "PAY_FROM_DOCUMENTS"
                 or (c["check"] == "CRIME_STORY" and c["detail"].startswith("Fits"))]
    forecast += [{"mark": "pass", "text": f"{c['label']}: {c['detail']}", "sub": []} for c in verdict["forecast"]
                 if c["check"] == "STABILITY" and c["status"] == "passed"]
    crime = _items(verdict["crime"], ("failed", "weak", "skipped"))
    documents = _items(verdict["document_findings"], ("failed", "weak", "skipped", "info", "passed"))
    for item, check in zip(documents, [c for c in verdict["document_findings"]
                                       if c["status"] in ("failed", "weak", "skipped", "info", "passed")]):
        if check.get("sources"):
            item["sub"] = item["sub"] + [f"From: {', '.join(check['sources'])}"]

    sections = [
        {"title": "Why", "items": why},
        {"title": "Benchmark - compared with peers in the same economy",
         "items": _benchmark_items(verdict["benchmark"])},
        {"title": "Forecast - what it predicted",
         "items": forecast or [{"mark": "pass", "text": "Every forecast check passed.", "sub": []}]},
        {"title": "Crime - known patterns for his industry",
         "items": crime or [{"mark": "pass", "text": "No known pattern matched his declaration.", "sub": []}]},
        {"title": "Documents - what his papers show", "items": documents},
        {"title": "Documents to request", "items": [
            {"mark": "info", "text": f"{n}. {d['document']}", "sub": [d["reason"]]}
            for n, d in enumerate(verdict["documents"], 1)]
         or [{"mark": "pass", "text": "None.", "sub": []}]},
        {"title": "What came back clean", "items": [{"mark": "pass", "text": group, "sub": []}
                                                    for group in verdict["clean"]]
         or [{"mark": "info", "text": "Nothing came back fully clean.", "sub": []}]},
    ]
    if verdict["not_run"]:
        sections.append({"title": "Could not run", "items": [{"mark": "skip", "text": part, "sub": []}
                                                             for part in verdict["not_run"]]})
    return {
        "header": {"client_ref": verdict["client_ref"], "name": verdict["name"],
                   "assessed_on": verdict["assessed_on"], "verdict": verdict["verdict"],
                   "verdict_label": verdict["verdict_label"], "next_step": verdict["next_step"],
                   "next_review_date": verdict["next_review_date"], "summary": verdict["summary"],
                   "policy_version": verdict["policy_version"]},
        "sections": sections,
        "evidence": verdict["evidence"],
        "evidence_titles": [part["title"] for part in verdict["evidence"]],
        "legend": REPORT_LEGEND,
    }