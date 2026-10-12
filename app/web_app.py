"""
web_app.py - simple web version of the input layer (Flask).
 
Put this file in the app/ folder (next to io_manager.py and data_manager.py).
Run it from the PROJECT ROOT (the folder that contains app/):
 
    pip install flask
    python -m app.web_app
 
Then open http://127.0.0.1:5000 in a browser.
 
It reads and writes the SAME record layout the forecasting uses:
 
    client_ref, current_date, name, nationality,
    career {occupation, seniority, industry, employer_name, employer_size,
            country_of_residence, age, career_start_year},
    career_history [ {start_year, end_year, occupation, ...} ],
    investments [ {asset_type, country, year_acquired, price_paid, ...} ],
    claims {declared_net_worth, expected_aum, asset_composition{...},
            wealth_countries [...], pep_status},
    sow_declaration (text), reviews []
 
The form shows text; build_record() turns it back into numbers / lists / fractions,
and validate_case() (from io_manager) checks the result before anything is saved.
"""
 
import datetime
import io
import os
import shutil
import tempfile
import time
import threading
import uuid

from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)
from jinja2 import DictLoader
from werkzeug.utils import secure_filename

from app import ai_manager, logic_manager, common
from app.data_manager import (
    save_case_record,
    load_all_records,
    upload_supporting_document,
    download_supporting_document,
    save_assessment,
    load_all_assessments,
    sync_json_to_mongodb,
    save_forecast_assessment,
    load_raw_replies,
    save_raw_replies,
    save_forecast,
    load_assessment as _load_assessment,
    load_client_assessments,
    load_assessment_by_id,
)
from app.io_manager import generate_client_id, validate_case, _number, show_crime_scan_result, show_benchmark_result, show_onboarding

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")

_client = ai_manager.init_client()

ASSESS_STEPS = [
    "Fetching sector research reports",
    "Verifying research sources",
    "Generating sector typologies",
    "Verifying typology citations",
    "Parsing source of wealth declaration",
    "Normalising jurisdictions",
    "Matching typology indicators",
    "Running benchmark",
    "Running onboarding forecast",
    "Saving results",
]

_assessment_jobs = {}  # job_id -> {status, step_num, step, result, error, client_ref}


# ============================================================================
# What the form contains.  Each field is (key, label, kind).
# kind: text | int | money | percent | date | textarea | pep | payment | countries
# ============================================================================
 
PEP_OPTIONS = ["None", "Domestic PEP", "Foreign PEP",
               "International organisation PEP", "Family member / close associate"]
PAYMENT_OPTIONS = ["cash", "loan", "asset_sale"]
 
CLIENT_FIELDS = [
    ("name", "Full name", "text"),
    ("nationality", "Nationality", "text"),
    ("country_of_residence", "Country of residence", "text"),
    ("age", "Age", "int"),
    ("current_date", "Assessment date", "date"),
]
JOB_FIELDS = [
    ("start_year", "Start year", "int"),
    ("end_year", "End year (blank for the current job)", "int"),
    ("occupation", "Job title", "text"),
    ("seniority", "Seniority", "text"),
    ("industry", "Industry", "text"),
    ("employer_name", "Employer", "text"),
    ("employer_size", "Employer size (e.g. 10000)", "text"),
    ("country", "Country of the job", "text"),
]
ASSET_FIELDS = [
    ("asset_type", "Asset (e.g. Property - Condo)", "text"),
    ("country", "Country", "text"),
    ("year_acquired", "Year bought", "int"),
    ("price_paid", "Price paid (SGD)", "money"),
    ("current_declared_value", "Value the client declares now (SGD)", "money"),
    ("payment_method", "How it was paid for", "payment"),
    ("income_produced", "Income it produces per year (SGD, 0 if none)", "money"),
]
COMPOSITION_FIELDS = [
    ("property", "Property (%)", "percent"),
    ("listed_equities", "Listed equities (%)", "percent"),
    ("private_business", "Private business (%)", "percent"),
    ("cash", "Cash (%)", "percent"),
]
CLAIM_FIELDS = [
    ("declared_net_worth", "Declared net worth (SGD)", "money"),
    ("expected_aum", "Expected assets to be managed by the bank (SGD)", "money"),
    ("pep_status", "PEP status", "pep"),
    ("wealth_countries", "Countries the wealth came from (comma separated)", "countries"),
    ("biggest_wealth_jump", "Biggest single-period wealth increase in SGD (leave blank if unknown)", "text"),
    ("wealth_jump_years", "Over how many years did this increase occur (leave blank if unknown)", "text"),
    ("declared_counterparties", "Known counterparties — banks, brokers, firms (comma separated, leave blank if none)", "text"),
]
SOW_FIELDS = [
    ("sow_declaration", "Declaration (at least 50 characters)", "textarea"),
]
 
# (form prefix, title, fields, "dict" = one set of boxes / "list" = many entries)
SECTIONS = [
    ("client", "Client", CLIENT_FIELDS, "dict"),
    ("jobs", "Career history (include the current job and leave its end year blank)",
     JOB_FIELDS, "list"),
    ("assets", "Assets the client still owns", ASSET_FIELDS, "list"),
    ("composition", "How the client says their wealth is split (must add up to 100%)",
     COMPOSITION_FIELDS, "dict"),
    ("claims", "Client declarations", CLAIM_FIELDS, "dict"),
    ("sow", "Source of wealth declaration", SOW_FIELDS, "dict"),
]
 
 
# ============================================================================
# Record  <->  text shown in the form
# ============================================================================
 
def today() -> str:
    return datetime.date.today().isoformat()
 
 
def trim(number) -> str:
    """60000.0 -> '60000', 7.5 -> '7.5'. Anything that isn't a number is shown as it is."""
    try:
        number = round(float(number), 2)
    except (TypeError, ValueError):
        return str(number)
    return str(int(number)) if number == int(number) else str(number)
 
 
def to_text(kind: str, value) -> str:
    """How one stored value is shown in a box."""
    if value is None:
        return ""
    if kind == "percent":
        try:
            return trim(float(value) * 100)       # 0.25 -> '25'
        except (TypeError, ValueError):
            return str(value)
    if kind == "countries":
        return ", ".join(value) if isinstance(value, list) else str(value)
    if kind in ("int", "money"):
        return trim(value)
    return str(value)
 
 
def values_from_record(record: dict) -> dict:
    """The text for every box, taken from a saved record."""
    career = record.get("career") or {}
    claims = record.get("claims") or {}
    raw = {
        "client": {"name": record.get("name"),
                   "nationality": record.get("nationality"),
                   "country_of_residence": career.get("country_of_residence"),
                   "age": career.get("age"),
                   "current_date": record.get("current_date")},
        "jobs": record.get("career_history") or [],
        "assets": record.get("investments") or [],
        "composition": claims.get("asset_composition") or {},
        "claims": claims,
        "sow": {"sow_declaration": record.get("sow_declaration")},
    }
    values = {}
    for prefix, title, fields, kind in SECTIONS:
        if kind == "dict":
            values[prefix] = {key: to_text(k, raw[prefix].get(key)) for key, label, k in fields}
        else:
            values[prefix] = [{key: to_text(k, item.get(key)) for key, label, k in fields}
                              for item in raw[prefix]]
    return values
 
 
