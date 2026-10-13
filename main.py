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

Flow for a client (menu options 1, 2 and 4):
    1. io_manager      New Client form -> saved to MongoDB (client_cases)
       or option 2:    a folder of documents -> data_manager reads them -> ai_manager fills in
                       the details -> io_manager asks for what's missing and shows a summary to check
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

from app import ai_manager, data_manager, io_manager, logic_manager, utilities  # noqa: E402

SAMPLE_CLIENT_PATH = os.path.join(_BASE, "data", "sample", "case_daniel_tan.json")
SAMPLE_BENCHMARK_PATH = os.path.join(_BASE, "data", "sample", "case_benchmark.json")


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

# Adapts the full client record into the flat dict that the crime scan pipeline expects.
def crime_scan_input(client_record: dict) -> dict:
    """Map the client record (flat structure) to the crime scan input."""
    career = client_record["career"]
    sow = client_record["sow_declaration"]
    return {
        "client_ref":           client_record["client_ref"],
        "occupation":           career["occupation"],
        "industry":             career["industry"],
        "age":                  utilities.whole_number(career["age"]),
        "career_start_year":    utilities.whole_number(career["career_start_year"]),
        "country":              career["country_of_residence"],
        "country_of_residence": career["country_of_residence"],
        "declaration_text":     sow if isinstance(sow, str) else sow.get("declaration_text", ""),
    }


# Orchestrates the full crime scan: research → verify → typologies → declaration → match → display.
def run_crime_scan_pipeline(case_input: dict) -> tuple[dict | None, dict | None, dict | None, dict | None]:
    """Research, verify, match typologies and parse declaration.
    Returns (research, typologies, declaration, crime_result)."""
    policy = utilities.policy()

    # Step 1: Research
    io_manager.show_message("\nSearching trusted sources for typology reports...")
    research, res_warnings = ai_manager.get_ai_research(case_input)
    for w in res_warnings:
        io_manager.show_error(w)
    if research is not None:
        io_manager.show_research_output(research)

    # Step 2: Verify research (TODO: CHECK)
    if research is not None:
        clean_research, verify_warnings = logic_manager.verify_research(research, policy)
        for w in verify_warnings:
            io_manager.show_error(w)
    else:
        clean_research = None

    # Step 3: Typologies (TODO: CHECK)
    io_manager.show_message("Calling AI for sector typologies...")
    typology_response, typ_warnings = ai_manager.get_typologies(case_input, research)
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
    crime_result = None
    if typology_response is not None and declaration is not None:
        io_manager.show_ai_output(case_input, typology_response, declaration)
        crime_result = logic_manager.assess_typology_case(case_input, typology_response, declaration, policy)
        io_manager.show_crime_scan_result(crime_result, typology_response, research)

    return research, typology_response, declaration, crime_result


def _run_sample_assessment() -> None:
    """Crime scan of the hardcoded sample client (python main.py --crime-scan-sample)."""
    client_record = data_manager.load_case(SAMPLE_CLIENT_PATH)
    if client_record is None:
        io_manager.show_error(f"Could not read the sample client: {SAMPLE_CLIENT_PATH}")
        return
    run_crime_scan_pipeline(crime_scan_input(client_record))


# ---------------------------------------------------------------------------
# 3. Benchmark
# ---------------------------------------------------------------------------

# Orchestrates the benchmark: builds input, calls AI for expected values, runs logic checks, returns findings.
def run_benchmark_pipeline(client_record: dict, case_input: dict) -> dict | None:
    """The benchmark check on the declared net worth and asset split."""
    claims = client_record.get("claims", {})
    comp   = claims.get("asset_composition", {})

    def _comp_pct(val) -> int | None:
        """Convert fraction (0.1) or percentage (10) to whole-number percentage."""
        n = utilities.whole_number(val)
        if n is None:
            return None
        # If stored as fraction (0-1), multiply by 100
        raw = val if val is not None else 0
        try:
            f = float(raw)
            return round(f * 100) if f <= 1.0 else n
        except (TypeError, ValueError):
            return n

    benchmark_input = {
        **case_input,
        "declared_net_worth":      utilities.whole_number(claims.get("declared_net_worth")),
        "listed_equities":         _comp_pct(comp.get("listed_equities")),
        "cash":                    _comp_pct(comp.get("cash")),
        "property":                _comp_pct(comp.get("property")),
        "private_business":        _comp_pct(comp.get("private_business")),
        "biggest_wealth_jump":     float(claims["biggest_wealth_jump"]) if claims.get("biggest_wealth_jump") is not None else None,
        "wealth_jump_years":       float(claims.get("wealth_jump_years") or 0.0),
        "declared_counterparties": claims.get("declared_counterparties", []),
        "declared_countries":      claims.get("wealth_countries", []),
    }

    unreadable = [k for k in ("declared_net_worth", "listed_equities", "cash", "property", "private_business")
                  if benchmark_input[k] is None]
    if unreadable:
        io_manager.show_error(f"Benchmark skipped: can't read {', '.join(unreadable)} from the client record.")
        return None
    ai_data = ai_manager.get_ai_benchmark(benchmark_input)
    return logic_manager.run_benchmark(benchmark_input, ai_data)

