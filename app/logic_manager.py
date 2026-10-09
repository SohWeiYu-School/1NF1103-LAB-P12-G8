import re

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


# ---------------------------------------------------------------------------
# Forecasting Section
# ---------------------------------------------------------------------------

# INSERT FORECASTING LOGIC BELOW
