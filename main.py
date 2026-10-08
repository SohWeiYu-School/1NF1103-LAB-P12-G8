"""
main.py - runs the Source of Wealth forecasting system.

    python main.py                         interactive menu
    python main.py --case data/sample/case_001.json
    python main.py --all-samples           assess every sample case, with its reviews (used by Docker)
    python main.py --all-samples --demo    the same, using the recorded AI replies in tests/golden
                                           (for machines with no API key, e.g. CI)

Flow for each case:
    io_manager (check the input) -> ai_manager (forecasts) -> logic_manager (decision)
    -> data_manager (save replies, forecast and decision)
"""

import json
import logging
import os
<<<<<<< HEAD
import sys

from dotenv import load_dotenv

load_dotenv()  # read .env before ai_manager reads its settings

from app import ai_manager, common, data_manager, io_manager, logic_manager  # noqa: E402


def assess_case(case: dict, client: ai_manager.AIClient) -> tuple[dict | None, dict | None]:
    """Forecast and decide one new case. Returns (forecast, onboarding assessment)."""
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
    data_manager.save_assessment(ref, "onboarding", assessment)
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
    data_manager.save_assessment(ref, f"review_{review['current_date']}", assessment)
    io_manager.show_review(assessment)
    return assessment


def load_checked_case(path: str) -> dict | None:
    case = data_manager.load_case(path)
    problems = io_manager.validate_case(case)
    if problems:
        io_manager.show_error(f"{path} can't be used:")
        for problem in problems:
            io_manager.show_error(f"  {problem}")
        return None
    return case


def run_case_file(path: str, client: ai_manager.AIClient) -> bool:
    """Assess a case file and run every review stored in it."""
    case = load_checked_case(path)
    if case is None:
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
    case = None
    for path in data_manager.list_sample_cases():
        candidate = data_manager.load_case(path)
        if candidate and candidate.get("client_ref") == ref:
            case = candidate
    if case is None:
        case = data_manager.load_case(f"{data_manager.DATA_DIR}/cases/{data_manager.safe_name(ref)}.json")
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
        if not case or not isinstance(recorded, dict):
            continue
        saved = data_manager.load_raw_replies(case["client_ref"])
        missing = {piece: {"ok": True, "from_cache": False, "raw": json.dumps(reply), "prompt_version": "recorded"}
                   for piece, reply in recorded.items() if piece not in saved}
        copied += data_manager.save_raw_replies(case["client_ref"], missing)
    return copied


def run_menu(client: ai_manager.AIClient) -> None:
    records = data_manager.load_all_assessments()   # load everything saved on startup
    io_manager.show(f"{len(records)} saved assessment(s) loaded.")
    while True:
        choice = io_manager.ask_menu()
        if choice == 0:
            paths = data_manager.list_sample_cases()
            if not paths:
                io_manager.show_error("No sample cases found in data/sample.")
                continue
            run_case_file(paths[io_manager.ask_choice("Which case?", paths)], client)
        elif choice == 1:
            case = io_manager.collect_case()
            problems = io_manager.validate_case(case)
            if problems:
                for problem in problems:
                    io_manager.show_error(problem)
                continue
            data_manager.save_case(case)
            assess_case(case, client)
        elif choice == 2:
            ref = io_manager.ask_text("Client reference")
            case, forecast, onboarding = saved_case_forecast(ref)
            if not (case and forecast and onboarding):
                io_manager.show_error(f"{ref} hasn't been assessed yet.")
                continue
            review_case(case, io_manager.collect_review(case), client, forecast, onboarding)
        elif choice == 3:
            outcomes = ["(all)", "PASSED", "MANUAL_REVIEW", "RISKY", "SERIOUS_RISK"]
            picked = outcomes[io_manager.ask_choice("Show which outcome?", outcomes)]
            records = data_manager.load_all_assessments()
            io_manager.show_assessment_list(
                data_manager.query_assessments(records, outcome=None if picked == "(all)" else picked))
        elif choice == 4:
            ref = io_manager.ask_text("Client reference")
            matches = data_manager.query_assessments(data_manager.load_all_assessments(), client_ref=ref)
            if not matches:
                io_manager.show_error(f"No saved assessments for {ref}.")
                continue
            picked = matches[io_manager.ask_choice("Which one?", [m["kind"] for m in matches])]
            if picked["kind"] == "onboarding":
                io_manager.show_onboarding(picked["assessment"])
            else:
                io_manager.show_review(picked["assessment"])
        else:
            io_manager.show("Goodbye.")
            return