def values_from_form(form) -> dict:
    """The text for every box, taken from the submitted form."""
    values = {}
    for prefix, title, fields, kind in SECTIONS:
        keys = [field[0] for field in fields]
        if kind == "dict":
            values[prefix] = {key: form.get(f"{prefix}.{key}", "").strip() for key in keys}
        else:
            # every entry repeats the same field names, so getlist gives one column per field
            columns = [form.getlist(f"{prefix}.{key}") for key in keys]
            rows = []
            for cells in zip(*columns):
                row = {key: cell.strip() for key, cell in zip(keys, cells)}
                if any(row.values()):               # skip completely empty entries
                    rows.append(row)
            values[prefix] = rows
    return values
 
 
# ============================================================================
# Text from the form  ->  a record (numbers, lists, fractions)
# ============================================================================
 
def read_year(text, label, problems, blank_ok=False):
    if text == "" and blank_ok:
        return None
    if text.isdigit() and 1900 <= int(text) <= 2100:
        return int(text)
    problems.append(f"{label} must be a year such as 2015.")
    return 0
 
 
def read_amount(text, label, problems, allow_zero=True, blank_zero=False):
    if text == "" and blank_zero:
        return 0.0
    number = _number(text)
    if number is None or number < 0 or (number == 0 and not allow_zero):
        wanted = "an amount like 250000" if allow_zero else "an amount above zero, like 250000"
        problems.append(f"{label} must be {wanted}.")
        return 0.0
    return number
 
 
def build_record(values: dict, client_ref: str, old: dict | None = None):
    """Turn the form text into a record. Returns (record, problems, field_errors).

    field_errors maps HTML input name -> short inline message for the form UI.
    For list items the key is  prefix.N.field  (0-based N).
    """
    problems = []
    field_errors = {}

    def _p(msg, field=None, short=None):
        problems.append(msg)
        if field:
            field_errors[field] = short or msg

    record = dict(old) if old else {}
    record["client_ref"] = client_ref
 
    # ---- client ----
    client = values["client"]
    for key, label in [("name", "Full name"), ("nationality", "Nationality"),
                       ("country_of_residence", "Country of residence")]:
        if not client[key]:
            _p(f"{label} is required.", f"client.{key}", "Required.")
    record["name"] = client["name"]
    record["nationality"] = client["nationality"]
 
    try:
        datetime.date.fromisoformat(client["current_date"])
    except ValueError:
        _p("Assessment date must be a real date.", "client.current_date", "Must be a real date.")
    record["current_date"] = client["current_date"]
 
    if client["age"].isdigit() and 18 <= int(client["age"]) <= 120:
        age = int(client["age"])
    else:
        _p("Age must be a whole number between 18 and 120.", "client.age", "Must be 18“120.")
        age = 0
 
    # ---- career history ----
    jobs = []
    for number, row in enumerate(values["jobs"], start=1):
        label = f"Job {number}"
        i = number - 1
        for key, text in [("occupation", "job title"), ("seniority", "seniority"),
                          ("industry", "industry"), ("employer_name", "employer"),
                          ("employer_size", "employer size"), ("country", "country")]:
            if not row[key]:
                _p(f"{label}: {text} is required.", f"jobs.{i}.{key}", "Required.")
        n = len(problems)
        sy = read_year(row["start_year"], f"{label}: start year", problems)
        if len(problems) > n:
            field_errors[f"jobs.{i}.start_year"] = "Must be a year like 2015."
        n = len(problems)
        ey = read_year(row["end_year"], f"{label}: end year", problems, blank_ok=True)
        if len(problems) > n:
            field_errors[f"jobs.{i}.end_year"] = "Must be a year like 2020."
        jobs.append({
            "start_year": sy,
            "end_year": ey,
            "occupation": row["occupation"],
            "seniority": row["seniority"],
            "industry": row["industry"],
            "employer_name": row["employer_name"],
            "employer_size": row["employer_size"],
            "country": row["country"],
        })
    if not jobs:
        _p("Add at least one job (the current job has no end year).")
    jobs.sort(key=lambda job: job["start_year"])
    record["career_history"] = jobs
 
    # the "career" block is the current job + age + residence (same as the forecasting builds it)
    if jobs:
        current = jobs[-1]
        career = dict(record.get("career") or {})
        career.update({"occupation": current["occupation"], "seniority": current["seniority"],
                       "industry": current["industry"], "employer_name": current["employer_name"],
                       "employer_size": current["employer_size"],
                       "country_of_residence": client["country_of_residence"],
                       "age": age, "career_start_year": jobs[0]["start_year"]})
        record["career"] = career
 
    # ---- assets ----
    investments = []
    for number, row in enumerate(values["assets"], start=1):
        label = f"Asset {number}"
        i = number - 1
        for key, text in [("asset_type", "name"), ("country", "country")]:
            if not row[key]:
                _p(f"{label}: {text} is required.", f"assets.{i}.{key}", "Required.")
        if row["payment_method"] not in PAYMENT_OPTIONS:
            _p(f"{label}: choose how it was paid for.", f"assets.{i}.payment_method", "Choose one.")
        n = len(problems)
        yr  = read_year(row["year_acquired"], f"{label}: year bought", problems)
        if len(problems) > n:
            field_errors[f"assets.{i}.year_acquired"] = "Must be a year like 2010."
        n = len(problems)
        pp  = read_amount(row["price_paid"], f"{label}: price paid", problems, allow_zero=False)
        if len(problems) > n:
            field_errors[f"assets.{i}.price_paid"] = "Must be above zero."
        n = len(problems)
        cdv = read_amount(row["current_declared_value"], f"{label}: declared value", problems)
        if len(problems) > n:
            field_errors[f"assets.{i}.current_declared_value"] = "Must be a number."
        n = len(problems)
        inc = read_amount(row["income_produced"], f"{label}: income", problems, blank_zero=True)
        if len(problems) > n:
            field_errors[f"assets.{i}.income_produced"] = "Must be a number."
        investments.append({
            "asset_type": row["asset_type"],
            "country": row["country"],
            "year_acquired": yr,
            "price_paid": pp,
            "current_declared_value": cdv,
            "payment_method": row["payment_method"],
            "income_produced": inc,
        })
    record["investments"] = investments
 
    # ---- claims ----
    claims = dict(record.get("claims") or {})
    form_claims = values["claims"]
    n = len(problems)
    claims["declared_net_worth"] = read_amount(form_claims["declared_net_worth"],
                                               "Declared net worth", problems, allow_zero=False)
    if len(problems) > n:
        field_errors["claims.declared_net_worth"] = "Must be above zero."
    n = len(problems)
    claims["expected_aum"] = read_amount(form_claims["expected_aum"],
                                         "Expected assets to be managed", problems,
                                         allow_zero=False)
    if len(problems) > n:
        field_errors["claims.expected_aum"] = "Must be above zero."
    if not form_claims["pep_status"]:
        _p("PEP status is required.", "claims.pep_status", "Required.")
    claims["pep_status"] = form_claims["pep_status"]

    countries = [c.strip() for c in form_claims["wealth_countries"].split(",") if c.strip()]
    if not countries:
        _p("Enter at least one country the wealth came from.",
           "claims.wealth_countries", "Enter at least one country.")
    claims["wealth_countries"] = countries

    jump_str = form_claims.get("biggest_wealth_jump", "").strip()
    claims["biggest_wealth_jump"] = _number(jump_str) if jump_str else None
    yrs_str = form_claims.get("wealth_jump_years", "").strip()
    try:
        claims["wealth_jump_years"] = float(yrs_str) if yrs_str else None
    except ValueError:
        claims["wealth_jump_years"] = None
    cp_str = form_claims.get("declared_counterparties", "").strip()
    claims["declared_counterparties"] = [c.strip() for c in cp_str.split(",") if c.strip()]

    split = {}
    for key, label, kind in COMPOSITION_FIELDS:
        n = len(problems)
        split[key] = read_amount(values["composition"][key], label.replace(" (%)", ""), problems)
        if len(problems) > n:
            field_errors[f"composition.{key}"] = "Must be a number."
    if not 95 <= sum(split.values()) <= 105:
        _p(f"The wealth split adds up to {trim(sum(split.values()))}%, it must be 100%.")
    claims["asset_composition"] = {key: value / 100 for key, value in split.items()}
    record["claims"] = claims

    # ---- declaration ----
    record["sow_declaration"] = values["sow"]["sow_declaration"]
    sow_len = len(record["sow_declaration"])
    if sow_len < 50:
        _p("The source of wealth declaration must be at least 50 characters.",
           "sow.sow_declaration", f"Too short ({sow_len}/50 characters minimum).")

    record.setdefault("reviews", [])
    record.setdefault("supporting_documents", [])
    return record, problems, field_errors
 
 
