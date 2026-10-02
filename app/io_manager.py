"""
io_manager.py - the input/output layer: every boundary between the system and the user.

    * collects a case from the terminal, checks every answer and asks again if it's bad
    * checks case files against app/schemas/case_input.schema.json
    * formats single assessments and lists of them
    * ALL print() calls in the system live in this file

The ask_* functions take an `ask` function (normally read_answer, i.e. typing) so tests
can feed in typed answers.
"""

import datetime
import json
import os
from typing import Callable

from jsonschema import Draft202012Validator

from app import common

Ask = Callable[[str], str]
LINE = "-" * 72

OUTCOME_LABELS = {
    "PASSED": "PASSED",
    "MANUAL_REVIEW": "MANUAL REVIEW",
    "RISKY": "RISKY",
    "SERIOUS_RISK": "SERIOUS RISK",
}


# ============================================================================
# Printing
# ============================================================================

def show(text: str = "") -> None:
    print(text)


def show_error(text: str) -> None:
    print(f"  ! {text}")


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def money_range(values: dict) -> str:
    return f"{money(values['cautious'])} to {money(values['optimistic'])} (typical {money(values['typical'])})"


# ============================================================================
# Asking questions (bad answers are rejected and asked again)
# ============================================================================

def read_answer(question: str) -> str:
    """The normal way to ask: type into the terminal."""
    return input(question)


def ask_text(question: str, ask: Ask = read_answer, min_length: int = 1) -> str:
    while True:
        answer = ask(f"{question}: ").strip()
        if len(answer) >= min_length:
            return answer
        show_error(f"Please enter at least {min_length} character(s).")


def ask_int(question: str, ask: Ask = read_answer, minimum: int | None = None,
            maximum: int | None = None, allow_blank: bool = False) -> int | None:
    while True:
        answer = ask(f"{question}: ").strip()
        if allow_blank and answer == "":
            return None
        try:
            number = int(answer)
        except ValueError:
            show_error("Please enter a whole number.")
            continue
        if minimum is not None and number < minimum:
            show_error(f"Please enter {minimum} or more.")
        elif maximum is not None and number > maximum:
            show_error(f"Please enter {maximum} or less.")
        else:
            return number


def ask_money(question: str, ask: Ask = read_answer, allow_zero: bool = True) -> float:
    while True:
        answer = ask(f"{question}: ").strip().replace(",", "")
        try:
            amount = float(answer)
        except ValueError:
            show_error("Please enter an amount, e.g. 1500000.")
            continue
        if amount < 0 or (amount == 0 and not allow_zero):
            show_error("Please enter an amount above zero." if not allow_zero else "Amounts can't be negative.")
            continue
        return amount


def ask_date(question: str, ask: Ask = read_answer) -> str:
    while True:
        answer = ask(f"{question} (YYYY-MM-DD): ").strip()
        try:
            datetime.date.fromisoformat(answer)
            return answer
        except ValueError:
            show_error("Please enter a real date like 2026-09-24.")


def ask_choice(question: str, options: list[str], ask: Ask = read_answer) -> int:
    """Show a numbered list and return the index picked."""
    show(question)
    for number, option in enumerate(options, start=1):
        show(f"  {number}. {option}")
    return ask_int("Choose a number", ask, 1, len(options)) - 1


