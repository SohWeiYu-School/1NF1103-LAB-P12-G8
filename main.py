"""
main.py — entry point and orchestrator for the SOW Screening System.

Imports all four managers and calls them in sequence.
Managers never import each other — all coordination happens here.
"""

import json
import logging
import os

from dotenv import load_dotenv

from app import ai_manager, io_manager, logic_manager

_BASE = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.path.join(_BASE, os.getenv("POLICY_PATH", "config/policy.json"))
SAMPLE_CLIENT_PATH = os.path.join(_BASE, "data", "sample", "case_daniel_tan.json")


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _run_live_assessment() -> None:
    """Interactive flow — collects input from the officer at the terminal."""
    case_input = io_manager.collect_profile()

    io_manager.show_message("\nCalling AI for sector typologies...")
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

    io_manager.show_ai_output(typology_response, declaration)

    benchmark = typology_response
    policy = _load_json(POLICY_PATH)
    result = logic_manager.assess_case(case_input, benchmark, declaration, policy)
    io_manager.show_assessment(result)


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
        "occupation": profile["latest_occupation"],
        "industry": profile["latest_industry"],
        "age": profile["age"],
        "career_start_year": profile["career_start_year"],
        "country": profile["country_of_residence"],
        "country_of_residence": profile["country_of_residence"],
        "declaration_text": raw["sow_declaration"]["text"],
    }

    io_manager.show_message("\n--- Fields sent to AI (5 safe fields only) ---")
    io_manager.show_message(f"  occupation:        {case_input['occupation']}")
    io_manager.show_message(f"  industry:          {case_input['industry']}")
    io_manager.show_message(f"  age:               {case_input['age']}")
    io_manager.show_message(f"  career_start_year: {case_input['career_start_year']}")
    io_manager.show_message(f"  country:           {case_input['country']}")

    io_manager.show_message("\nCalling AI for sector typologies...")
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

    io_manager.show_ai_output(typology_response, declaration)


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
        choice = io_manager.show_main_menu()

        if choice == "1":
            _run_sample_assessment()
        elif choice == "2":
            io_manager.show_message("View all cases — not implemented yet.")
        elif choice == "3":
            io_manager.show_message("Goodbye.")
            break
        else:
            io_manager.show_error("Invalid choice. Please enter 1, 2, or 3.")


if __name__ == "__main__":
    main()