def check_record(record: dict) -> list:
    """The forecasting's own checks (schema, job dates, ...). Returns problems."""
    if not record.get("career_history"):
        return ["Add at least one job."]
    # supporting_documents isn't part of the case layout, so leave it out of the check
    case = {key: value for key, value in record.items() if key != "supporting_documents"}
    return validate_case(case)
 
 
# ============================================================================
# Helpers
# ============================================================================
 
def get_record(client_ref: str):
    """Find one client record by reference. Returns None if not found."""
    for record in load_all_records():
        if record.get("client_ref") == client_ref:
            return record
    return None
 
 
def show_form(heading: str, values: dict, problems: list, field_errors: dict = None):
    return render_template("client_form.html", heading=heading, values=values,
                           problems=problems, SECTIONS=SECTIONS,
                           field_errors=field_errors or {})


# ============================================================================
# Assessment helpers
# ============================================================================

def _whole_number(value):
    try:
        return int(float(str(value).replace(",", "").replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


def _crime_scan_input(record):
    career = record.get("career") or {}
    return {
        "client_ref":           record["client_ref"],
        "occupation":           career.get("occupation", ""),
        "industry":             career.get("industry", ""),
        "age":                  career.get("age"),
        "career_start_year":    career.get("career_start_year"),
        "country":              career.get("country_of_residence", ""),
        "country_of_residence": career.get("country_of_residence", ""),
        "declaration_text":     record.get("sow_declaration", ""),
    }


def _run_crime_scan(case_input):
    policy = common.policy()
    research, _ = ai_manager.get_ai_research(case_input)
    clean_research = None
    if research is not None:
        clean_research, _ = logic_manager.verify_research(research, policy)
    typology_response, _ = ai_manager.get_typologies(case_input, research)
    if typology_response is not None and clean_research is not None:
        verified_typs, _ = logic_manager.verify_typology_sources(
            typology_response.get("sector_typologies", []), clean_research, policy,
        )
        typology_response = {**typology_response, "sector_typologies": verified_typs}
    declaration, _ = ai_manager.get_declaration(case_input)
    if declaration is not None:
        declaration, _ = logic_manager.normalize_declaration_jurisdictions(declaration)
    crime_result = None
    if typology_response is not None and declaration is not None:
        crime_result = logic_manager.assess_case(case_input, typology_response, declaration, policy)
    return research, typology_response, declaration, crime_result


def _run_benchmark(record, case_input):
    claims = record.get("claims") or {}
    comp   = claims.get("asset_composition") or {}
    nw     = claims.get("declared_net_worth") or 0
    benchmark_input = {
        **case_input,
        "declared_net_worth":      _whole_number(nw),
        "listed_equities":         _whole_number((comp.get("listed_equities") or 0) * nw),
        "cash":                    _whole_number((comp.get("cash") or 0) * nw),
        "property":                _whole_number((comp.get("property") or 0) * nw),
        "private_business":        _whole_number((comp.get("private_business") or 0) * nw),
        "biggest_wealth_jump":     claims.get("biggest_wealth_jump"),
        "wealth_jump_years":       float(claims.get("wealth_jump_years") or 0.0),
        "declared_counterparties": claims.get("declared_counterparties", []),
        "declared_countries":      claims.get("wealth_countries", []),
    }
    if any(benchmark_input[k] is None for k in
           ("declared_net_worth", "listed_equities", "cash", "property", "private_business")):
        return None
    return logic_manager.run_benchmark(benchmark_input, ai_data_fn=ai_manager.get_ai_benchmark)


def _run_assessment_job(job_id, client_ref, record):
    job = _assessment_jobs[job_id]

    def step(n):
        job["step_num"] = n
        job["step"] = ASSESS_STEPS[n - 1]

    try:
        case_input = _crime_scan_input(record)
        policy = common.policy()

        step(1)
        research, _ = ai_manager.get_ai_research(case_input)

        step(2)
        clean_research = None
        if research is not None:
            clean_research, _ = logic_manager.verify_research(research, policy)
        time.sleep(4)

        step(3)
        typology_response, _ = ai_manager.get_typologies(case_input, research)

        step(4)
        if typology_response is not None and clean_research is not None:
            verified_typs, _ = logic_manager.verify_typology_sources(
                typology_response.get("sector_typologies", []), clean_research, policy,
            )
            typology_response = {**typology_response, "sector_typologies": verified_typs}
        time.sleep(4)

        step(5)
        declaration, _ = ai_manager.get_declaration(case_input)

        step(6)
        if declaration is not None:
            declaration, _ = logic_manager.normalize_declaration_jurisdictions(declaration)
        time.sleep(4)

        step(7)
        crime_result = None
        if typology_response is not None and declaration is not None:
            crime_result = logic_manager.assess_case(case_input, typology_response, declaration, policy)
            show_crime_scan_result(crime_result, typology_response, research)
        time.sleep(4)

        step(8)
        benchmark = _run_benchmark(record, case_input)
        if benchmark is not None:
            show_benchmark_result(benchmark)
        time.sleep(4)

        step(9)
        forecast_assessment = None
        cache = load_raw_replies(client_ref)
        run = ai_manager.run_onboarding_forecast(_client, record, cache)
        save_raw_replies(client_ref, run["results"])
        if run["ok"]:
            save_forecast(client_ref, run["forecast"])
            forecast_assessment = logic_manager.assess_onboarding(record, run["forecast"])
            save_forecast_assessment(client_ref, "onboarding", forecast_assessment)
            show_onboarding(forecast_assessment)

        step(10)
        time.sleep(4)
        assessment_id = str(uuid.uuid4())
        save_assessment({
            "assessment_id": assessment_id,
            "client_ref":    client_ref,
            "kind":          "onboarding",
            "assessed_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "crime_scan": {
                "research":    research,
                "typologies":  typology_response,
                "declaration": declaration,
            },
            "crime_result": crime_result,
            "benchmark":    benchmark,
            "forecast":     forecast_assessment,
            "assessment":   forecast_assessment,
        })

        job["result"] = {
            "record":        record,
            "crime_result":  crime_result,
            "benchmark":     benchmark,
            "forecast":      forecast_assessment,
            "assessment_id": assessment_id,
            "assessed_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        job["status"] = "done"

    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)


# ============================================================================
# Pages
# ============================================================================
 
@app.route("/")
def home():
    return render_template("home.html", clients=load_all_records())
 
 
@app.route("/find")
def find():
    client_ref = request.args.get("ref", "").strip().upper()
    if client_ref and get_record(client_ref):
        return redirect(url_for("view_client", client_ref=client_ref))
    flash(f"No client found with reference '{client_ref}'.")
    return redirect(url_for("home"))
 
 
@app.route("/client/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        values = values_from_form(request.form)
        record, problems, field_errors = build_record(values, generate_client_id())
        if not problems:
            problems = check_record(record)
            field_errors = {}
        if not problems:
            if save_case_record(record):
                flash("Client saved.")
                return redirect(url_for("view_client", client_ref=record["client_ref"]))
            problems = ["The client could not be saved. Check the database connection."]
        return show_form("New client", values, problems, field_errors)
 
    return show_form("New client", values_from_record({"current_date": today()}), [])
 
 
@app.route("/client/<client_ref>")
def view_client(client_ref):
    record = get_record(client_ref)
    if record is None:
        abort(404)
    assessments = load_client_assessments(client_ref)
    return render_template("client_view.html", record=record,
                           values=values_from_record(record), SECTIONS=SECTIONS,
                           assessments=assessments)


@app.route("/assessment/<assessment_id>")
def view_assessment(assessment_id):
    saved = load_assessment_by_id(assessment_id)
    if saved is None:
        abort(404)
    record = get_record(saved["client_ref"])
    if record is None:
        abort(404)
    return render_template("assess_result.html",
        record=record,
        crime_result=saved.get("crime_result"),
        benchmark=saved.get("benchmark"),
        forecast=saved.get("forecast"),
        assessed_at=saved.get("assessed_at"),
        assessment_id=assessment_id,
    )
 
 
@app.route("/client/<client_ref>/edit", methods=["GET", "POST"])
def edit_client(client_ref):
    old = get_record(client_ref)
    if old is None:
        abort(404)
 
    if request.method == "POST":
        values = values_from_form(request.form)
        record, problems, field_errors = build_record(values, client_ref, old)
        if not problems:
            problems = check_record(record)
            field_errors = {}
        if not problems:
            if save_case_record(record):
                flash("Changes saved.")
                return redirect(url_for("view_client", client_ref=client_ref))
            problems = ["The changes could not be saved. Check the database connection."]
        return show_form(f"Edit {client_ref}", values, problems, field_errors)
 
    return show_form(f"Edit {client_ref}", values_from_record(old), [])
 
 
@app.route("/client/<client_ref>/documents", methods=["POST"])
def upload_documents(client_ref):
    record = get_record(client_ref)
    if record is None:
        abort(404)
 
    added = 0
    chosen = 0
    for uploaded in request.files.getlist("files"):
        if not uploaded.filename:
            continue
        chosen += 1
        name = secure_filename(uploaded.filename) or "document"
 
        # upload_supporting_document wants a file path, so save to a temp folder first
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, name)
        uploaded.save(path)
        file_id = upload_supporting_document(path)
        shutil.rmtree(folder, ignore_errors=True)
 
        if file_id is None:
            flash(f"Could not upload {name}.")
            continue
        record.setdefault("supporting_documents", []).append(
            {"file_name": name, "file_id": file_id})
        added += 1
 
    if added:
        if save_case_record(record):
            flash(f"{added} document(s) uploaded.")
        else:
            flash("Documents uploaded, but the record could not be saved.")
    elif chosen == 0:
        flash("Choose at least one file first.")
    return redirect(url_for("view_client", client_ref=client_ref))
 
 
@app.route("/client/<client_ref>/documents/<file_id>")
def download_document(client_ref, file_id):
    record = get_record(client_ref)
    if record is None:
        abort(404)
 
    document = None
    for item in record.get("supporting_documents", []):
        if item.get("file_id") == file_id:
            document = item
    if document is None:
        abort(404)
 
    name = secure_filename(document.get("file_name", "")) or "document"
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, name)
    ok = download_supporting_document(file_id, path)
    data = b""
    if ok:
        with open(path, "rb") as handle:
            data = handle.read()
    shutil.rmtree(folder, ignore_errors=True)
 
    if not ok:
        flash("Could not download that document.")
        return redirect(url_for("view_client", client_ref=client_ref))
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name)


