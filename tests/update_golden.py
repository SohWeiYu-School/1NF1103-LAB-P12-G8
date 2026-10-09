"""
Create the logic manager's expected output (the "answer sheet") from your real AI replies.

    python -m tests.update_golden

For each case in data/sample that has AI replies saved in data/raw/<client_ref>/, this
runs the logic manager and saves its decision as:
    tests/golden/<case_id>/expected_onboarding.json
    tests/golden/<case_id>/expected_review.json      (if the case has a review)

Then CHECK THOSE FILES AS A TEAM before committing: is the outcome right for this
client? Once approved, test_golden.py checks the logic always gives that decision.

Run this again only when you deliberately change the logic, or get new AI replies.
"""

import json
import logging
import os

from app import data_manager
from tests.helpers import CASE_IDS, decisions, golden_path, load_case


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    log = logging.getLogger("update_golden")
    for case_id in CASE_IDS:
        client_ref = load_case(case_id)["client_ref"]
        if not data_manager.load_raw_replies(client_ref):
            log.warning("%s: skipped - no AI replies in data/raw/%s yet (run the system on it first)",
                        case_id, client_ref)
            continue
        try:
            onboarding, review = decisions(case_id)
        except AssertionError as problem:
            log.warning("%s: skipped - %s", case_id, problem)
            continue
        except Exception:                              # pytest.skip raises too; show anything else
            log.exception("%s: could not build the decision", case_id)
            continue

        os.makedirs(os.path.dirname(golden_path(case_id, "x")), exist_ok=True)
        files = [("expected_onboarding.json", onboarding)] + ([("expected_review.json", review)] if review else [])
        for name, result in files:
            with open(golden_path(case_id, name), "w", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, sort_keys=True)
                handle.write("\n")
        log.warning("%s: saved %s - onboarding %s%s. Now check these files as a team.", case_id,
                    ", ".join(name for name, _ in files), onboarding["outcome"],
                    f", review {review['outcome']}" if review else "")


if __name__ == "__main__":
    main()
