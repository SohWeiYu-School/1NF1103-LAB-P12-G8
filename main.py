"""
main.py - runs the Source of Wealth screening system. It only coordinates: the four
managers never import each other's work, everything is passed through here.

    python main.py                          interactive menu
    python main.py --case data/sample/case_001.json
                                            forecast one sample case (and its reviews)
    python main.py --all-samples            forecast every sample case in the forecasting format
    python main.py --all-samples --demo     the same, using the recorded AI replies in tests/golden
                                            (for machines with no API key, e.g. CI)
    python main.py --crime-scan-sample      crime scan of data/sample/case_daniel_tan.json

Flow for a client (menu options 1 and 3):
    1. io_manager      New Client form -> saved to MongoDB (client_cases)
    2. Crime scan      ai_manager: research, sector typologies, declaration reading
    3. Benchmark       logic_manager.run_benchmark (AI expected values -> PASS / FAIL)
       -> crime scan + benchmark saved to MongoDB (ai_assessments)
    4. Forecasting     io_manager turns the client record into a forecasting case
                       -> ai_manager forecasts (Calls 1-4) -> logic_manager decides
                       -> forecast, decision and AI replies saved to MongoDB
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE, ".env"))  # read .env before ai_manager reads its settings

from app import ai_manager, common, data_manager, io_manager, logic_manager  # noqa: E402

SAMPLE_CLIENT_PATH = os.path.join(_BASE, "data", "sample", "case_daniel_tan.json")
SAMPLE_BENCHMARK_PATH = os.path.join(_BASE, "data", "sample", "case_benchmark.json")


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _whole_number(value: object) -> int | None:
    """'16,500,000' / '30%' / 30 -> a whole number, or None if it can't be read."""
    try:
        return int(float(str(value).replace(",", "").replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


def _configure_logging() -> None:
    """Route all logging (including httpx) to logs/app.log. Nothing goes to the console."""
    log_dir = os.path.join(_BASE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "app.log"))
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# 1-2. Sector Crime Scan
# ---------------------------------------------------------------------------

def crime_scan_input(client_record: dict) -> dict:
    """Map the client record (DB fields) to the crime scan input."""
    profile = client_record["client_profile"]
    return {
        "client_ref":        client_record["client_ref"],
        "occupation":        profile["latest_occupation"],
        "industry":          profile["latest_industry"],
        "age":               _whole_number(profile["age"]),
        "career_start_year": _whole_number(profile["career_start_year"]),
        "country":           profile["country_of_residence"],
        "country_of_residence": profile["country_of_residence"],
        "declaration_text":  client_record["sow_declaration"]["declaration_text"],
    }


def run_crime_scan(case_input: dict) -> tuple[dict | None, dict | None, dict | None]:
    """Research, verify, match typologies and parse declaration. Returns (research, typologies, declaration)."""
    policy = _load_json(os.path.join(_BASE, "config", "policy.json"))

    # Step 1: Research
    io_manager.show_message("\nSearching trusted sources for typology reports...")
    research, res_warnings = ai_manager.get_research(case_input)
    for w in res_warnings:
        io_manager.show_error(w)
    if research is not None:
        io_manager.show_research_output(research)

    # Step 2: Verify research
    if research is not None:
        clean_research, verify_warnings = logic_manager.verify_research(research, policy)
        for w in verify_warnings:
            io_manager.show_error(w)
    else:
        clean_research = None

    # Step 3: Typologies
    io_manager.show_message("Calling AI for sector typologies...")
    typology_response, typ_warnings = ai_manager.get_typologies(case_input)
    for w in typ_warnings:
        io_manager.show_error(w)

    # Step 4: Verify typology source citations
    if typology_response is not None and clean_research is not None:
        verified_typs, typ_verify_warnings = logic_manager.verify_typology_sources(
            typology_response.get("sector_typologies", []), clean_research, policy,
        )
        for w in typ_verify_warnings:
            io_manager.show_error(w)
        typology_response = {**typology_response, "sector_typologies": verified_typs}

    # Step 5: Declaration
    io_manager.show_message("Calling AI to parse declaration...")
    declaration, decl_warnings = ai_manager.get_declaration(case_input)
    for w in decl_warnings:
        io_manager.show_error(w)

    # Step 6: Normalise declaration jurisdictions
    if declaration is not None:
        declaration, norm_warnings = logic_manager.normalize_declaration_jurisdictions(declaration)
        for w in norm_warnings:
            io_manager.show_error(w)

    # Steps 7-8: Match typologies and display result
    if typology_response is not None and declaration is not None:
        io_manager.show_ai_output(case_input, typology_response, declaration)
        crime_result = logic_manager.assess_case(case_input, typology_response, declaration, policy)
        io_manager.show_crime_scan_result(crime_result)

    return research, typology_response, declaration


def _run_sample_assessment() -> None:
    """Crime scan of the hardcoded sample client (python main.py --crime-scan-sample)."""
    raw = _load_json(SAMPLE_CLIENT_PATH)
    profile = raw["client_profile"]

    # Field name mapping: sample JSON uses different keys from the internal schema.
    #   latest_occupation  → occupation
    #   latest_industry    → industry
    #   country_of_residence is kept for the get_typologies fallback
    #   sow_declaration.text → declaration_text
    case_input = {
        "client_ref": profile["client_ref"],
        "occupation": profile["latest_occupation"],
        "industry": profile["latest_industry"],
        "age": profile["age"],
        "career_start_year": profile["career_start_year"],
        "country": profile["country_of_residence"],
        "country_of_residence": profile["country_of_residence"],
        "declaration_text": raw["sow_declaration"]["text"],
    }
    run_crime_scan(case_input)


# ---------------------------------------------------------------------------
# 3. Benchmark
# ---------------------------------------------------------------------------

def run_benchmark_for(client_record: dict, case_input: dict) -> dict | None:
    """The benchmark check on the declared net worth and asset split."""
    comp = client_record.get("asset_composition", {})
    decl = client_record.get("client_declarations", {})
    benchmark_input = {
        **case_input,
        "declared_net_worth": _whole_number(decl.get("declared_net_worth")),
        "listed_equities": _whole_number(comp.get("listed_equities")),
        "cash": _whole_number(comp.get("cash")),
        "property": _whole_number(comp.get("property")),
        "private_business": _whole_number(comp.get("private_business")),
        "biggest_wealth_jump": _whole_number(decl.get("biggest_wealth_jump")),
        "wealth_jump_years": float(decl.get("wealth_jump_years") or 0),
        "declared_counterparties": decl.get("declared_counterparties", []),
        "declared_countries": decl.get("declared_countries", []),
    }

    unreadable = [k for k in ("declared_net_worth", "listed_equities", "cash", "property", "private_business")
                  if benchmark_input[k] is None]
    if unreadable:
        io_manager.show_error(f"Benchmark skipped: can't read {', '.join(unreadable)} from the client record.")
        return None
    return logic_manager.run_benchmark(benchmark_input)


# ---------------------------------------------------------------------------
# 4. Forecasting
# ---------------------------------------------------------------------------

def assess_case(case: dict, client: ai_manager.AIClient) -> tuple[dict | None, dict | None]:
    """Forecast and decide one case. Returns (forecast, onboarding assessment)."""
    ref = case["client_ref"]
    cache = data_manager.load_raw_replies(ref)
    io_manager.show(f"\nForecasting {ref} ({len(cache)} saved AI replies found)...")
    run = ai_manager.run_onboarding_forecast(client, case, cache)
    data_manager.save_raw_replies(ref, run["results"])
    io_manager.show_ai_progress(run["results"])
    if not run["ok"]:
        io_manager.show_error(f"Stopped at {run['failed_step']}. Finished replies are saved, "
                              "so running again carries on from there.")
        return None, None

    data_manager.save_forecast(ref, run["forecast"])
    assessment = logic_manager.assess_onboarding(case, run["forecast"])
    if not data_manager.save_forecast_assessment(ref, "onboarding", assessment):
        io_manager.show_error("Could not reach the database; the decision below was not saved.")
    io_manager.show_onboarding(assessment)
    return run["forecast"], assessment


def review_case(case: dict, review: dict, client: ai_manager.AIClient,
                forecast: dict, onboarding: dict) -> dict | None:
    """Read a review's notes with the AI and decide against the saved forecast."""
    ref = case["client_ref"]
    review = {"client_ref": ref, **review}
    result = ai_manager.read_review_notes(client, review, forecast, data_manager.load_raw_replies(ref))
    data_manager.save_raw_replies(ref, {result["call"]: result})
    io_manager.show_ai_progress({result["call"]: result})
    if not result["ok"]:
        io_manager.show_error("The review notes could not be read; nothing was decided.")
        return None
    assessment = logic_manager.assess_review(case, review, result["data"], forecast, onboarding)
    if not data_manager.save_forecast_assessment(ref, f"review_{review['current_date']}", assessment):
        io_manager.show_error("Could not reach the database; the review below was not saved.")
    io_manager.show_review(assessment)
    return assessment


# ---------------------------------------------------------------------------
# The whole flow for a client from the form: crime scan -> benchmark -> forecasting
# ---------------------------------------------------------------------------

def assess_client_record(client_record: dict, client: ai_manager.AIClient) -> None:
    ref = client_record["client_ref"]

    # Crime scan
    try:
        case_input = crime_scan_input(client_record)
    except KeyError as missing:
        io_manager.show_error(f"Crime scan and benchmark skipped: the client record has no {missing}.")
        case_input = None
    research = typology_response = declaration = ai_data = None
    if case_input is not None:
        research, typology_response, declaration = run_crime_scan(case_input)
        # Benchmark
        ai_data = run_benchmark_for(client_record, case_input)

    # Save AI results to ai_assessments collection
    assessment = {
        "client_ref":   ref,
        "assessed_at":  datetime.now(timezone.utc).isoformat(),
        "crime_scan": {
            "research":    research,
            "typologies":  typology_response,
            "declaration": declaration,
        },
        "benchmark": ai_data,
    }
    if data_manager.save_assessment(assessment):
        io_manager.show_message("\nAI results saved to database.")
    else:
        io_manager.show_error("Could not reach the database; the crime scan and benchmark were not saved.")

    # Forecasting
    case = io_manager.case_from_client_record(client_record)
    if case is None:
        return
    data_manager.save_case(case)
    assess_case(case, client)


# ---------------------------------------------------------------------------
# Sample case files and saved cases
# ---------------------------------------------------------------------------

def is_forecasting_case(case: dict | None) -> bool:
    """Sample files come in two formats; only the forecasting format can be forecast directly."""
    return isinstance(case, dict) and "career_history" in case and "client_ref" in case


def run_case_file(path: str, client: ai_manager.AIClient) -> bool:
    """Forecast a case file and run every review stored in it."""
    case = data_manager.load_case(path)
    if case is not None and not is_forecasting_case(case):
        io_manager.show(f"\n{os.path.basename(path)}: not in the forecasting format "
                        "(it's a client record), skipped. Use the menu to assess it.")
        return True
    problems = io_manager.validate_case(case)
    if problems:
        io_manager.show_case_problems(path, problems)
        return False
    forecast, onboarding = assess_case(case, client)
    if forecast is None:
        return False
    for review in case.get("reviews", []):
        if review_case(case, review, client, forecast, onboarding) is None:
            return False
    return True


def saved_case_forecast(ref: str) -> tuple[dict | None, dict | None, dict | None]:
    """The case, forecast and onboarding decision saved for a client, if all three exist."""
    case = data_manager.load_forecasting_case(ref)
    if case is None:
        for path in data_manager.list_sample_cases():
            candidate = data_manager.load_case(path)
            if is_forecasting_case(candidate) and candidate["client_ref"] == ref:
                case = candidate
    return case, data_manager.load_forecast(ref), data_manager.load_assessment(ref, "onboarding")


def use_recorded_replies() -> int:
    """Copy the recorded AI replies from tests/golden into the reply cache.
    Only fills gaps, so real replies already saved are never overwritten."""
    golden = os.path.join(common.ROOT_DIR, "tests", "golden")
    copied = 0
    for path in data_manager.list_sample_cases():
        name = os.path.splitext(os.path.basename(path))[0]
        recorded = data_manager.load_json(os.path.join(golden, name, "ai_responses.json"), default={})
        case = data_manager.load_case(path)
        if not is_forecasting_case(case) or not isinstance(recorded, dict):
            continue
        saved = data_manager.load_raw_replies(case["client_ref"])
        missing = {piece: {"ok": True, "from_cache": False, "raw": json.dumps(reply), "prompt_version": "recorded"}
                   for piece, reply in recorded.items() if piece not in saved}
        copied += data_manager.save_raw_replies(case["client_ref"], missing)
    return copied


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def list_assessments() -> None:
    """List saved forecasting decisions (filtered by outcome) and optionally open one."""
    outcomes = ["(all)", "PASSED", "MANUAL_REVIEW", "RISKY", "SERIOUS_RISK"]
    picked = outcomes[io_manager.ask_choice("Show which outcome?", outcomes)]
    matches = data_manager.query_assessments(data_manager.load_all_assessments(),
                                             outcome=None if picked == "(all)" else picked)
    io_manager.show_assessment_list(matches)
    if matches and io_manager.ask_yes_no("Open one in full?"):
        labels = [f"{m['client_ref']}  {m['kind']}" for m in matches]
        chosen = matches[io_manager.ask_choice("Which one?", labels)]
        if chosen["kind"] == "onboarding":
            io_manager.show_onboarding(chosen["assessment"])
        else:
            io_manager.show_review(chosen["assessment"])


def run_menu(client: ai_manager.AIClient) -> None:
    records = data_manager.load_all_assessments()   # load everything saved on startup
    io_manager.show_message(f"{len(records)} saved assessment(s) loaded.")
    while True:
        choice = io_manager.show_app_menu()
        if choice == "1":
            client_record = io_manager.create_new_client(
                save_fn=data_manager.save_case_record,
                upload_fn=data_manager.upload_supporting_document,
            )
            if client_record is not None:
                assess_client_record(client_record, client)

        elif choice == "2":
            io_manager.find_existing_client(
                load_fn=data_manager.load_all_records,
                save_fn=data_manager.save_case_record,
                download_fn=data_manager.download_supporting_document,
                upload_fn=data_manager.upload_supporting_document,
            )

        elif choice == "3":
            ref = io_manager.ask_client_ref()
            client_record = data_manager.find_client_record(ref)
            if client_record is None:
                io_manager.show_error(f"No client found with reference {ref} (or the database can't be reached).")
                continue
            assess_client_record(client_record, client)

        elif choice == "4":
            ref = io_manager.ask_client_ref()
            case, forecast, onboarding = saved_case_forecast(ref)
            if not (case and forecast and onboarding):
                io_manager.show_error(f"{ref} hasn't been assessed yet. Use option 3 first.")
                continue
            review_case(case, io_manager.collect_review(case), client, forecast, onboarding)

        elif choice == "5":
            list_assessments()

        elif choice == "6":
            # NEW: Option to manually trigger cloud sync on demand
            io_manager.show_message("\n--- Synchronizing Local Data to Cloud Database ---")
            stats = data_manager.sync_json_to_mongodb()
            io_manager.show_message(
                f"Sync complete! Cases synced: {stats['cases_synced']}, "
                f"Assessments synced: {stats['assessments_synced']}"
            )
            if stats["errors"]:
                for err in stats["errors"]:
                    io_manager.show_error(err)

        elif choice == "7":  # Shifted exit choice from 6 to 7
            io_manager.show_message("\nGoodbye.")
            break


def main(arguments: list[str]) -> int:
    _configure_logging()
    problems = common.config_problems()
    if problems:
        for problem in problems:
            io_manager.show_error(problem)
        return 1
    client = ai_manager.init_client()
    if client is None:
        io_manager.show_error("No OPENAI_API_KEY set: only saved AI replies can be used.")

    if "--crime-scan-sample" in arguments:
        _run_sample_assessment()
        return 0
    if "--demo" in arguments:
        io_manager.show(f"Demo mode: {use_recorded_replies()} recorded AI replies loaded from tests/golden.")
    if "--all-samples" in arguments:
        paths = data_manager.list_sample_cases()
        results = [run_case_file(path, client) for path in paths]
        return 0 if paths and all(results) else 1
    if "--case" in arguments:
        position = arguments.index("--case")
        if position + 1 >= len(arguments):
            io_manager.show_error("--case needs a file path")
            return 1
        return 0 if run_case_file(arguments[position + 1], client) else 1

    run_menu(client)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