@app.route("/client/<client_ref>/assess", methods=["POST"])
def assess_client(client_ref):
    record = get_record(client_ref)
    if record is None:
        abort(404)
    if _client is None:
        flash("No API key configured — assessment cannot run.")
        return redirect(url_for("view_client", client_ref=client_ref))

    job_id = uuid.uuid4().hex
    _assessment_jobs[job_id] = {
        "status":     "running",
        "step_num":   0,
        "step":       "Starting…",
        "result":     None,
        "error":      None,
        "client_ref": client_ref,
    }
    threading.Thread(
        target=_run_assessment_job, args=(job_id, client_ref, record), daemon=True
    ).start()
    return redirect(url_for("assess_loading", job_id=job_id))


@app.route("/assess/loading/<job_id>")
def assess_loading(job_id):
    if job_id not in _assessment_jobs:
        abort(404)
    client_ref = _assessment_jobs[job_id]["client_ref"]
    record = get_record(client_ref) or {"client_ref": client_ref, "name": client_ref}
    return render_template("assess_loading.html", job_id=job_id, record=record,
                           ASSESS_STEPS=ASSESS_STEPS)


@app.route("/assess/poll/<job_id>")
def assess_poll(job_id):
    if job_id not in _assessment_jobs:
        return jsonify({"status": "error", "error": "Job not found."})
    job = _assessment_jobs[job_id]
    return jsonify({
        "status":   job["status"],
        "step_num": job["step_num"],
        "step":     job["step"],
        "error":    job.get("error"),
    })