def ask_yes_no(question: str, ask: Ask = read_answer) -> bool:
    while True:
        answer = ask(f"{question} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        show_error("Please answer y or n.")


# ============================================================================
# Checking a case
# ============================================================================

def validate_case(case: object) -> list[str]:
    """Everything wrong with a case (from a file or typed in). [] means it's fine."""
    if not isinstance(case, dict):
        return ["the case must be a JSON object"]
    with open(os.path.join(common.SCHEMA_DIR, "case_input.schema.json"), "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = [f"{'/'.join(str(p) for p in e.path) or 'case'}: {e.message}"
              for e in Draft202012Validator(schema).iter_errors(case)]
    if errors:
        return errors

    this_year = int(case["current_date"][:4])
    jobs = case["career_history"]
    for index, job in enumerate(jobs):
        end = job["end_year"] if job["end_year"] is not None else this_year
        if job["start_year"] > end:
            errors.append(f"job {index + 1}: starts after it ends")
        if end > this_year:
            errors.append(f"job {index + 1}: ends after the assessment date")
        if index > 0 and job["start_year"] < (jobs[index - 1]["end_year"] or this_year):
            errors.append(f"job {index + 1}: starts before job {index} ends")
    if [j for j in jobs[:-1] if j["end_year"] is None]:
        errors.append("only the last job can be the current one (no end year)")
    first_year = jobs[0]["start_year"]
    for index, asset in enumerate(case["investments"]):
        if not first_year <= asset["year_acquired"] <= this_year:
            errors.append(f"asset {index + 1}: bought outside the career years {first_year}-{this_year}")
    total = sum(case["claims"]["asset_composition"].values())
    if not 0.95 <= total <= 1.05:
        errors.append(f"asset composition adds up to {total:.0%}, it should be 100%")
    for index, review in enumerate(case.get("reviews", [])):
        if review["current_date"] <= case["current_date"]:
            errors.append(f"review {index + 1}: must be after the onboarding date")
    return errors


# ============================================================================
# Collecting a new case from the terminal
# ============================================================================

def collect_job(number: int, this_year: int, earliest: int, ask: Ask = read_answer) -> dict:
    show(f"\nJob {number}")
    start = ask_int("  Start year", ask, earliest, this_year)
    end = ask_int("  End year (blank if this is the current job)", ask, start, this_year, allow_blank=True)
    return {
        "start_year": start, "end_year": end,
        "occupation": ask_text("  Job title", ask),
        "seniority": ask_text("  Seniority (e.g. Junior, Director, C-suite)", ask),
        "industry": ask_text("  Industry", ask),
        "employer_name": ask_text("  Employer", ask),
        "employer_size": ask_text("  Employer size (e.g. 1,000-5,000 employees)", ask),
        "country": ask_text("  Country", ask),
    }


def collect_asset(number: int, first_year: int, this_year: int, ask: Ask = read_answer) -> dict:
    show(f"\nAsset {number}")
    methods = ["cash", "loan", "asset_sale"]
    return {
        "asset_type": ask_text("  What is it (e.g. Residential property (condominium))", ask),
        "country": ask_text("  Country", ask),
        "year_acquired": ask_int("  Year bought", ask, first_year, this_year),
        "price_paid": ask_money("  Price paid (SGD)", ask, allow_zero=False),
        "current_declared_value": ask_money("  What the client says it's worth now (SGD)", ask),
        "payment_method": methods[ask_choice("  How was it paid for?", methods, ask)],
        "income_produced": ask_money("  Income it produces a year (SGD, 0 if none)", ask),
    }


def collect_composition(ask: Ask = read_answer) -> dict:
    """Ask for the % split of his wealth until it adds up to 100%."""
    while True:
        show("\nHow the client says his wealth is split (percent)")
        split = {key: ask_money(f"  {label} %", ask) / 100
                 for key, label in [("property", "Property"), ("listed_equities", "Listed shares"),
                                    ("private_business", "Private business"), ("cash", "Cash")]}
        if 0.95 <= sum(split.values()) <= 1.05:
            return split
        show_error(f"That adds up to {sum(split.values()):.0%}. Please make it 100%.")


def collect_case(ask: Ask = read_answer) -> dict:
    """Ask the officer for a whole case, one checked answer at a time."""
    show(LINE)
    show("New client case")
    show(LINE)
    current_date = ask_date("Assessment date", ask)
    this_year = int(current_date[:4])
    case = {
        "client_ref": ask_text("Client reference (e.g. SOW-2026-0150)", ask),
        "current_date": current_date,
        "name": ask_text("Client name (never sent to the AI)", ask),
        "nationality": ask_text("Nationality (never sent to the AI)", ask),
    }
    age = ask_int("Age", ask, 18, 120)
    job_count = ask_int("How many jobs has he had", ask, 1, 15)
    jobs, earliest = [], this_year - (age - 16)
    for number in range(1, job_count + 1):
        job = collect_job(number, this_year, earliest, ask)
        jobs.append(job)
        earliest = job["end_year"] or this_year
        if job["end_year"] is None and number < job_count:
            show_error("Only the last job can be the current one; the remaining jobs are skipped.")
            break
    current = jobs[-1]
    case["career"] = {"occupation": current["occupation"], "seniority": current["seniority"],
                      "industry": current["industry"], "employer_name": current["employer_name"],
                      "employer_size": current["employer_size"], "country_of_residence": current["country"],
                      "age": age, "career_start_year": jobs[0]["start_year"]}
    case["career_history"] = jobs
    asset_count = ask_int("How many assets does he hold", ask, 0, 20)
    case["investments"] = [collect_asset(n, jobs[0]["start_year"], this_year, ask) for n in range(1, asset_count + 1)]
    show("\nClient claims")
    countries = ask_text("Countries his wealth came from (comma separated)", ask)
    case["claims"] = {
        "declared_net_worth": ask_money("Declared net worth (SGD)", ask, allow_zero=False),
        "expected_aum": ask_money("Expected assets to be managed by the bank (SGD)", ask, allow_zero=False),
        "asset_composition": collect_composition(ask),
        "wealth_countries": [c.strip() for c in countries.split(",") if c.strip()],
        "pep_status": ask_text("PEP status (e.g. None)", ask),
    }
    case["sow_declaration"] = ask_text("Source of wealth declaration (one paragraph)", ask, min_length=50)
    case["reviews"] = []
    return case


def collect_review(case: dict, ask: Ask = read_answer) -> dict:
    """Ask for a periodic review: date, current wealth and the officer's notes."""
    show(LINE)
    show(f"Periodic review for {case['client_ref']}")
    show(LINE)
    while True:
        review_date = ask_date("Review date", ask)
        if review_date > case["current_date"]:
            break
        show_error(f"The review must be after the onboarding date {case['current_date']}.")
    return {"client_ref": case["client_ref"], "current_date": review_date,
            "current_wealth": ask_money("Client's wealth now (SGD)", ask, allow_zero=False),
            "officer_notes": ask_text("What happened in his accounts since the last review", ask, min_length=20)}


# ============================================================================
# Menus and progress
# ============================================================================

MENU = ["Assess a sample case", "Enter a new case", "Run a periodic review",
        "List saved assessments", "View a saved assessment", "Quit"]


def ask_menu(ask: Ask = read_answer) -> int:
    show()
    show(LINE)
    show("Source of Wealth forecasting  (INF1103 Group 8)")
    show(LINE)
    return ask_choice("What would you like to do?", MENU, ask)


def show_ai_progress(results: dict) -> None:
    """One line per AI request: where the reply came from and whether it passed the checks."""
    for name, result in results.items():
        source = "saved reply" if result["from_cache"] else f"API, {result['attempts']} attempt(s)"
        status = "ok" if result["ok"] else f"FAILED - {result['error']}"
        show(f"  {name:<40} {source:<20} {status}")


# ============================================================================
# Formatting assessments
# ============================================================================

def format_onboarding(assessment: dict) -> str:
    """A readable onboarding report."""
    lines = [LINE, f"ONBOARDING  {assessment['case_ref']}  ({assessment['assessed_on']})", LINE,
             f"Outcome:      {OUTCOME_LABELS[assessment['outcome']]}",
             f"Action:       {assessment['routing']}",
             f"Next review:  {assessment['next_review_date']}",
             f"Policy:       {assessment['policy_version']}", "", "Why:"]
    lines += [f"  - {reason}" for reason in assessment["reasons"]]

    lines += ["", "Kinds of person his figures were checked against:"]
    for row in assessment["types"]:
        mark = "fits" if row["fits"] else "does not fit"
        lines.append(f"  {row['type_id']}  {row['name']}  [{mark}, fit {row['fit_score']:.2f}, "
                     f"score {row['score']:.0%}]")
        lines.append(f"      route: {row['route']} ({row['route_years']})")
        lines.append(f"      wealth today: {money_range(row['wealth_now'])}; "
                     f"purchases affordable: {row['purchases_affordable']}")

    lines += ["", "Purchases (as-declared version):"]
    for row in assessment["affordability"]:
        verdict = "affordable" if row["affordable"] else "NOT affordable"
        lines.append(f"  {row['year']}  asset {row['asset_index']} ({row['payment_method']}): needed "
                     f"{money(row['needed'])}, had up to {money(row['available']['optimistic'])}  -> {verdict}")

    failed = [c for c in assessment["asset_checks"] if c["failed"]]
    if failed:
        lines += ["", "Asset checks that failed:"] + [f"  - {c['detail']}" for c in failed]

    lines += ["", "Documents to request:"]
    lines += [f"  {n}. {d['document']}  ({d['reason']})" for n, d in enumerate(assessment["documents"], 1)] or ["  none"]

    lines += ["", "Watch dates:"]
    lines += [f"  {w['date']}  {w['what']}" for w in assessment["watch_dates"]] or ["  none"]

    quality = assessment["data_quality"]
    lines += ["", f"Data quality: {quality['market_figures']} market figures, "
                  f"{quality['unverified_share']:.0%} from AI memory or missing."]
    return "\n".join(lines)


def format_review(assessment: dict) -> str:
    lines = [LINE, f"REVIEW  {assessment['case_ref']}  ({assessment['review_date']})", LINE,
             f"Outcome:      {OUTCOME_LABELS[assessment['outcome']]}",
             f"Action:       {assessment['routing']}",
             f"Year type:    {assessment['scenario']}",
             f"Next review:  {assessment['next_review_date']}", "", "Findings:"]
    for finding in assessment["findings"]:
        mark = "FAILED" if finding["failed"] else "ok"
        lines.append(f"  [{mark:^6}] {finding['rule_id']}: {finding['detail']}")
    lines += ["", "Running score per kind of person:"]
    for row in assessment["type_scores"]:
        lines.append(f"  {row['type_id']}  {row['previous']:.0%} -> {row['score']:.0%}  (review fit {row['review_fit']:.3f})")
    if assessment["documents"]:
        lines += ["", "Documents to request:"]
        lines += [f"  {n}. {d['document']}  ({d['reason']})" for n, d in enumerate(assessment["documents"], 1)]
    return "\n".join(lines)


def format_assessment_list(records: list[dict]) -> str:
    if not records:
        return "No saved assessments match."
    lines = [f"{'Client':<16} {'Kind':<20} {'Date':<12} Outcome"]
    for record in records:
        assessment = record["assessment"]
        date = assessment.get("assessed_on") or assessment.get("review_date", "")
        lines.append(f"{record['client_ref']:<16} {record['kind']:<20} {date:<12} "
                     f"{OUTCOME_LABELS.get(assessment.get('outcome'), '?')}")
    return "\n".join(lines)


def show_onboarding(assessment: dict) -> None:
    show(format_onboarding(assessment))


def show_review(assessment: dict) -> None:
    show(format_review(assessment))


def show_assessment_list(records: list[dict]) -> None:
    show(format_assessment_list(records))
