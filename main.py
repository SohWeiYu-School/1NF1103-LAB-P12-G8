"""
main.py — entry point and orchestrator for the SOW Screening System.

Imports all four managers and calls them in sequence.
Managers never import each other — all coordination happens here.
"""

import json
import logging
import os

from dotenv import load_dotenv

from app import ai_manager, data_manager, io_manager, logic_manager
from app.utilities import load_policy

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

    if research is None:
        io_manager.show_error(
            "Sector Crime Scan skipped: research failed. This case needs manual review."
        )
        typology_response = None
        clean_research = None
    else:
        policy = load_policy()
        clean_research, verify_warnings = logic_manager.verify_research(research, policy)
        for w in verify_warnings:
            io_manager.show_error(w)
        if len(clean_research.get("reports", [])) == 0:
            io_manager.show_message("No published typologies found for this industry.")
            typology_response = {
                "sector_typologies": [],
                "expected_jurisdictions": [],
                "max_accumulation_per_year_sgd": 0,
            }
        else:
            io_manager.show_message("Calling AI for sector typologies...")
            typology_response, typ_warnings = ai_manager.get_typologies(case_input, clean_research)
            for w in typ_warnings:
                io_manager.show_error(w)
            if typology_response is not None:
                results, typ_verify_warnings = logic_manager.verify_typology_sources(
                    typology_response.get("sector_typologies", []), clean_research, policy,
                )
                for w in typ_verify_warnings:
                    io_manager.show_error(w)
                typology_response = {**typology_response, "sector_typologies": results}

    io_manager.show_message("Calling AI to parse declaration...")
    declaration, decl_warnings = ai_manager.get_declaration(case_input)
    for w in decl_warnings:
        io_manager.show_error(w)
    if declaration is None:
        return
    declaration, norm_warnings = logic_manager.normalize_declaration_jurisdictions(declaration)
    for w in norm_warnings:
        io_manager.show_error(w)

    io_manager.show_ai_output(case_input, typology_response, declaration, research=clean_research)

    if typology_response is not None:
        crime_result = logic_manager.assess_case(raw, typology_response, declaration, policy)
        io_manager.show_crime_scan_result(crime_result)


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

            if research is None:
                io_manager.show_error(
                    "Sector Crime Scan skipped: research failed. This case needs manual review."
                )
                typology_response = None
                clean_research = None
            else:
                policy = load_policy()
                clean_research, verify_warnings = logic_manager.verify_research(research, policy)
                for w in verify_warnings:
                    io_manager.show_error(w)
                if len(clean_research.get("reports", [])) == 0:
                    io_manager.show_message("No published typologies found for this industry.")
                    typology_response = {
                        "sector_typologies": [],
                        "expected_jurisdictions": [],
                        "max_accumulation_per_year_sgd": 0,
                    }
                else:
                    io_manager.show_message("Calling AI for sector typologies...")
                    typology_response, typ_warnings = ai_manager.get_typologies(case_input, clean_research)
                    for w in typ_warnings:
                        io_manager.show_error(w)
                    if typology_response is not None:
                        cleaned_typologies, typ_verify_warnings = logic_manager.verify_typology_sources(
                            typology_response.get("sector_typologies", []), clean_research, policy,
                        )
                        for w in typ_verify_warnings:
                            io_manager.show_error(w)
                        typology_response = {**typology_response, "sector_typologies": cleaned_typologies}

            io_manager.show_message("Calling AI to parse declaration...")
            declaration, decl_warnings = ai_manager.get_declaration(case_input)
            for w in decl_warnings:
                io_manager.show_error(w)
            if declaration is not None:
                declaration, norm_warnings = logic_manager.normalize_declaration_jurisdictions(declaration)
                for w in norm_warnings:
                    io_manager.show_error(w)

            if typology_response is not None and declaration is not None:
                io_manager.show_ai_output(case_input, typology_response, declaration, research=clean_research)
                crime_result = logic_manager.assess_case(
                    client_record, typology_response, declaration, policy,
                )
                io_manager.show_crime_scan_result(crime_result)

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


if __name__ == "__main__":
    main()