@app.route("/assess/result/<job_id>")
def assess_result(job_id):
    if job_id not in _assessment_jobs:
        abort(404)
    job = _assessment_jobs[job_id]
    if job["status"] == "running":
        return redirect(url_for("assess_loading", job_id=job_id))
    if job["status"] == "error":
        flash(f"Assessment failed: {job.get('error', 'Unknown error')}")
        return redirect(url_for("view_client", client_ref=job["client_ref"]))
    return render_template("assess_result.html", **job["result"])


@app.route("/client/<client_ref>/review", methods=["GET", "POST"])
def review_client(client_ref):
    record = get_record(client_ref)
    if record is None:
        abort(404)
    if _client is None:
        flash("No API key configured — review cannot run.")
        return redirect(url_for("view_client", client_ref=client_ref))

    saved = _load_assessment(client_ref)
    if saved is None or saved.get("assessment") is None:
        flash("Run a full assessment first before adding a periodic review.")
        return redirect(url_for("view_client", client_ref=client_ref))

    if request.method == "POST":
        # Raw forecast data is not yet persisted between sessions (stub).
        flash("Periodic reviews require forecast data to be persisted between sessions — coming soon.")
        return redirect(url_for("view_client", client_ref=client_ref))

    return render_template("review_form.html", record=record, today=today())


@app.route("/assessments")
def list_assessments_view():
    return render_template("assessments.html", assessments=load_all_assessments())


@app.route("/sync", methods=["POST"])
def sync_to_cloud():
    stats = sync_json_to_mongodb()
    if stats["errors"]:
        for err in stats["errors"]:
            flash(f"Sync error: {err}")
    else:
        flash(
            f"Sync complete — {stats['cases_synced']} case(s) and "
            f"{stats['assessments_synced']} assessment(s) pushed to cloud."
        )
    return redirect(url_for("home"))



@app.errorhandler(404)
def handle_404(e):
    return render_template("error.html", error_detail=None), 404


@app.errorhandler(Exception)
def handle_exception(e):
    import logging
    import traceback
    logging.error("Unhandled exception: %s", traceback.format_exc())
    detail = str(e) if app.debug else None
    return render_template("error.html", error_detail=detail), 500


# ============================================================================
# HTML templates (kept in this file so there's only one file to manage)
# ============================================================================
 
