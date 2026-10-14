"""
main.py - runs the Source of Wealth screening system. It only coordinates: the four
managers never import each other's work, everything is passed through here.

    python main.py                          interactive menu
    python main.py --case data/sample/case_001.json
                                            assess one sample case (and run its reviews)
    python main.py --all-samples            assess every sample case in the forecasting format
    python main.py --all-samples --demo     the same, using the recorded AI replies in tests/golden
                                            (for machines with no API key, e.g. CI)

ONE assessment, shared with the web app (app/web_app.py calls run_combined_assessment and
save_combined_assessment below, so the command line and the web app give the same verdict
and the same report):

    The client record -> the case (logic_manager.forecasting_case; the CLI asks for anything missing)
     1    Documents           every supporting document is read: what they SHOW (the documents layer),
                              compared later with what the client DECLARED
     2-5  Crime scan context  research reports -> verified -> sector typologies -> verified quotes
     6-10 Forecast            declaration (read ONCE, shared with the crime scan) -> economy ->
                              pay + assets -> person types BUILT FROM the typologies -> behaviour + events
                              (pay is then set to what his tax notices / employment letter show)
     11   Benchmark           peers in the same economy (the forecast's markets go to the benchmark AI)
     12   Test every story    crime warning signs per person type, benchmark checks cross-checked
                              against the forecast, forecast checks, documents vs declaration
     13   ONE verdict         logic_manager.combined_verdict -> logic_manager.verdict_report
     14   Save                one assessment record (verdict, report and the evidence behind them)
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE, ".env"))  # read .env before ai_manager reads its settings

from app import ai_manager, data_manager, io_manager, logic_manager, utilities  # noqa: E402


def _configure_logging() -> None:
    """Route all logging to logs/app.log. Nothing goes to the console. The network libraries only
    log warnings, so their connection-by-connection DEBUG lines don't flood the file."""
    log_dir = os.path.join(_BASE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "app.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)
    utilities.quiet_library_logs()


# ---------------------------------------------------------------------------
# The combined assessment (used by the command line AND the web app)
# ---------------------------------------------------------------------------

# The steps both the command line and the web app's loading page show, in order
ASSESSMENT_STEPS = [
    "Reading the submitted documents",
    "Searching trusted sources for typology reports",
    "Checking the research sources",
    "Describing sector typologies",
    "Checking the typology quotes",
    "Reading the source of wealth declaration",
    "Forecasting the economy",
    "Forecasting pay and assets",
    "Building person types from the typologies",
    "Forecasting behaviour and future events",
    "Comparing with peers in the same economy",
    "Testing every story",
    "Deciding the verdict",
    "Saving the assessment",
]
_FORECAST_STEP = {"step0_declaration": 6, "call1_markets": 7, "call2_earnings": 8,
                  "call3_types": 9, "call4_events": 10}


def today() -> str:
    """Every assessment is dated today (the command line and the web app both use this)."""
    return datetime.now().date().isoformat()


def prepare_case(client_record: dict) -> dict | None:
    """The case to assess, built exactly as the web app builds it. The command line only asks
    the officer for what can't be read from the record."""
    case, missing = logic_manager.forecasting_case(client_record, today())
    if case is None:
        io_manager.show_error("Needed before the assessment can run: " + ", ".join(missing))
        case = io_manager.case_from_client_record(client_record)
        if case is None:
            return None
    problems = io_manager.validate_case(case)
    if problems:
        io_manager.show_case_problems(case["client_ref"], problems)
        return None
    return case


def run_combined_assessment(case: dict, supporting_documents: list[dict] | None = None, on_step=None) -> dict:
    """Documents, crime scan, forecast and benchmark as ONE assessment, then ONE verdict.

    supporting_documents: the client record's saved documents; every one is read.

    on_step(number, label) is told each step in ASSESSMENT_STEPS as it starts (the command
    line prints it, the web app's loading page shows it). Nothing is printed here.
    """
    def step(number: int) -> None:
        if on_step is not None:
            on_step(number, ASSESSMENT_STEPS[number - 1])

    policy = utilities.policy()
    ref = case["client_ref"]
    notes: list[str] = []
    not_run: list[str] = []
    crime_input = logic_manager.crime_scan_input(case)

    # 1. Documents: what every submitted document shows (compared later with what he declared)
    step(1)
    files = [str(d.get("file_name") or "document") for d in supporting_documents or []]
    extracted = None
    if files:
        extracted, warnings = ai_manager.extract_client_details(
            data_manager.read_supporting_documents(supporting_documents))
        notes += warnings
        if extracted is None:
            not_run.append("Reading the submitted documents")
    evidence = logic_manager.document_evidence(extracted, files, policy)

    # 2-5. Crime scan context: what published reports say about his industry
    step(2)
    research, warnings = ai_manager.get_ai_research(crime_input)
    notes += warnings
    step(3)
    clean_research = None
    if research is not None:
        clean_research, warnings = logic_manager.verify_research(research, policy)
        notes += warnings
    step(4)
    typology_response, warnings = ai_manager.get_typologies(crime_input, research)
    notes += warnings
    step(5)
    typologies = None
    if typology_response is None:
        not_run.append("The crime scan (sector typologies)")
    else:
        typologies = typology_response.get("sector_typologies", [])
        if clean_research is not None:
            typologies, warnings = logic_manager.verify_typology_sources(typologies, clean_research, policy)
            notes += warnings

    # 6-10. Forecast: the declaration is read once here, and the crime types are built from the typologies
    run = ai_manager.run_onboarding_forecast(case, data_manager.load_raw_replies(ref), typologies,
                                             on_step=lambda stage: step(_FORECAST_STEP[stage]))
    data_manager.save_raw_replies(ref, run["results"])
    partial = run["partial"]
    forecast = run["forecast"] if run["ok"] else None
    stopped_at = None
    if forecast is None:
        stopped_at = (f"{logic_manager.describe_piece(case, run.get('failed_piece'))} "
                      f"({run.get('failed_error') or 'unknown error'})")
        not_run.append("The forecast")
        notes.append(f"The forecast stopped at {stopped_at}. Finished AI replies are saved, "
                     "so running again carries on from there.")
    else:
        data_manager.save_forecast(ref, forecast)
    # his real pay, from his tax notices / employment letter, replaces the AI's guess
    pay_adjustments = []
    if forecast is not None:
        forecast, pay_adjustments = logic_manager.calibrate_pay(case, forecast, evidence["documented_pay"], policy)

    # 11. Benchmark: peers in the same economy (the forecast's markets, never his own figures)
    step(11)
    bench_input, unreadable = logic_manager.benchmark_input(case, crime_input)
    bench_ai = findings = None
    if unreadable:
        notes.append(f"Peer benchmark skipped: can't read {', '.join(unreadable)} from the case.")
    else:
        bench_ai = ai_manager.get_ai_benchmark(bench_input, logic_manager.economy_brief(partial.get("markets")))
        if bench_ai is not None:
            try:
                findings = logic_manager.run_benchmark(bench_input, bench_ai)["findings"]
            except (ZeroDivisionError, KeyError, TypeError) as error:
                notes.append(f"Peer benchmark couldn't be worked out from the AI's reply ({error!r}).")
    if findings is None:
        not_run.append("The peer benchmark")

    # 12. Test every story
    step(12)
    forecast_assessment = logic_manager.assess_onboarding(case, forecast) if forecast else None
    declaration = partial.get("declaration")
    crime_decl, warnings = logic_manager.crime_declaration(declaration, policy)
    notes += warnings
    crime_result = None
    if typologies is not None and crime_decl is not None:
        crime_decl, warnings = logic_manager.normalize_declaration_jurisdictions(crime_decl)
        notes += warnings
        crime_result = logic_manager.assess_typology_case(crime_input, {"sector_typologies": typologies},
                                                          crime_decl, policy)
    elif typologies is not None:
        not_run.append("The crime scan's warning signs (the declaration couldn't be read)")

    # 13. One verdict, and the report both front ends show
    step(13)
    verdict = logic_manager.combined_verdict(case, {
        "research": clean_research if clean_research is not None else research,
        "typologies": typologies, "crime_result": crime_result, "declaration": declaration,
        "benchmark_input": bench_input, "benchmark_ai": bench_ai, "benchmark_findings": findings,
        "forecast": forecast, "forecast_assessment": forecast_assessment,
        "markets": partial.get("markets"), "evidence": evidence, "pay_adjustments": pay_adjustments,
        "failed_step": stopped_at, "not_run": not_run, "notes": notes,
    }, policy)
    return {
        "case": case,
        "research": research,
        "typology_response": typology_response,
        "typologies": typologies,
        "declaration": declaration,
        "crime_declaration": crime_decl,
        "crime_result": crime_result,
        "benchmark": {"input": bench_input, "expected": bench_ai, "findings": findings},
        "forecast": forecast,
        "forecast_assessment": forecast_assessment,
        "evidence": evidence,
        "verdict": verdict,
        "report": logic_manager.verdict_report(verdict),
    }


def save_combined_assessment(result: dict, client_record: dict | None = None,
                             on_step=None) -> str | None:
    """Save the assessment as ONE record (and the case on the client record, when given).
    Returns the assessment id, or None if it couldn't be saved."""
    if on_step is not None:
        on_step(len(ASSESSMENT_STEPS), ASSESSMENT_STEPS[-1])
    case = result["case"]
    verdict = result["verdict"]
    if client_record is not None:
        client_record["forecasting_case"] = case
        client_record.update(outcome=verdict["verdict"], routing=verdict["next_step"],
                             next_review_date=verdict["next_review_date"], last_assessed_on=verdict["assessed_on"])
        data_manager.save_case_record(client_record)
    assessment_id = str(uuid.uuid4())
    saved = data_manager.save_assessment({
        "assessment_id": assessment_id,
        "client_ref": case["client_ref"],
        "kind": "onboarding",
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "outcome": result["verdict"]["verdict"],
        "verdict": result["verdict"],
        "report": result["report"],
        "crime_scan": {"research": result["research"], "typologies": result["typology_response"],
                       "declaration": result["crime_declaration"]},
        "crime_result": result["crime_result"],
        "benchmark": result["benchmark"],
        "document_evidence": result["evidence"],
        "forecast": result["forecast_assessment"],
        "assessment": result["forecast_assessment"],
    })
    return assessment_id if saved else None


def _show_step(number: int, label: str) -> None:
    io_manager.show_step(number, len(ASSESSMENT_STEPS), label)


def assess_client_record(client_record: dict) -> dict | None:
    """The command line's assessment of a saved client: the same flow and report as the web app."""
    case = prepare_case(client_record)
    if case is None:
        return None
    io_manager.show_message(f"\nAssessing {case['client_ref']}: documents, crime scan, forecast and benchmark together.")
    result = run_combined_assessment(case, client_record.get("supporting_documents"), on_step=_show_step)
    saved = save_combined_assessment(result, client_record, on_step=_show_step)
    io_manager.show_verdict_report(result["report"],
                                   crime_result=result.get("crime_result"),
                                   typologies=result.get("typologies"))
    if saved:
        io_manager.show_message("Assessment saved.")
    else:
        io_manager.show_error("Could not save the assessment.")
    return result


def review_case(case: dict, review: dict,
                forecast: dict, onboarding: dict) -> dict | None:
    """Read a review's notes with the AI and decide against the saved forecast."""
    ref = case["client_ref"]
    review = {"client_ref": ref, **review}
    result = ai_manager.read_review_notes(review, forecast, data_manager.load_raw_replies(ref))
    data_manager.save_raw_replies(ref, {result["call"]: result})
    io_manager.show_ai_progress({result["call"]: result})
    if not result["ok"]:
        io_manager.show_ai_unavailable("Review notes")
        return None
    assessment = logic_manager.assess_review(case, review, result["data"], forecast, onboarding)
    if not data_manager.save_forecast_assessment(ref, f"review_{review['current_date']}", assessment):
        io_manager.show_error("Could not reach the database; the review below was not saved.")
    io_manager.show_review(assessment)
    return assessment


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
        logging.getLogger(__name__).info(warning)
    if extracted is None:
        io_manager.show_ai_unavailable("Document reading")
        if not io_manager.ask_yes_no("Type the details in yourself?"):
            return None
        extracted = {}

    # 3. Build the record and ask for anything missing
    date = today()
    doc_record, _ = logic_manager.record_from_documents(extracted, io_manager.generate_client_id(), date)
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
        checks = logic_manager.check_document_record(record, date)
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
    """Assess a case file (the same combined assessment as the menu) and run every review stored in it."""
    case = data_manager.load_case(path)
    if case is not None and not is_forecasting_case(case):
        io_manager.show(f"\n{os.path.basename(path)}: not in the forecasting format "
                        "(it's a client record), skipped. Use the menu to assess it.")
        return True
    problems = io_manager.validate_case(case)
    if problems:
        io_manager.show_case_problems(path, problems)
        return False
    io_manager.show_message(f"\nAssessing {case['client_ref']} ({os.path.basename(path)}).")
    result = run_combined_assessment(case, on_step=_show_step)
    if not save_combined_assessment(result, on_step=_show_step):
        io_manager.show_error("Could not save the assessment.")
    io_manager.show_verdict_report(result["report"], ask_for_evidence=False,
                                   crime_result=result.get("crime_result"),
                                   typologies=result.get("typologies"))
    forecast, onboarding = result["forecast"], result["forecast_assessment"]
    if forecast is None:
        return False
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
    choice = io_manager.ask_choice("Show which outcome?", outcomes, allow_back=True)
    if choice is None:
        return          # 0 = back to the main menu
    picked = outcomes[choice]
    matches = data_manager.query_assessments(data_manager.load_all_assessments(),
                                             outcome=None if picked == "(all)" else picked)
    io_manager.show_assessment_list(matches)
    if matches and io_manager.ask_yes_no("Open one in full?"):
        labels = [f"{m['client_ref']}  {m['kind']}" for m in matches]
        chosen = matches[io_manager.ask_choice("Which one?", labels)]
        if chosen.get("report"):
            io_manager.show_verdict_report(chosen["report"])     # the combined assessment
        elif chosen["kind"] == "onboarding":
            io_manager.show_onboarding(chosen["assessment"])     # saved before the combined verdict
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
                allowed, wait = utilities.check_rate_limit("cli")
                if not allowed:
                    io_manager.show_rate_limit_error(wait)
                else:
                    utilities.record_assessment("cli")
                    assess_client_record(client_record)

        elif choice == "2":
            client_record = new_client_from_documents()
            if client_record is not None:
                allowed, wait = utilities.check_rate_limit("cli")
                if not allowed:
                    io_manager.show_rate_limit_error(wait)
                else:
                    utilities.record_assessment("cli")
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
            allowed, wait = utilities.check_rate_limit("cli")
            if not allowed:
                io_manager.show_rate_limit_error(wait)
            else:
                utilities.record_assessment("cli")
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

    if "--demo" in arguments:
        io_manager.show(f"Demo mode: {use_recorded_replies()} recorded AI replies loaded from tests/golden.")
    try:
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
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error in CLI")
        io_manager.show_unexpected_error()
        return 1

    try:
        run_menu()
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error in CLI")
        io_manager.show_unexpected_error()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))