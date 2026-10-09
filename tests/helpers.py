"""
helpers.py - shared helpers for the golden and cross-check tests.

The real AI replies are read straight from where the system saves them: the
ai_raw_replies collection in MongoDB (written when you run the system on a case).

Only sample files in the forecasting format (career_history, investments, claims) are
used. data/sample also holds client records in the New Client form's format
(case_daniel_tan.json, case_benchmark.json); those go through the menu, not these tests.

Tests that need saved replies are skipped, with a message, until the case has been run
(or when the database can't be reached).
"""

import json
import os

import pytest

from app import ai_manager, data_manager, logic_manager

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(TESTS_DIR, "golden")
CROSSCHECK_DIR = os.path.join(TESTS_DIR, "crosscheck")
def _is_forecasting_case(path: str) -> bool:
    case = data_manager.load_case(path)
    return isinstance(case, dict) and "career_history" in case and "client_ref" in case


CASE_IDS = sorted(os.path.splitext(os.path.basename(path))[0]
                  for path in data_manager.list_sample_cases() if _is_forecasting_case(path))


def load_case(case_id: str) -> dict:
    case = data_manager.load_case(os.path.join(data_manager.sample_dir(), f"{case_id}.json"))
    assert case is not None, f"data/sample/{case_id}.json is missing or not valid JSON"
    return case


def first_review(case: dict) -> dict | None:
    """The case's review input, whether it's stored as 'review' or as a list under 'reviews'."""
    if isinstance(case.get("review"), dict):
        return case["review"]
    reviews = case.get("reviews") or []
    return reviews[0] if reviews else None


def saved_replies(case_id: str) -> dict:
    """The real AI replies saved in the database for this case, or skip the test if there are none."""
    client_ref = load_case(case_id)["client_ref"]
    replies = data_manager.load_raw_replies(client_ref)
    if not replies:
        pytest.skip(f"no saved AI replies for {client_ref} in the database yet - run the system on "
                    f"{case_id} first (python main.py --case data/sample/{case_id}.json)")
    return replies


def run_from_saved(case_id: str) -> dict:
    """Rebuild the forecast (and the review reading) from the saved replies. No API calls.

    Returns {"case", "forecast", "review", "review_data"}; review/review_data are None if
    the case has no review or the review hasn't been run yet.
    """
    case = load_case(case_id)
    replies = saved_replies(case_id)
    run = ai_manager.run_onboarding_forecast(None, case, replies)
    assert run["ok"], (f"the saved replies for {case_id} are incomplete (stopped at {run['failed_step']}) - "
                       f"run the system on {case_id} again so it finishes")
    review = first_review(case)
    review_data = None
    if review is not None:
        result = ai_manager.read_review_notes(None, review, run["forecast"], replies)
        review_data = result["data"] if result["ok"] else None
    return {"case": case, "forecast": run["forecast"], "review": review, "review_data": review_data}


def decisions(case_id: str) -> tuple[dict, dict | None]:
    """(onboarding decision, review decision or None) from the saved replies, as they'd look saved to JSON."""
    run = run_from_saved(case_id)
    onboarding = as_saved(logic_manager.assess_onboarding(run["case"], run["forecast"]))
    review = None
    if run["review"] is not None and run["review_data"] is not None:
        review = as_saved(logic_manager.assess_review(run["case"], run["review"], run["review_data"],
                                                      run["forecast"], onboarding))
    return onboarding, review


def golden_path(case_id: str, name: str) -> str:
    return os.path.join(GOLDEN_DIR, case_id, name)


def read_golden(case_id: str, name: str) -> dict:
    path = golden_path(case_id, name)
    if not os.path.exists(path):
        pytest.skip(f"tests/golden/{case_id}/{name} doesn't exist yet - run `python -m tests.update_golden` "
                    f"and check the result as a team")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def as_saved(value: dict) -> dict:
    """What a result looks like after saving to JSON and reading back (number keys become strings)."""
    return json.loads(json.dumps(value))