def main(arguments: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    problems = common.config_problems()
    if problems:
        for problem in problems:
            io_manager.show_error(problem)
        return 1
    client = ai_manager.init_client()

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

    if client is None:
        io_manager.show_error("No GEMINI_API_KEY set: only saved AI replies can be used.")
    run_menu(client)
    return 0
=======

from dotenv import load_dotenv

from app import ai_manager, data_manager, io_manager, logic_manager

_BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CLIENT_PATH = os.path.join(_BASE, "data", "sample", "case_daniel_tan.json")
SAMPLE_BENCHMARK_PATH = os.path.join(_BASE, "data", "sample", "case_benchmark.json")


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _run_sample_assessment() -> None:
    # Interactive input disabled for testing; uses hardcoded sample client.
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

    io_manager.show_message("\nSearching trusted sources for typology reports...")
    research, res_warnings = ai_manager.get_research(case_input)
    for w in res_warnings:
        io_manager.show_error(w)
    if research is not None:
        io_manager.show_research_output(research)

    io_manager.show_message("Calling AI for sector typologies...")
    typology_response, typ_warnings = ai_manager.get_typologies(case_input)
    for w in typ_warnings:
        io_manager.show_error(w)
    if typology_response is None:
        return

    io_manager.show_message("Calling AI to parse declaration...")
    declaration, decl_warnings = ai_manager.get_declaration(case_input)
    for w in decl_warnings:
        io_manager.show_error(w)
    if declaration is None:
        return

    io_manager.show_ai_output(case_input, typology_response, declaration)


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


def main() -> None:
    load_dotenv(os.path.join(_BASE, ".env"))
    _configure_logging()

    while True:
        choice = io_manager.show_app_menu()
        if choice == "1":
            client_record = io_manager.create_new_client()
            if client_record is None:
                continue

            # Map DB fields → crime scan input
            profile = client_record["client_profile"]
            case_input = {
                "client_ref":        client_record["client_ref"],
                "occupation":        profile["latest_occupation"],
                "industry":          profile["latest_industry"],
                "age":               int(profile["age"]),
                "career_start_year": int(profile["career_start_year"]),
                "country":           profile["country_of_residence"],
                "country_of_residence": profile["country_of_residence"],
                "declaration_text":  client_record["sow_declaration"]["declaration_text"],
            }

            # --- Crime Scan ---
            io_manager.show_message("\nSearching trusted sources for typology reports...")
            research, res_warnings = ai_manager.get_research(case_input)
            for w in res_warnings:
                io_manager.show_error(w)
            if research is not None:
                io_manager.show_research_output(research)

            io_manager.show_message("Calling AI for sector typologies...")
            typology_response, typ_warnings = ai_manager.get_typologies(case_input)
            for w in typ_warnings:
                io_manager.show_error(w)

            io_manager.show_message("Calling AI to parse declaration...")
            declaration, decl_warnings = ai_manager.get_declaration(case_input)
            for w in decl_warnings:
                io_manager.show_error(w)

            if typology_response is not None and declaration is not None:
                io_manager.show_ai_output(case_input, typology_response, declaration)

            # --- Benchmark ---
            comp = client_record["asset_composition"]
            decl = client_record["client_declarations"]
            benchmark_input = {
                **case_input,
                "declared_net_worth": int(decl["declared_net_worth"]),
                "listed_equities":    int(comp["listed_equities"]),
                "cash":               int(comp["cash"]),
                "property":           int(comp["property"]),
                "private_business":   int(comp["private_business"]),
            }
            ai_data = logic_manager.run_benchmark(benchmark_input)

            # --- Save AI results to ai_assessments collection ---
            from datetime import datetime, timezone
            assessment = {
                "client_ref":   client_record["client_ref"],
                "assessed_at":  datetime.now(timezone.utc).isoformat(),
                "crime_scan": {
                    "research":    research,
                    "typologies":  typology_response,
                    "declaration": declaration,
                },
                "benchmark": ai_data,
            }
            data_manager.save_assessment(assessment)
            io_manager.show_message("\nAI results saved to database.")
        elif choice == "2":
            io_manager.find_existing_client()
        elif choice == "3":
            io_manager.show_message("\nGoodbye.")
            break
>>>>>>> cc91561


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
