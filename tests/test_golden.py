"""
Golden tests: the logic manager must keep giving the decision your team approved.

    expected output = tests/golden/<case_id>/expected_*.json
                      (made by `python -m tests.update_golden`, then checked by the team)
    actual output   = the logic manager run again on the same saved AI replies

Skipped until the case has been run and the expected files exist.
"""

import pytest

from tests.helpers import CASE_IDS, decisions, read_golden


def first_difference(expected: object, actual: object, path: str = "") -> str | None:
    """Where two results first differ, so a failure says exactly what changed."""
    if type(expected) is not type(actual):
        return f"{path or '<top>'}: expected {expected!r}, got {actual!r}"
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                return f"{path}/{key}: only in one of them"
            found = first_difference(expected[key], actual[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: expected {len(expected)} items, got {len(actual)}"
        for index, (e, a) in enumerate(zip(expected, actual)):
            found = first_difference(e, a, f"{path}/{index}")
            if found:
                return found
        return None
    if isinstance(expected, float):
        return None if actual == pytest.approx(expected, abs=0.01) else f"{path}: expected {expected}, got {actual}"
    return None if expected == actual else f"{path}: expected {expected!r}, got {actual!r}"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_onboarding_matches_approved_decision(case_id: str) -> None:
    expected = read_golden(case_id, "expected_onboarding.json")
    actual, _ = decisions(case_id)
    difference = first_difference(expected, actual)
    assert difference is None, difference


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_review_matches_approved_decision(case_id: str) -> None:
    expected = read_golden(case_id, "expected_review.json")
    _, actual = decisions(case_id)
    assert actual is not None, "the review couldn't be rebuilt from the saved replies"
    difference = first_difference(expected, actual)
    assert difference is None, difference


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_same_replies_give_the_same_decision(case_id: str) -> None:
    first, _ = decisions(case_id)
    second, _ = decisions(case_id)
    assert first == second


def test_first_difference_points_at_the_change() -> None:
    assert first_difference({"outcome": "PASSED"}, {"outcome": "RISKY"}) == "/outcome: expected 'PASSED', got 'RISKY'"
    assert first_difference({"a": [1.0]}, {"a": [1.004]}) is None
