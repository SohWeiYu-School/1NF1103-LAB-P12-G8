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


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _run_live_assessment() -> None:
    case_input = io_manager.collect_profile()

    io_manager.show_message("\nCalling AI for sector typologies...")
    try:
        typology_response = ai_manager.get_typologies(case_input)
    except Exception as exc:
        io_manager.show_error(f"Typology call failed: {exc}")
        return

    io_manager.show_message("Calling AI to parse declaration...")
    try:
        declaration = ai_manager.get_declaration(case_input)
    except Exception as exc:
        io_manager.show_error(f"Declaration call failed: {exc}")
        return

    io_manager.show_ai_output(typology_response, declaration)

    benchmark = typology_response  # assess_case reads sector_typologies + indicator fields

    policy = _load_json(POLICY_PATH)
    result = logic_manager.assess_case(case_input, benchmark, declaration, policy)
    io_manager.show_assessment(result)


def main() -> None:
    load_dotenv(os.path.join(_BASE, ".env"))
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))

    while True:
        choice = io_manager.show_main_menu()

        if choice == "1":
            _run_live_assessment()
        elif choice == "2":
            io_manager.show_message("View all cases — not implemented yet.")
        elif choice == "3":
            io_manager.show_message("Goodbye.")
            break
        else:
            io_manager.show_error("Invalid choice. Please enter 1, 2, or 3.")


if __name__ == "__main__":
    main()