# ---------------------------------------------------------------------------
# 4. Forecasting
# ---------------------------------------------------------------------------

def run_forecast_pipeline(case: dict) -> tuple[dict | None, dict | None]:
    """Forecast and decide one case. Returns (forecast, onboarding assessment)."""
    ref = case["client_ref"]
    cache = data_manager.load_raw_replies(ref)
    io_manager.show(f"\nForecasting {ref} ({len(cache)} saved AI replies found)...")
    run = ai_manager.run_onboarding_forecast(case, cache)
    data_manager.save_raw_replies(ref, run["results"])
    io_manager.show_ai_progress(run["results"])
    if not run["ok"]:
        io_manager.show_error(f"Stopped at {run['failed_step']}. Finished replies are saved, "
                              "so running again carries on from there.")
        return None, None

    data_manager.save_forecast(ref, run["forecast"])
    assessment = logic_manager.assess_onboarding(case, run["forecast"])
    io_manager.show_onboarding(assessment)
    return run["forecast"], assessment


def review_case(case: dict, review: dict,
                forecast: dict, onboarding: dict) -> dict | None:
    """Read a review's notes with the AI and decide against the saved forecast."""
    ref = case["client_ref"]
    review = {"client_ref": ref, **review}
    result = ai_manager.read_review_notes(review, forecast, data_manager.load_raw_replies(ref))
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

def assess_client_record(client_record: dict) -> None:
    """Crime scan, benchmark and forecasting for a saved client, saved as one assessment."""
    ref = client_record["client_ref"]

    # Crime scan
    try:
        case_input = crime_scan_input(client_record)
    except KeyError as missing:
        io_manager.show_error(f"Crime scan and benchmark skipped: the client record has no {missing}.")
        case_input = None
    research = typology_response = declaration = crime_result = benchmark = None
    if case_input is not None:
        research, typology_response, declaration, crime_result = run_crime_scan_pipeline(case_input)
        # Benchmark
        benchmark = run_benchmark_pipeline(client_record, case_input)
        if benchmark is not None:
            io_manager.show_benchmark_result(benchmark)

    # Forecasting (the officer is asked only for anything the forecast needs that's missing)
    forecast_assessment = None
    case = io_manager.case_from_client_record(client_record)
    if case is not None:
        client_record["forecasting_case"] = case
        data_manager.save_case_record(client_record)
        _, forecast_assessment = run_forecast_pipeline(case)
        if forecast_assessment is not None:
            data_manager.save_forecast_assessment(ref, "onboarding", forecast_assessment)

    # Save one combined record (crime scan + benchmark + forecast)
    assessment = {
        "client_ref":  ref,
        "kind":        "onboarding",
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "crime_scan": {
            "research":    research,
            "typologies":  typology_response,
            "declaration": declaration,
        },
        "crime_result": crime_result,
        "benchmark":    benchmark,
        "forecast":     forecast_assessment,
        "assessment":   forecast_assessment,
    }
    if data_manager.save_assessment(assessment):
        io_manager.show_message("\nAssessment saved.")
    else:
        io_manager.show_error("Could not save the assessment.")


# ---------------------------------------------------------------------------
# New client from a folder of documents (menu option 2)
# ---------------------------------------------------------------------------

def new_client_from_documents() -> dict | None:
    """Read a folder of documents, auto-fill the client, ask for what's missing, show a summary
    to check, then save. Returns the saved record (to assess) or None."""
    # 1. Which folder
    while True:
        folder = io_manager.ask_document_folder()
        if folder is None:
            return None
        paths = data_manager.list_documents_in_folder(folder)
        if paths is None:
            io_manager.show_error(f"Folder not found: {folder}")
        elif not paths:
            io_manager.show_error("No PDF, Word, Excel, text or image files in that folder.")
        else:
            break
    io_manager.show_documents_found(paths)

    # 2. Read the documents and let the AI pull out the details
    io_manager.show_message(f"\nReading {len(paths)} document(s) with AI... this can take up to a minute.")
    documents = [data_manager.read_document_for_extraction(path) for path in paths]
    extracted, warnings = ai_manager.extract_client_details(documents)
    for warning in warnings:
        io_manager.show_error(warning)
    if extracted is None:
        if not io_manager.ask_yes_no("The documents couldn't be read automatically. Type the details in yourself?"):
            return None
        extracted = {}

    # 3. Build the record and ask for anything missing
    today = datetime.now().date().isoformat()
    doc_record, _ = logic_manager.record_from_documents(extracted, io_manager.generate_client_id(), today)
    record = doc_record
    missing = logic_manager.missing_client_fields(record)
    io_manager.show_missing_intro(len(missing))
    for field in missing:
        record = logic_manager.set_path(record, field["path"], io_manager.ask_missing_field(field))
    record = logic_manager.finish_document_record(record)
    doc_record = logic_manager.finish_document_record(doc_record)

    # 4. Summary to check, edit until right, then save or discard
    notes = list(extracted.get("notes") or []) + list(warnings)
    while True:
        rows = logic_manager.summarise_document_record(record, doc_record)
        checks = logic_manager.check_document_record(record, today)
        io_manager.show_document_summary(rows, paths, notes, checks)
        action = io_manager.ask_document_record_action()
        if action == "discard":
            io_manager.show_message("\nClient discarded. Nothing was saved.")
            return None
        if action == "edit":
            before = json.loads(json.dumps(record))
            io_manager.edit_existing_client(record, data_manager.upload_supporting_document)
            record = logic_manager.sync_after_edit(before, record)
            still_missing = logic_manager.missing_client_fields(record)
            for field in still_missing:
                record = logic_manager.set_path(record, field["path"], io_manager.ask_missing_field(field))
            continue
        break

    # 5. Keep the documents with the client, then save
    for path in paths:
        stored = data_manager.upload_supporting_document(path)
        if stored is None:
            io_manager.show_error(f"Could not keep a copy of {os.path.basename(path)}.")
        else:
            record.setdefault("supporting_documents", []).append(
                {"file_name": os.path.basename(path), "file_path": stored})
    if not data_manager.save_case_record(record):
        io_manager.show_error("The client could not be saved.")
        return None
    io_manager.show_message(f"\nClient saved. Reference: {record['client_ref']} "
                            f"({len(record['supporting_documents'])} document(s) attached).")
    return record if io_manager.ask_after_save(record) else None


