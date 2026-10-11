"""Golden case test harness — see tests/golden/README.md for format."""

import glob
import json
import os

import pytest

from app import logic_manager

_GOLDEN_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_golden_cases():
    pattern = os.path.join(_GOLDEN_DIR, "case_*.json")
    cases = []
    for path in sorted(glob.glob(pattern)):
        with open(path) as f:
            data = json.load(f)
        data["_path"] = os.path.basename(path)
        cases.append(data)
    return cases


@pytest.mark.parametrize(
    "case",
    _load_golden_cases(),
    ids=lambda c: c["_path"],
)
def test_golden_case(case):
    result = logic_manager.assess_case(
        case["case_input"],
        case["benchmark"],
        case["declaration"],
        case["policy"],
    )
    assert result["breach_count"] == case["expected_breach_count"], (
        f"[{case['_path']}] {case['description']}\n"
        f"  Expected breach_count: {case['expected_breach_count']}\n"
        f"  Got:                   {result['breach_count']}\n"
        f"  Match results: {[(mr['typology_id'], mr['breached']) for mr in result['match_results']]}"
    )
