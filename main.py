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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