# ---------------------------------------------------------------------------
# Sample case files and saved cases
# ---------------------------------------------------------------------------

def is_forecasting_case(case: dict | None) -> bool:
    """Sample files come in two formats; only the forecasting format can be forecast directly."""
    return isinstance(case, dict) and "career_history" in case and "client_ref" in case


def run_case_file(path: str) -> bool:
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
    forecast, onboarding = run_forecast_pipeline(case)
    if forecast is None:
        return False
    if not data_manager.save_forecast_assessment(case["client_ref"], "onboarding", onboarding):
        io_manager.show_error("Could not reach the database; the decision was not saved.")
    for review in case.get("reviews", []):
        if review_case(case, review, forecast, onboarding) is None:
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
    golden = os.path.join(utilities.ROOT_DIR, "tests", "golden")
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


def run_menu() -> None:
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
                assess_client_record(client_record)

        elif choice == "2":
            client_record = new_client_from_documents()
            if client_record is not None:
                assess_client_record(client_record)

        elif choice == "3":
            io_manager.find_existing_client(
                load_fn=data_manager.load_all_records,
                save_fn=data_manager.save_case_record,
                download_fn=data_manager.download_supporting_document,
                upload_fn=data_manager.upload_supporting_document,
            )

        elif choice == "4":
            ref = io_manager.ask_client_ref()
            if ref is None:
                continue
            client_record = data_manager.find_client_record(ref)
            if client_record is None:
                io_manager.show_error(f"No client found with reference {ref} (or the database can't be reached).")
                continue
            assess_client_record(client_record)

        elif choice == "5":
            ref = io_manager.ask_client_ref()
            if ref is None:
                continue
            case, forecast, onboarding = saved_case_forecast(ref)
            if not (case and forecast and onboarding):
                io_manager.show_error(f"{ref} hasn't been assessed yet. Use option 4 first.")
                continue
            review_case(case, io_manager.collect_review(case), forecast, onboarding)

        elif choice == "6":
            list_assessments()

        elif choice == "7":
            io_manager.show_client_list(data_manager.load_all_records())
            io_manager.read_answer("Press Enter to continue...")

        elif choice == "8":
            io_manager.show_message("\n--- Synchronizing Local Data to Cloud Database ---")
            stats = data_manager.sync_json_to_mongodb()
            io_manager.show_message(
                f"Sync complete! Cases synced: {stats['cases_synced']}, "
                f"Assessments synced: {stats['assessments_synced']}, "
                f"Already up to date: {stats['skipped']}"
            )
            if stats["errors"]:
                for err in stats["errors"]:
                    io_manager.show_error(err)

        elif choice == "9":
            io_manager.show_message("\nGoodbye.")
            break


def main(arguments: list[str]) -> int:
    _configure_logging()
    problems = utilities.config_problems()
    if problems:
        for problem in problems:
            io_manager.show_error(problem)
        return 1
    if not ai_manager.has_api_key():
        io_manager.show_error("No OPENAI_API_KEY set: only saved AI replies can be used.")

    if "--crime-scan-sample" in arguments:
        _run_sample_assessment()
        return 0
    if "--demo" in arguments:
        io_manager.show(f"Demo mode: {use_recorded_replies()} recorded AI replies loaded from tests/golden.")
    if "--all-samples" in arguments:
        paths = data_manager.list_sample_cases()
        results = [run_case_file(path) for path in paths]
        return 0 if paths and all(results) else 1
    if "--case" in arguments:
        position = arguments.index("--case")
        if position + 1 >= len(arguments):
            io_manager.show_error("--case needs a file path")
            return 1
        return 0 if run_case_file(arguments[position + 1]) else 1

    run_menu()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))