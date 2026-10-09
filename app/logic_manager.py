"""
logic_manager.py - the logic layer: every calculation and every decision.

The sections run in this order for a new client:

    1. Benchmark          run_benchmark: declared figures vs the AI's single expected
                          values (total wealth, liquidity, composition) -> PASS / FAIL
    2. Sector Crime Scan  decide_outcome, build_document_requests, assess_case
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

import datetime
import logging
import re

from app import common
from app.ai_manager import get_ai_data
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


def run_benchmark(client_data: dict):
    """Run all six benchmark checks for a client."""

    # Ask AI for the expected benchmark values
    ai_data = get_ai_data(client_data)

    if ai_data is None:
        print("\n[Benchmark] AI call failed.")
        return None

    # 1. TOTAL WEALTH CHECK
    result1 = calculate_total_wealth(client_data, ai_data)

    print("\n--- Total Wealth Check ---")
    print("Declared Net Worth:", client_data["declared_net_worth"])
    print("Expected Wealth:", ai_data["expected_wealth"])
    print("Ratio:", round(result1, 2))

    if result1 <= LIMITS["total_wealth"]:
        print("Result: PASS")
    else:
        print("Result: FAIL")

    # 2. LIQUIDITY CHECK
    result2 = calculate_liquidity(client_data, ai_data)

    print("\n--- Liquidity Check ---")
    print(
        "Declared Liquidity:",
        client_data["listed_equities"] + client_data["cash"], "%"
    )
    print("Expected Liquidity:", ai_data["expected_liquidity"], "%")
    print("Ratio:", round(result2, 2))

    if result2 <= LIMITS["liquidity"]:
        print("Result: PASS")
    else:
        print("Result: FAIL")

    # 3. ASSET COMPOSITION CHECK
    
    calculate_composition(client_data, ai_data)

    # 4. VELOCITY CHECK
    print("\n--- Velocity Check ---")

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

        print("Actual Velocity:", round(actual_velocity, 2))
        print("Expected Velocity:", expected_velocity)
        print("Ratio:", round(result3, 2))

        if result3 <= LIMITS["velocity"]:
            print("Result: PASS")
        else:
            print("Result: FAIL")
    else:
        print("Result: SKIPPED - Missing or invalid wealth timeline data")

    # 5. COUNTERPARTIES CHECK
    print("\n--- Counterparties Check ---")

    declared_counterparties = client_data.get(
        "declared_counterparties", []
    )
    expected_counterparties = ai_data.get(
        "expected_counterparties", []
    )

    unknown_counterparties = calculate_counterparties(
        declared_counterparties,
        expected_counterparties
    )

    print("Declared Counterparties:", declared_counterparties)
    print("Expected Counterparties:", expected_counterparties)
    print("Unknown Counterparties:", unknown_counterparties)

    if len(unknown_counterparties) <= LIMITS["counterparties"]:
        print("Result: PASS")
    else:
        print("Result: FAIL")

    # 6. JURISDICTION CHECK
    print("\n--- Jurisdiction Check ---")

    declared_countries = client_data.get("declared_countries", [])
    expected_countries = ai_data.get("expected_jurisdiction", [])

    unknown_countries = calculate_jurisdiction(
        declared_countries,
        expected_countries
    )

    print("Declared Countries:", declared_countries)
    print("Expected Countries:", expected_countries)
    print("Unknown Countries:", unknown_countries)

    if len(unknown_countries) <= LIMITS["jurisdiction"]:
        print("Result: PASS")
    else:
        print("Result: FAIL")

    
# ---------------------------------------------------------------------------
# Sector Crime Scan Section
# ---------------------------------------------------------------------------

def _normalise_text(text: str) -> str:
    """Lowercase, collapse whitespace, unify quote marks and dashes for substring matching."""
    text = text.lower()
    for ch in '\u201c\u201d':   # " "  → "
        text = text.replace(ch, '"')
    for ch in '\u2018\u2019':   # ' '  → '
        text = text.replace(ch, "'")
    text = text.replace('\u2013', '-').replace('\u2014', '-')   # en/em dash → -
    return re.sub(r'\s+', ' ', text).strip()


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

    clean_research = {
        "reports": kept,
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


# ---------------------------------------------------------------------------
# Country normalisation helpers
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


# ---------------------------------------------------------------------------
# Typology indicator checks (pure, no I/O)
# ---------------------------------------------------------------------------

_RELATED_PARTY_KEYWORDS = [
    "associate", "family", "relative", "friend",
    "brother", "sister", "spouse", "wife", "husband",
    "son", "daughter", "parent", "mother", "father",
    "partner", "in-law",
]


def _check_offshore_entity(
    declaration: dict,
    expected_jurisdictions: list[str],
    all_jurisdictions: list[str],
) -> bool:
    """True if any declared source or known case jurisdiction is outside expected list."""
    if not expected_jurisdictions:
        return False
    for src in declaration.get("sources", []):
        j = src.get("jurisdiction")
        if j and j not in expected_jurisdictions:
            return True
    return any(j not in expected_jurisdictions for j in all_jurisdictions)


def _check_counterparty_unnamed(declaration: dict, policy: dict) -> bool:
    """True if the count of null-counterparty sources meets or exceeds the policy limit."""
    limit = policy.get("thresholds", {}).get("unknown_counterparty_limit", 1)
    count = sum(1 for s in declaration.get("sources", []) if s.get("counterparty") is None)
    return count >= limit


def _check_rapid_accumulation(clean_case: dict, max_accumulation: float) -> bool:
    """True if declared_net_worth / career_years exceeds max_accumulation."""
    nw = clean_case.get("declared_net_worth_sgd")
    start = clean_case.get("career_start_year")
    ref = clean_case.get("reference_year")
    if nw is None or start is None or ref is None:
        return False
    years = ref - start
    if years <= 0:
        return False
    return (nw / years) > max_accumulation


def _check_unvalued_source(declaration: dict) -> bool:
    """True if any declaration source has amount_sgd = null."""
    return any(s.get("amount_sgd") is None for s in declaration.get("sources", []))


def _check_related_party(declaration: dict) -> bool:
    """True if any counterparty contains a related-party keyword."""
    for src in declaration.get("sources", []):
        cp = src.get("counterparty")
        if cp and any(kw in cp.lower() for kw in _RELATED_PARTY_KEYWORDS):
            return True
    return False


def match_typologies(
    typologies: list[dict],
    declaration: dict,
    clean_case: dict,
    expected_jurisdictions: list[str],
    max_accumulation: float,
    policy: dict,
) -> list[dict]:
    """Match each typology's indicators against client data. Pure function.

    A typology breaches when ALL its core indicators fire.
    Returns a list of match result dicts, one per typology.
    """
    indicator_results = {
        "offshore_entity_present": _check_offshore_entity(
            declaration, expected_jurisdictions,
            clean_case.get("all_jurisdictions", []),
        ),
        "counterparty_unnamed": _check_counterparty_unnamed(declaration, policy),
        "rapid_accumulation": _check_rapid_accumulation(clean_case, max_accumulation),
        "unvalued_source_present": _check_unvalued_source(declaration),
        "related_party_transaction": _check_related_party(declaration),
    }

    matches: list[dict] = []
    for typ in typologies:
        indicators = typ.get("indicators", [])
        core = [i["indicator"] for i in indicators if i.get("weight") == "core"]
        supporting = [i["indicator"] for i in indicators if i.get("weight") == "supporting"]
        core_fired = [i for i in core if indicator_results.get(i, False)]
        supporting_fired = [i for i in supporting if indicator_results.get(i, False)]
        breached = len(core_fired) == len(core) and len(core) > 0

        matches.append({
            "typology_id": typ.get("typology_id", ""),
            "name": typ.get("name", ""),
            "breached": breached,
            "core_indicators": core,
            "core_fired": core_fired,
            "supporting_indicators": supporting,
            "supporting_fired": supporting_fired,
            "typical_documents": typ.get("typical_documents", []),
        })
    return matches


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
    case_input: dict,
    typology_response: dict | None,
    declaration: dict | None,
    policy: dict,
) -> dict:
    """Run the full Sector Crime Scan assessment for a single case.

    Returns a dict with keys: outcome, findings, documents, match_results.
    """
    if typology_response is None or declaration is None:
        max_slots = policy.get("typology_max_patterns", 5)
        return {"breach_count": 0, "pass_count": max_slots, "max_slots": max_slots, "match_results": []}

    clean_case, _ = prepare_case_for_matching(case_input)
    typologies = typology_response.get("sector_typologies", [])
    expected_jur = typology_response.get("expected_jurisdictions", [])
    max_accum = typology_response.get("max_accumulation_per_year_sgd", 0)

    match_results = match_typologies(
        typologies, declaration, clean_case,
        expected_jur, max_accum, policy,
    )

    max_slots = policy.get("typology_max_patterns", 5)
    breach_count = sum(1 for mr in match_results if mr["breached"])
    pass_count = max_slots - breach_count

    return {
        "breach_count": breach_count,
        "pass_count": pass_count,
        "max_slots": max_slots,
        "match_results": match_results,
    }


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
    return common.vocabulary().get("crime_categories", {}).get(code, code)


def enabled_by_text(person_type: dict, record: dict) -> str:
    """What makes this type possible for him, in words: the job, asset or declared source."""
    if person_type["type_id"] == common.BASELINE_TYPE:
        return "everything he declared"
    parts = []
    jobs = common.career_positions(record)
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
    return {market_id: {row["year"]: {s: row[s] for s in common.SCENARIOS} for row in series["outlook"]}
            for market_id, series in forecast["markets"]["series"].items()}


def scenario_chances(forecast: dict) -> dict:
    """{year: {scenario: share}} with the shares adding up to exactly 1."""
    chances = {}
    for row in forecast["markets"]["scenario_chances"]:
        total = sum(row[s] for s in common.SCENARIOS) or 1
        chances[row["year"]] = {s: row[s] / total for s in common.SCENARIOS}
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
    for level in common.RANGE_KEYS:      # one path using all the low inputs, one typical, one high
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
    return {"values": {y: sorted_band([results[level][0][y] for level in common.RANGE_KEYS]) for y in years},
            "income": {y: sorted_band([results[level][1][y] for level in common.RANGE_KEYS]) for y in years}}


def all_asset_paths(record: dict, forecast: dict, last_year: int, scenario: str,
                    shocks: dict | None = None) -> list[dict]:
    history, outlook = market_history(forecast), market_outlook(forecast)
    _, end = common.career_years(record)
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
    _, end = common.career_years(record)
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
    behind = {same_country(job.get("country", ""), policy) for job in common.career_positions(record)}
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
    net_worth_fits = declared <= upper and (type_id == common.BASELINE_TYPE or declared >= lower)
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
    t1 = fits[common.BASELINE_TYPE]
    others_fit = [t for t, result in fits.items() if t != common.BASELINE_TYPE and result["fits"]]
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
    _, end = common.career_years(record)
    declared = record["claims"]["declared_net_worth"]
    behaviours = forecast["types"]["behaviour"]
    wealth_now = type_wealth_now(record, forecast, savings_rows, paths)

    asset_values = check_asset_values(record, forecast, paths, end, policy, scale)
    asset_income = check_asset_income(record, paths, end, policy, scale)
    asset_checks = asset_values + asset_income
    affordability = {t: check_affordability(record, savings_rows, paths, b, policy, scale)
                     for t, b in behaviours.items()}
    fits = {t: fit_for_type(t, wealth_now[t], affordability[t], asset_checks, declared, policy, scale)
            for t in behaviours}

    jurisdiction = check_jurisdictions(record, forecast, policy)
    sources = check_declared_sources(forecast, policy)
    t1_breach = not all(row["affordable"] for row in affordability[common.BASELINE_TYPE])
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
    _, end = common.career_years(record)
    years = common.future_years(record)
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
    by_scenario = {s: forward_paths(record, forecast, savings_rows, s) for s in common.SCENARIOS}
    expected = {}
    for type_id in forecast["types"]["behaviour"]:
        expected[type_id] = {}
        for year in by_scenario["steady"][type_id]:
            weights = chances.get(year, {"steady": 1.0, "boom": 0.0, "downturn": 0.0})
            expected[type_id][year] = band(*(sum(weights[s] * by_scenario[s][type_id][year][b]
                                                 for s in common.SCENARIOS) for b in BANDS))
    return expected


def event_impacts(record: dict, forecast: dict, savings_rows: list[dict]) -> list[dict]:
    """For each event and type: change in 2030 wealth if it happens, and weighted by its chance."""
    last_year = common.future_years(record)[-1]
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
    baseline = paths[common.BASELINE_TYPE]
    rows = []
    for type_id, points in paths.items():
        if type_id == common.BASELINE_TYPE:
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
    for signal in forecast["types"]["behaviour"][common.BASELINE_TYPE]["signals"]:
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
    names = common.vocabulary().get("document_codes", {})
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
    policy = policy or common.policy()
    start, end = common.career_years(record)
    years = common.future_years(record)
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
    t1 = main["fits"][common.BASELINE_TYPE]
    declared = record["claims"]["declared_net_worth"]
    others_fit = [t for t, f in main["fits"].items() if t != common.BASELINE_TYPE and f["fits"]]
    reasons = [f"Declared net worth {declared:,.0f}; the honest version explains "
               f"{t1['wealth_now']['cautious']:,.0f} to {t1['wealth_now']['optimistic']:,.0f}."]
    if not t1["fits"]:
        reasons.append("The honest version does not fit his figures.")
    if others_fit:
        reasons.append("Crime types that do fit: " + "; ".join(
            f"{t} {category_name(names[t]['category'])} via {enabled_by_text(names[t], record)} "
            f"({forecast['types']['behaviour'][t]['route_open_from']}-"
            f"{forecast['types']['behaviour'][t]['route_open_to']})" for t in others_fit) + ".")
    for row in main["affordability"][common.BASELINE_TYPE]:
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
                              for row in main["affordability"][common.BASELINE_TYPE] if not row["affordable"]]
    if not t1["fits"]:
        affordability_failures.insert(0, {"rule_id": "NET_WORTH", "detail": reasons[0]})
    candidates = [common.BASELINE_TYPE] + others_fit
    if len(candidates) == 1 and outcome != PASSED:
        candidates = [t for t, b in forecast["types"]["behaviour"].items() if b["route_open_to"] >= end]
    documents = []
    if outcome != PASSED:
        documents = document_request(affordability_failures + failures, forecast, candidates, policy)

    # The future
    expected = expected_paths(record, forecast, savings_rows)
    divergence = divergence_years(expected)
    watch_dates = sorted(
        [{"date": f"{d['distinguishable_from']}-01-01", "what": f"{common.BASELINE_TYPE} and {d['type_id']} "
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
        "affordability": main["affordability"][common.BASELINE_TYPE],
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
    chances = scenario_chances(forecast).get(year, {s: 1 / 3 for s in common.SCENARIOS})
    distance = {}
    for scenario in common.SCENARIOS:
        gaps = [abs(move["change_pct"] - outlook[move["market_id"]][year][scenario]["typical"])
                for move in market_moves
                if move["change_pct"] is not None and year in outlook.get(move["market_id"], {})]
        distance[scenario] = sum(gaps) / len(gaps) if gaps else None
    known = [s for s in common.SCENARIOS if distance[s] is not None]
    if not known:
        return max(common.SCENARIOS, key=lambda s: chances[s])
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
    policy = policy or common.policy()
    start, onboarding_year = common.career_years(record)
    year = common.current_year(review)
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
    linked = {same_country(job.get("country", ""), policy) for job in common.career_positions(record)}
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

    t1_growth = growth[common.BASELINE_TYPE]
    tolerance = policy["tolerance"]["review_growth"]
    a = not (t1_growth["cautious"] - abs(t1_growth["cautious"]) * tolerance <= actual_growth
             <= t1_growth["optimistic"] + abs(t1_growth["optimistic"]) * tolerance)
    leader = max(scores, key=lambda t: (scores[t], t == common.BASELINE_TYPE))
    b = leader != common.BASELINE_TYPE and scores[leader] > scores[common.BASELINE_TYPE]
    c = bool(counter_hits)
    d = bool(unusual)
    outcome = decide_review(a, b, c, d)

    findings = [
        {"rule_id": "A_GROWTH", "failed": a,
         "detail": f"Wealth grew {actual_growth:,.0f} since onboarding; the honest version ({scenario} year) "
                   f"grows {t1_growth['cautious']:,.0f} to {t1_growth['optimistic']:,.0f}."},
        {"rule_id": "B_OTHER_TYPE_LEADS", "failed": b,
         "detail": f"Highest running score: {leader} ({scores[leader]:.0%}); honest: "
                   f"{scores[common.BASELINE_TYPE]:.0%}."},
        {"rule_id": "C_INCOME_AGAINST_MARKET", "failed": c,
         "detail": "; ".join(f"{h['signal_code']} {h['received']:,.0f} (forecast up to {h['ceiling']:,.0f}) "
                             f"while {', '.join(h['markets_fell'])} fell" for h in counter_hits) or "none"},
        {"rule_id": "D_UNUSUAL_PAYMENTS", "failed": d, "detail": "; ".join(unusual) or "none"},
    ]
    candidates = [t for t in scores if scores[t] >= scores[common.BASELINE_TYPE]]
    documents = []
    if outcome != PASSED and len(candidates) > 1:
        names = common.vocabulary().get("document_codes", {})
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