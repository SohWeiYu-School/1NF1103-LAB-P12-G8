"""
AI cross-check: is what the AI said TRUE?

Your real AI replies (data/raw/<client_ref>/) are compared with real answers your team
collected in tests/crosscheck/<case_id>/:
    market_history.json   official statistics, each with a source link
    pay_ranges.json       published salary data, each with a source link
    declaration.json      sources listed by reading the client's declaration

Nothing is invented:
  - a figure without an http(s) source link fails the test;
  - a 'from_text' that isn't word for word in the client's text fails the test;
  - rows nobody has filled in are ignored; a file with none filled in is skipped.

Run:  python -m pytest -q tests/test_ai_crosscheck.py -rs
"""

import json
import os
import re

import pytest

from app import logic_manager
from tests.helpers import CASE_IDS, CROSSCHECK_DIR, load_case, run_from_saved


def read_crosscheck(case_id: str, name: str) -> dict:
    path = os.path.join(CROSSCHECK_DIR, case_id, name)
    if not os.path.exists(path):
        pytest.skip(f"no cross-check file tests/crosscheck/{case_id}/{name}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def has_source(row: dict) -> bool:
    return str(row.get("source", "")).startswith(("http://", "https://"))


def normalise(text: str) -> str:
    return " ".join(str(text).lower().split())


# ---------------------------------------------------------------------------
# The comparisons (plain functions: given the AI's output and the real answers,
# return a list of (passed, explanation))
# ---------------------------------------------------------------------------

def compare_market_history(forecast: dict, rows: list[dict]) -> list[tuple[bool, str]]:
    markets = forecast["markets"]
    described = {m["market_id"]: " ".join(str(m.get(k, "")) for k in
                                         ("market_id", "name", "country", "indicator", "description")).lower()
                 for m in markets["relevant_markets"]}
    history = {market_id: {h["year"]: h["change_pct"] for h in series["history"]}
               for market_id, series in markets["series"].items()}
    results = []
    for row in rows:
        market_id = next((m for m, text in described.items() if re.search(row["market_match"], text)), None)
        ai_value = history.get(market_id, {}).get(row["year"]) if market_id else None
        ok = ai_value is not None and abs(ai_value - row["real_change_pct"]) <= row["allowed_difference"]
        results.append((ok, f"{row['market']} {row['year']}: AI said {ai_value} (market {market_id}), "
                            f"real {row['real_change_pct']} (allowed +/-{row['allowed_difference']}) - {row['source']}"))
    return results


def compare_pay(forecast: dict, rows: list[dict]) -> list[tuple[bool, str]]:
    pay = {(job["position_index"], row["year"]): logic_manager.pay_total(row, "typical")
           for job in forecast["earnings"]["positions"] for row in job["yearly"]}
    results = []
    for row in rows:
        ai_pay = pay.get((row["position_index"], row["year"]))
        ok = ai_pay is not None and row["min_total_pay"] <= ai_pay <= row["max_total_pay"]
        results.append((ok, f"{row['job']} {row['year']}: AI said {ai_pay}, published range "
                            f"{row['min_total_pay']}-{row['max_total_pay']} - {row['source']}"))
    return results


def compare_declaration(forecast: dict, items: list[dict]) -> list[tuple[bool, str]]:
    found = forecast["declaration"]["claimed_sources"]
    results = []
    for item in items:
        match = [s for s in found if s["source_type"] == item["source_type"]]
        if "amount" in item:
            match = [s for s in match if s["amount"] == item["amount"]]
        results.append((bool(match), f"{item['source_type']} (amount {item.get('amount', 'any')}): "
                                     f"'{item['from_text']}'"))
    return results


# ---------------------------------------------------------------------------
# Checks on the cross-check files themselves (so they stay legitimate)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_real_figure_has_a_source(case_id: str) -> None:
    problems = []
    for name, keys in (("market_history.json", ["real_change_pct"]),
                       ("pay_ranges.json", ["min_total_pay", "max_total_pay"])):
        path = os.path.join(CROSSCHECK_DIR, case_id, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for row in json.load(handle)["rows"]:
                if any(row.get(k) is not None for k in keys) and not has_source(row):
                    problems.append(f"{name}: '{row.get('market') or row.get('job')}' {row.get('year')} "
                                    f"has a figure but no source link")
    assert problems == [], problems


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_quote_is_really_in_the_clients_text(case_id: str) -> None:
    case = load_case(case_id)
    texts = {"declaration.json": case.get("sow_declaration", "")}
    problems = []
    for name, text in texts.items():
        path = os.path.join(CROSSCHECK_DIR, case_id, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for item in json.load(handle)["must_find"]:
                quote = item.get("from_text", "")
                if not quote or normalise(quote) not in normalise(text):
                    problems.append(f"{name}: '{quote}' is not word for word in the client's text")
    assert problems == [], problems


# ---------------------------------------------------------------------------
# Cross-checks of the AI's real replies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_id", CASE_IDS)
def test_market_history_matches_official_figures(case_id: str) -> None:
    expected = read_crosscheck(case_id, "market_history.json")
    rows = [r for r in expected["rows"] if r["real_change_pct"] is not None and has_source(r)]
    if not rows:
        pytest.skip(f"tests/crosscheck/{case_id}/market_history.json has no filled-in rows yet")
    results = compare_market_history(run_from_saved(case_id)["forecast"], rows)
    share = sum(ok for ok, _ in results) / len(results)
    wrong = [line for ok, line in results if not ok]
    assert share >= expected["pass_mark"], (f"only {share:.0%} of the AI's figures match the official ones "
                                            f"(need {expected['pass_mark']:.0%}). Wrong: {wrong}")


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_pay_within_published_ranges(case_id: str) -> None:
    expected = read_crosscheck(case_id, "pay_ranges.json")
    rows = [r for r in expected["rows"] if r["min_total_pay"] is not None and r["max_total_pay"] is not None
            and has_source(r)]
    if not rows:
        pytest.skip(f"tests/crosscheck/{case_id}/pay_ranges.json has no filled-in rows yet")
    results = compare_pay(run_from_saved(case_id)["forecast"], rows)
    share = sum(ok for ok, _ in results) / len(results)
    assert share >= expected["pass_mark"], [line for ok, line in results if not ok]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_declaration_read_correctly(case_id: str) -> None:
    expected = read_crosscheck(case_id, "declaration.json")
    if not expected["must_find"]:
        pytest.skip(f"tests/crosscheck/{case_id}/declaration.json is empty - list the sources from the declaration")
    results = compare_declaration(run_from_saved(case_id)["forecast"], expected["must_find"])
    missed = [line for ok, line in results if not ok]
    assert missed == [], f"the AI missed these sources from the declaration: {missed}"