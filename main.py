"""
main.py — entry point and orchestrator for the SOW Screening System.

Imports all four managers and calls them in sequence.
Managers never import each other — all coordination happens here.
"""

import json
import logging
import os

from dotenv import load_dotenv

from app import ai_manager, io_manager

_BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CLIENT_PATH = os.path.join(_BASE, "data", "sample", "case_daniel_tan.json")


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
            io_manager.create_new_client()
        elif choice == "2":
            io_manager.find_existing_client()
        elif choice == "3":
            _run_sample_assessment()
        elif choice == "4":
            # TODO: wire up benchmark teammate's logic
            io_manager.show_message("\n[Benchmark] Not yet wired up.")
        elif choice == "5":
            # TODO: wire up forecasting
            io_manager.show_message("\n[Forecasting] Not yet wired up.")
        elif choice == "6":
            io_manager.show_message("\nGoodbye.")
            break


if __name__ == "__main__":
    main()