BASE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Source of Wealth Screening{% endblock %}</title>
<style>
  :root { --ink:#1f2933; --muted:#616e7c; --line:#d9dee3; --bg:#f0f2f5; --accent:#1d4f7a; --bad:#a61b1b; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); line-height:1.5; }
  header { background:var(--accent); padding:0 28px; display:flex; align-items:center; justify-content:space-between; height:54px; }
  .brand { color:#fff; font-weight:700; font-size:1rem; text-decoration:none; letter-spacing:.02em; }
  nav { display:flex; gap:8px; align-items:center; }
  .nav-link { color:rgba(255,255,255,.85); font-size:.875rem; text-decoration:none; padding:5px 11px; border-radius:4px; border:1px solid rgba(255,255,255,.3); transition:background .15s; }
  .nav-link:hover { background:rgba(255,255,255,.12); }
  .nav-cta { background:#fff; color:var(--accent); font-size:.875rem; font-weight:600; text-decoration:none; padding:5px 13px; border-radius:4px; border:none; cursor:pointer; font:inherit; }
  .nav-cta:hover { background:#e8edf2; }
  main { max-width:960px; margin:0 auto; padding:28px 24px; }
  h1 { font-size:1.45rem; margin:0 0 18px; }
  h2 { font-size:1.05rem; margin:0 0 12px; color:var(--ink); }
  a { color:var(--accent); }
  .panel { background:#fff; border:1px solid var(--line); border-radius:8px; padding:18px 22px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,.04); }
  .msg { background:#e8f1f8; border:1px solid #b9d3e6; border-radius:6px; padding:10px 14px; margin-bottom:14px; }
  .errors { background:#fdeaea; border:1px solid #e3b0b0; color:var(--bad); border-radius:6px; padding:10px 14px 10px 30px; margin-bottom:14px; }
  .actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  button, .button { font:inherit; background:var(--accent); color:#fff; border:0; border-radius:5px; padding:7px 16px; cursor:pointer; text-decoration:none; display:inline-block; font-size:.9rem; }
  button.secondary, .button.secondary { background:#fff; color:var(--accent); border:1px solid var(--accent); }
  button.danger { background:#fff; color:var(--bad); border:1px solid var(--bad); padding:4px 10px; }
  button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, a:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  label { display:block; font-size:.88rem; color:var(--muted); margin-bottom:10px; }
  input, select, textarea { display:block; width:100%; font:inherit; color:var(--ink); padding:7px 10px; margin-top:3px; border:1px solid #c4cdd6; border-radius:5px; background:#fff; }
  fieldset { border:1px solid var(--line); border-radius:8px; background:#fff; padding:16px 20px; margin:0 0 16px; }
  legend { font-weight:600; padding:0 6px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:0 16px; }
  .wide { grid-column:1 / -1; }
  .entry { border:1px solid var(--line); border-radius:6px; padding:12px 14px; margin-bottom:12px; background:#fafbfc; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:middle; font-size:.9rem; }
  th { color:var(--muted); font-weight:500; font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; width:auto; }
  .muted { color:var(--muted); }
  .savebar { position:sticky; bottom:0; background:var(--bg); padding:12px 0; border-top:1px solid var(--line); }
  .badge { display:inline-block; font-size:.75rem; font-weight:700; padding:2px 8px; border-radius:12px; letter-spacing:.03em; }
  .badge-pass { background:#d1fae5; color:#065f46; }
  .badge-warn { background:#fef3c7; color:#92400e; }
  .badge-risk { background:#ffedd5; color:#9a3412; }
  .badge-bad  { background:#fee2e2; color:#991b1b; }
</style>
</head>
<body>
<header>
  <a class="brand" href="{{ url_for('home') }}">Source of Wealth Screening</a>
  <nav>
    <a class="nav-link" href="{{ url_for('list_assessments_view') }}">Assessments</a>
    <form method="post" action="{{ url_for('sync_to_cloud') }}" style="margin:0">
      <button class="nav-link" type="submit" style="cursor:pointer">Sync to Cloud</button>
    </form>
    <a class="nav-cta" href="{{ url_for('new_client') }}">+ New client</a>
  </nav>
</header>
<main>
  {% for message in get_flashed_messages() %}<div class="msg">{{ message }}</div>{% endfor %}
  {% block content %}{% endblock %}
</main>
</body>
</html>
"""
 
HOME = """{% extends "base.html" %}
{% block content %}
<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:18px; gap:16px">
  <h1 style="margin:0">Clients</h1>
  <form action="{{ url_for('find') }}" method="get" style="display:flex; gap:8px; align-items:center">
    <input name="ref" placeholder="Search by client reference..." aria-label="Client reference"
           style="width:280px; margin:0; padding:7px 10px">
    <button class="secondary" type="submit" style="white-space:nowrap">Find</button>
  </form>
</div>

<div class="panel" style="padding:0; overflow:hidden">
  {% if not clients %}
    <p class="muted" style="padding:20px 22px; margin:0">No clients yet — click <strong>+ New client</strong> in the top right to add the first one.</p>
  {% else %}
  <table>
    <thead>
      <tr style="background:#fafbfc">
        <th>Reference</th>
        <th>Name</th>
        <th>Occupation</th>
        <th>Industry</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
    {% for c in clients %}
    <tr style="transition:background .1s" onmouseover="this.style.background='#f5f7fa'" onmouseout="this.style.background=''">
      <td><code style="font-size:.82rem; color:var(--muted)">{{ c.get('client_ref', '') }}</code></td>
      <td><strong>{{ c.get('name', '') }}</strong></td>
      <td>{{ (c.get('career') or {}).get('occupation', '') }}</td>
      <td>{{ (c.get('career') or {}).get('industry', '') }}</td>
      <td style="text-align:right; padding-right:14px">
        <a class="button" href="{{ url_for('view_client', client_ref=c.get('client_ref', '')) }}"
           style="padding:4px 12px; font-size:.82rem">Open →</a>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  <div style="padding:10px 22px; color:var(--muted); font-size:.82rem; border-top:1px solid var(--line)">
    {{ clients|length }} client{{ 's' if clients|length != 1 else '' }}
  </div>
  {% endif %}
</div>
{% endblock %}
"""
 
FORM = """{% extends "base.html" %}
{% macro box(prefix, field, value) %}
  <label {% if field[2] == 'textarea' %}class="wide"{% endif %}>{{ field[1] }}
    {% if field[2] == 'textarea' %}
      <textarea name="{{ prefix }}.{{ field[0] }}" rows="6">{{ value }}</textarea>
    {% elif field[2] == 'pep' %}
      <select name="{{ prefix }}.{{ field[0] }}">
        <option value="">Choose</option>
        {% for option in PEP_OPTIONS %}<option {{ 'selected' if value == option else '' }}>{{ option }}</option>{% endfor %}
        {% if value and value not in PEP_OPTIONS %}<option selected>{{ value }}</option>{% endif %}
      </select>
    {% elif field[2] == 'payment' %}
      <select name="{{ prefix }}.{{ field[0] }}">
        <option value="">Choose</option>
        {% for option in PAYMENT_OPTIONS %}<option {{ 'selected' if value == option else '' }}>{{ option }}</option>{% endfor %}
      </select>
    {% else %}
      <input name="{{ prefix }}.{{ field[0] }}" type="{{ 'date' if field[2] == 'date' else 'text' }}" value="{{ value }}">
    {% endif %}
  </label>
{% endmacro %}
 
{% macro entry(prefix, fields, item) %}
  <div class="entry">
    <div class="grid">
      {% for field in fields %}{{ box(prefix, field, item.get(field[0], '')) }}{% endfor %}
    </div>
    <button type="button" class="danger" onclick="this.parentElement.remove()">Remove entry</button>
  </div>
{% endmacro %}
 
{% block content %}
<h1>{{ heading }}</h1>
 
{% if problems %}
<div class="errors" style="padding:10px 14px; border-radius:6px">
  <strong>{{ problems|length }} issue{{ 's' if problems|length != 1 else '' }} found</strong>
  — fix the fields highlighted below.
  {% set _general = [] %}
  {% for p in problems %}{% if p not in field_errors.values() %}{% if _general.append(p) %}{% endif %}{% endif %}{% endfor %}
  {% if _general %}<ul style="margin:6px 0 0; padding-left:18px">{% for p in _general %}<li>{{ p }}</li>{% endfor %}</ul>{% endif %}
</div>
{% endif %}
 
<form method="post">
  {% for prefix, title, fields, kind in SECTIONS %}
  <fieldset>
    <legend>{{ title }}</legend>
    {% if kind == 'dict' %}
      {% set data = values[prefix] %}
      <div class="grid">
        {% for field in fields %}{{ box(prefix, field, data.get(field[0], '')) }}{% endfor %}
      </div>
    {% else %}
      <div id="list-{{ prefix }}">
        {% for item in (values[prefix] or [{}]) %}{{ entry(prefix, fields, item) }}{% endfor %}
      </div>
      <button type="button" class="secondary" onclick="addEntry('{{ prefix }}')">Add entry</button>
      <template id="tpl-{{ prefix }}">{{ entry(prefix, fields, {}) }}</template>
    {% endif %}
  </fieldset>
  {% endfor %}
 
  <p class="muted">Supporting documents can be uploaded on the client page after saving.</p>
  <div class="savebar actions">
    <button type="submit">Save client</button>
    <a class="button secondary" href="{{ url_for('home') }}">Cancel</a>
  </div>
</form>
 
<script>
(function () {
  var fe = {{ field_errors | tojson }};
  Object.keys(fe).forEach(function (key) {
    var msg = fe[key];
    var parts = key.split(".");
    var input;
    if (parts.length === 3 && /^\\d+$/.test(parts[1])) {
      var name = parts[0] + "." + parts[2];
      var idx  = parseInt(parts[1], 10);
      var all  = document.querySelectorAll('[name="' + name + '"]');
      input = all[idx] || null;
    } else {
      input = document.querySelector('[name="' + key + '"]');
    }
    if (!input) return;
    input.style.borderColor = "#e3b0b0";
    input.style.background  = "#fff5f5";
    var lbl = input.closest("label");
    if (!lbl) return;
    var span = document.createElement("span");
    span.style.cssText = "color:#a61b1b;font-size:.79rem;display:block;margin-top:3px";
    span.textContent = msg;
    lbl.appendChild(span);
  });
})();
</script>
<script>
  function addEntry(prefix) {
    var template = document.getElementById("tpl-" + prefix);
    document.getElementById("list-" + prefix).appendChild(template.content.cloneNode(true));
  }
</script>
{% endblock %}
"""
 
VIEW = """{% extends "base.html" %}
{% block content %}
<h1>{{ record.get('name', 'Client') }}
  <span class="muted" style="font-size:1rem">{{ record.get('client_ref', '') }}</span></h1>
 
<div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid var(--line)">
  <a class="button secondary" href="{{ url_for('edit_client', client_ref=record.get('client_ref', '')) }}">Edit</a>
  <form method="post" action="{{ url_for('assess_client', client_ref=record.get('client_ref', '')) }}" style="margin:0">
    <button type="submit">Run Assessment</button>
  </form>
  <a class="button secondary" href="{{ url_for('review_client', client_ref=record.get('client_ref', '')) }}">Periodic Review</a>
  <span style="flex:1"></span>
  <a class="button secondary" href="{{ url_for('home') }}" style="color:var(--muted); border-color:var(--line)">← Back</a>
</div>

{% if assessments %}
<div class="panel" style="margin-bottom:20px">
  <h2>Assessment History</h2>
  <table>
    {% for a in assessments %}
    <tr>
      <td>{{ a.get('assessed_at', '')[:10] }}</td>
      <td>{{ a.get('assessed_at', '')[11:16] }} UTC</td>
      <td>
        {% if a.get('assessment_id') %}
        <a href="{{ url_for('view_assessment', assessment_id=a.get('assessment_id')) }}">View</a>
        {% else %}
        <span class="muted">—</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}
 
{% for prefix, title, fields, kind in SECTIONS %}
<div class="panel">
  <h2>{{ title }}</h2>
  {% if kind == 'dict' %}
    {% set data = values[prefix] %}
    <table>
      {% for field in fields %}
      <tr><th>{{ field[1] }}</th><td>{{ data.get(field[0], '') }}</td></tr>
      {% endfor %}
    </table>
  {% else %}
    {% for item in values[prefix] %}
      <table style="margin-bottom:12px">
        {% for field in fields %}
        <tr><th>{{ field[1] }}</th><td>{{ item.get(field[0], '') }}</td></tr>
        {% endfor %}
      </table>
    {% else %}
      <p class="muted">Nothing entered.</p>
    {% endfor %}
  {% endif %}
</div>
{% endfor %}
 
<div class="panel">
  <h2>Supporting documents</h2>
  {% for document in record.get('supporting_documents', []) %}
    <p style="margin:4px 0">
      {{ document.get('file_name', 'Unknown file') }}
      {% if document.get('file_id') %}
        - <a href="{{ url_for('download_document', client_ref=record.get('client_ref', ''), file_id=document.get('file_id')) }}">Download</a>
      {% endif %}
    </p>
  {% else %}
    <p class="muted">No documents uploaded yet.</p>
  {% endfor %}
 
  <form method="post" action="{{ url_for('upload_documents', client_ref=record.get('client_ref', '')) }}"
        enctype="multipart/form-data" style="margin-top:14px">
    <label>Add documents
      <input type="file" name="files" multiple>
    </label>
    <button type="submit">Upload</button>
  </form>
</div>
{% endblock %}
"""
 
ASSESS_RESULT = """{% extends "base.html" %}
{% set COLORS = {"PASSED": "#1a7a3f", "MANUAL_REVIEW": "#b45309", "RISKY": "#c2410c", "SERIOUS_RISK": "#b91c1c"} %}
{% block content %}
<h1>Assessment — {{ record.get('name', '') }}
  <span class="muted" style="font-size:1rem">{{ record.get('client_ref', '') }}</span>
</h1>
{% if assessed_at %}
<p class="muted" style="margin:0 0 12px 0">Assessed on {{ assessed_at[:10] }}</p>
{% endif %}
<div class="actions" style="margin-bottom:16px; display:flex; gap:8px; flex-wrap:wrap">
  <a class="button secondary" href="{{ url_for('view_client', client_ref=record.get('client_ref', '')) }}">Back to client</a>
</div>

{% if crime_result %}
<div class="panel">
  <h2>Crime Scan &nbsp;
    <span style="font-size:1rem; font-weight:700; color:{{ '#b91c1c' if crime_result.get('breach_count', 0) > 0 else '#1a7a3f' }}">
      {{ crime_result.get('breach_count', 0) }} typolog{{ 'y' if crime_result.get('breach_count', 0) == 1 else 'ies' }} matched
    </span>
  </h2>
  {% for mr in crime_result.get('match_results', []) %}
  <div style="padding:8px 0; border-bottom:1px solid var(--line)">
    <strong>{{ mr.name }}</strong>
    <span style="margin-left:10px; font-weight:700; color:{{ '#b91c1c' if mr.breached else '#1a7a3f' }}">
      {{ 'BREACH' if mr.breached else 'PASS' }}
    </span>
    {% if mr.core_fired %}
    <div class="muted" style="font-size:.87rem; margin-top:2px">Fired: {{ mr.core_fired | join(', ') }}</div>
    {% endif %}
  </div>
  {% else %}
  <p class="muted">No typologies returned.</p>
  {% endfor %}
</div>
{% else %}
<div class="panel"><p class="muted">Crime scan could not be completed (check API key).</p></div>
{% endif %}

{% if benchmark %}
<div class="panel">
  <h2>Benchmark</h2>
  <table>
    {% for f in benchmark.get('findings', []) %}
    <tr>
      <th>{{ f.get('rule_id', '') }}</th>
      <td style="color:{{ '#b91c1c' if f.get('failed') else '#1a7a3f' }}; font-weight:600">
        {{ 'FAILED' if f.get('failed') else 'OK' }}
      </td>
      <td>{{ f.get('detail', '') }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
{% else %}
<div class="panel"><p class="muted">Benchmark could not be completed.</p></div>
{% endif %}

{% if forecast %}
<div class="panel">
  <h2>Forecasting Onboarding &nbsp;
    <span style="font-size:1rem; font-weight:700; color:{{ COLORS.get(forecast.get('outcome',''), '#333') }}">
      {{ (forecast.get('outcome') or '').replace('_', ' ') }}
    </span>
  </h2>
  <p>{{ forecast.get('routing', '') }}</p>
  <table>
    {% for f in forecast.get('findings', []) %}
    <tr>
      <th>{{ f.get('rule_id', '') }}</th>
      <td style="color:{{ '#b91c1c' if f.get('failed') else '#1a7a3f' }}; font-weight:600">
        {{ 'FAILED' if f.get('failed') else 'OK' }}
      </td>
      <td>{{ f.get('detail', '') }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
{% else %}
<div class="panel"><p class="muted">Forecasting could not be completed.</p></div>
{% endif %}
{% endblock %}
"""

REVIEW_FORM = """{% extends "base.html" %}
{% block content %}
<h1>Periodic Review — {{ record.get('name', '') }}
  <span class="muted" style="font-size:1rem">{{ record.get('client_ref', '') }}</span>
</h1>
<div class="panel">
  <form method="post">
    <div class="grid">
      <label>Review date
        <input type="date" name="current_date" value="{{ today }}">
      </label>
    </div>
    <label class="wide" style="margin-top:12px">Review notes
      <textarea name="notes" rows="6" placeholder="Describe any changes to the client's circumstances..."></textarea>
    </label>
    <div class="actions" style="margin-top:16px">
      <button type="submit">Submit Review</button>
      <a class="button secondary" href="{{ url_for('view_client', client_ref=record.get('client_ref', '')) }}">Cancel</a>
    </div>
  </form>
</div>
{% endblock %}
"""

ASSESSMENTS = """{% extends "base.html" %}
{% block content %}
<h1>Saved Assessments</h1>
<div class="actions" style="margin-bottom:16px">
  <a class="button secondary" href="{{ url_for('home') }}">Back to clients</a>
</div>
<div class="panel">
  {% if not assessments %}
    <p class="muted">No assessments saved yet. Open a client and click Run Assessment.</p>
  {% else %}
  <table>
    <tr>
      <th style="width:auto">Client</th>
      <th style="width:auto">Date</th>
      <th style="width:auto">Kind</th>
      <th style="width:auto">Outcome</th>
      <th style="width:auto"></th>
    </tr>
    {% for a in assessments %}
    {% set outcome = (a.get('assessment') or {}).get('outcome', '') %}
    <tr>
      <td>{{ a.get('client_ref', '') }}</td>
      <td>{{ (a.get('assessed_at') or a.get('assessment', {}).get('assessed_on', ''))[:10] }}</td>
      <td>{{ a.get('kind', 'crime scan + benchmark') }}</td>
      <td>{{ outcome.replace('_', ' ') if outcome else '—' }}</td>
      <td style="display:flex; gap:10px">
        <a href="{{ url_for('view_client', client_ref=a.get('client_ref', '')) }}">Open client</a>
        {% if a.get('assessment_id') %}
        <a href="{{ url_for('view_assessment', assessment_id=a.get('assessment_id')) }}">View result</a>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
</div>
{% endblock %}
"""

ASSESS_LOADING = """{% extends "base.html" %}
{% block title %}Assessing {{ record.get('name', '') }} — Source of Wealth Screening{% endblock %}
{% block content %}
<h1>Assessing {{ record.get('name', '') }}
  <span class="muted" style="font-size:1rem">{{ record.get('client_ref', '') }}</span>
</h1>
<div class="panel">
  <p style="color:var(--muted); margin:0 0 18px">The assessment is running in the background. This usually takes 20–60 seconds.</p>
  <ol id="step-list" style="list-style:none; padding:0; margin:0">
    {% for s in ASSESS_STEPS %}
    <li id="step-{{ loop.index }}" style="display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid var(--line)">
      <span class="step-icon" style="width:22px; text-align:center; flex-shrink:0">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--line)"></span>
      </span>
      <span class="step-label" style="font-size:.9rem; color:var(--muted)">{{ s }}</span>
    </li>
    {% endfor %}
  </ol>
  <div id="error-box" style="display:none; margin-top:18px; padding:12px 14px; background:#fdeaea; border:1px solid #e3b0b0; border-radius:6px; color:#a61b1b; font-size:.9rem"></div>
  <div style="margin-top:20px">
    <button class="button secondary" onclick="cancel()">Cancel</button>
  </div>
</div>

<style>
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid #d0d7de; border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; vertical-align:middle; }
</style>

<script>
  var pollUrl   = {{ url_for('assess_poll',   job_id=job_id) | tojson }};
  var resultUrl = {{ url_for('assess_result', job_id=job_id) | tojson }};
  var cancelUrl = {{ url_for('view_client',   client_ref=record.get('client_ref', '')) | tojson }};
  var total = {{ ASSESS_STEPS | length }};
  var timer;

  function cancel() {
    clearInterval(timer);
    window.location.href = cancelUrl;
  }

  function updateUI(data) {
    var n = data.step_num || 0;
    for (var i = 1; i <= total; i++) {
      var li    = document.getElementById("step-" + i);
      var icon  = li.querySelector(".step-icon");
      var label = li.querySelector(".step-label");
      if (data.status === "done" || i < n) {
        icon.innerHTML         = '<span style="color:#1a7a3f;font-size:1rem;line-height:1">✓</span>';
        label.style.color      = "var(--ink)";
        label.style.fontWeight = "";
      } else if (i === n) {
        icon.innerHTML         = '<span class="spinner"></span>';
        label.style.color      = "var(--ink)";
        label.style.fontWeight = "600";
      } else {
        icon.innerHTML         = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--line)"></span>';
        label.style.color      = "var(--muted)";
        label.style.fontWeight = "";
      }
    }
    if (data.status === "done") {
      clearInterval(timer);
      window.location.href = resultUrl;
    } else if (data.status === "error") {
      clearInterval(timer);
      var box = document.getElementById("error-box");
      box.style.display = "block";
      box.textContent = "Assessment failed: " + (data.error || "Unknown error");
    }
  }

  function poll() {
    fetch(pollUrl)
      .then(function(r) { return r.json(); })
      .then(updateUI)
      .catch(function() {});
  }

  timer = setInterval(poll, 2000);
  poll();
</script>
{% endblock %}
"""


ERROR = """{% extends "base.html" %}
{% block title %}Error — Source of Wealth Screening{% endblock %}
{% block content %}
<div class="panel" style="text-align:center; padding:40px 22px">
  <div style="font-size:2.5rem; margin-bottom:12px; color:var(--bad)">&#9888;</div>
  <h1 style="color:var(--bad); margin-bottom:10px">Something went wrong</h1>
  <p style="color:var(--muted); max-width:440px; margin:0 auto 24px">
    An unexpected error occurred. Please go back and try again.
    If the problem persists, contact your system administrator.
  </p>
  {% if error_detail %}
  <p style="font-size:.8rem; color:var(--muted); font-family:monospace; max-width:600px; margin:0 auto 24px; word-break:break-all">{{ error_detail }}</p>
  {% endif %}
  <a class="button secondary" href="{{ url_for('home') }}">← Back to home</a>
</div>
{% endblock %}
"""
app.jinja_env.loader = DictLoader({
    "base.html":          BASE,
    "home.html":          HOME,
    "client_form.html":   FORM,
    "client_view.html":   VIEW,
    "assess_loading.html": ASSESS_LOADING,
    "assess_result.html": ASSESS_RESULT,
    "review_form.html":   REVIEW_FORM,
    "assessments.html":   ASSESSMENTS,
    "error.html":         ERROR,
})
# available inside every template and macro
app.jinja_env.globals.update(PEP_OPTIONS=PEP_OPTIONS, PAYMENT_OPTIONS=PAYMENT_OPTIONS)
 
 
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)

