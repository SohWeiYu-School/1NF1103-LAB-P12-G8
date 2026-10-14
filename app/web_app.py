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
    sow_declaration (text)
 
The form shows text; build_record() turns it back into numbers / lists / fractions,
and validate_case() (from io_manager) checks the result before anything is saved.
"""
 
import datetime
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
import uuid

from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)
from jinja2 import DictLoader
from werkzeug.utils import secure_filename

from app import ai_manager, logic_manager, utilities
from app.data_manager import (
    save_case_record,
    load_all_records,
    upload_supporting_document,
    download_supporting_document,
    save_assessment,
    load_all_assessments,
    sync_json_to_mongodb,
    load_raw_replies,
    save_raw_replies,
    save_forecast,
    load_client_assessments,
    load_assessment_by_id,
    read_document_for_extraction,
)
from app.io_manager import generate_client_id, validate_case, _number

# The assessment itself lives in main.py, so the web app runs EXACTLY the same flow as the
# command line (same steps, same verdict, same report, same saved record).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import main as assessment_flow  # noqa: E402

_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_handler = logging.FileHandler(os.path.join(_log_dir, "app.log"), encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.handlers.clear()
_root_logger.addHandler(_log_handler)
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # suppress per-request access logs, keep startup URL
utilities.quiet_library_logs()      # same as the command line: no network-library noise in the log

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")

app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024   # uploads for auto-fill: 40 MB per request

ASSESS_STEPS = assessment_flow.ASSESSMENT_STEPS    # the same steps the command line prints

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
    return assessment_flow.today()
 
 
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
    if isinstance(value, list):                    # countries, counterparties
        return ", ".join(str(item) for item in value)
    if kind in ("int", "money") or (isinstance(value, (int, float)) and not isinstance(value, bool)):
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
 
    # The assessment date is always today - not typed in (the command line does the same).
    record["current_date"] = today()
 
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
# Auto-fill from uploaded documents
# ============================================================================

def extracted_to_values(extracted: dict, current_date: str = "") -> dict:
    """The AI's document details as form text, in the same shape values_from_form gives.

    The AI reply is turned into a client record by logic_manager.record_from_documents - the very
    function the command line's "New Client from Documents" uses - so both give the same values
    (field names, age from the date of birth, wealth split as fractions, oldest job first)."""
    record, _ = logic_manager.record_from_documents(extracted, "", current_date or today())
    if not extracted.get("jobs"):
        record["career_history"] = []      # the command line's empty "current job" is only there to be asked for
    values = values_from_record(record)
    for prefix, title, fields, kind in SECTIONS:
        if kind == "list":
            values[prefix] = [row for row in values[prefix] if any(row.values())]
    return values


def _same_entry(prefix: str, a: dict, b: dict) -> bool:
    """Is this extracted job / asset already on the form?"""
    def norm(text):
        return " ".join(str(text or "").lower().split())
    if prefix == "jobs":
        return (norm(a.get("employer_name")) == norm(b.get("employer_name"))
                and norm(a.get("start_year")) == norm(b.get("start_year")))
    return (norm(a.get("asset_type")) == norm(b.get("asset_type"))
            and norm(a.get("year_acquired")) == norm(b.get("year_acquired")))


def fill_blanks(values: dict, found: dict) -> tuple[dict, list[str]]:
    """Put the document details into the form without overwriting anything typed.

    Single boxes are filled only when empty. Jobs and assets found in the documents
    are added unless the same one is already there; empty entries are filled first.
    The wealth split is filled only if all four boxes are empty, so it still adds up.
    Returns (new values, input names that were filled) so the form can highlight them.
    """
    merged = {prefix: (dict(section) if isinstance(section, dict) else [dict(r) for r in section])
              for prefix, section in values.items()}
    filled = []
    for prefix, title, fields, kind in SECTIONS:
        new = found.get(prefix)
        if not new:
            continue
        if kind == "dict":
            current = merged.setdefault(prefix, {})
            if prefix == "composition" and any(current.get(f[0]) for f in fields):
                continue
            for key, label, k in fields:
                if new.get(key) and not current.get(key):
                    current[key] = new[key]
                    filled.append(f"{prefix}.{key}")
            continue

        rows = [row for row in merged.get(prefix, []) if any(row.values())]
        for row in new:
            if any(_same_entry(prefix, row, existing) for existing in rows):
                continue
            rows.append(row)
            index = len(rows) - 1
            filled.extend(f"{prefix}.{index}.{key}" for key, value in row.items() if value)
        if prefix == "jobs":   # keep the oldest job first, like the saved record
            order = sorted(range(len(rows)), key=lambda i: rows[i].get("start_year") or "9999")
            remap = {f"{prefix}.{old}.": f"{prefix}.{new}." for new, old in enumerate(order)}
            filled = [next((name.replace(o, n, 1) for o, n in remap.items() if name.startswith(o)), name)
                      for name in filled]
            rows = [rows[i] for i in order]
        merged[prefix] = rows
    return merged, filled


def _is_stored_document(file_id: str) -> bool:
    """Only accept document references this app made (a local copy or a GridFS id)."""
    file_id = str(file_id or "")
    if len(file_id) == 24 and all(c in "0123456789abcdef" for c in file_id.lower()):
        return True
    return file_id.startswith("data/documents/") and ".." not in file_id and "\\" not in file_id


def pending_documents(form) -> list[dict]:
    """Documents uploaded during auto-fill, carried in hidden boxes until the client is saved."""
    names, ids = form.getlist("doc.file_name"), form.getlist("doc.file_id")
    return [{"file_name": secure_filename(name) or "document", "file_id": file_id}
            for name, file_id in zip(names, ids) if _is_stored_document(file_id)]


def store_uploads(files) -> tuple[list[dict], list[dict]]:
    """Keep each uploaded file as a supporting document and read it for the AI.
    Returns (supporting document entries, documents read for extraction)."""
    stored, read = [], []
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        name = secure_filename(uploaded.filename) or "document"
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, name)
            uploaded.save(path)
            read.append(read_document_for_extraction(path, name))
            file_id = upload_supporting_document(path)
            if file_id is not None:
                stored.append({"file_name": name, "file_id": file_id})
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    return stored, read


def autofill_form(heading: str, form, files):
    """Read the uploaded documents and show the form again with the blanks filled in."""
    values = values_from_form(form)
    documents = pending_documents(form)
    stored, read = store_uploads(files)
    documents += stored
    if not read:
        return show_form(heading, values, ["Choose at least one document to read."], documents=documents)

    extracted, warnings = ai_manager.extract_client_details(read)
    if extracted is None:
        flash("Document reading is not available right now — please try again later.")
    filled, notes = [], list(warnings)
    if extracted is not None:
        found = extracted_to_values(extracted, today())
        values, filled = fill_blanks(values, found)
        notes += extracted.get("notes") or []
        # the same second-look checks the command line shows after reading documents
        date = today()
        record, _ = logic_manager.record_from_documents(extracted, "", date)
        notes += logic_manager.check_document_record(logic_manager.finish_document_record(record), date)
    if extracted is not None and filled:
        flash(f"Read {len(read)} document(s) and filled {len(filled)} box(es), highlighted in yellow. "
              "Check every highlighted value before saving.")
    elif extracted is not None:
        flash("The documents were read, but nothing new was found to fill in.")
    return show_form(heading, values, [], documents=documents, autofilled=filled, notes=notes)


# ============================================================================
# Helpers
# ============================================================================

def get_record(client_ref: str):
    """Find one client record by reference. Returns None if not found."""
    for record in load_all_records():
        if record.get("client_ref") == client_ref:
            return record
    return None


# ---------------------------------------------------------------------------
# Flash helpers — mirrors io_manager display functions for the web layer
# ---------------------------------------------------------------------------

def flash_ai_unavailable(feature: str = "Assessment") -> None:
    flash(f"{feature} is not available right now — please try again later.")

def flash_rate_limit_error(seconds_left: int) -> None:
    flash(f"Rate limit: please wait {seconds_left} second{'s' if seconds_left != 1 else ''} before running another assessment.")

def flash_unexpected_error() -> None:
    flash("An unexpected error occurred — please try again or contact support.")
 
 
def show_form(heading: str, values: dict, problems: list, field_errors: dict = None,
              documents: list | None = None, autofilled: list | None = None,
              notes: list | None = None):
    return render_template("client_form.html", heading=heading, values=values,
                           problems=problems, SECTIONS=SECTIONS,
                           field_errors=field_errors or {}, documents=documents or [],
                           autofilled=autofilled or [], notes=notes or [])


# ============================================================================
# Assessment helpers
# ============================================================================

def _run_assessment_job(job_id, record, case):
    """The combined assessment, in the background: main.run_combined_assessment, the very
    function the command line runs, then the same save."""
    job = _assessment_jobs[job_id]

    def step(number, label):
        job["step_num"] = number
        job["step"] = label

    try:
        result = assessment_flow.run_combined_assessment(case, record.get("supporting_documents"), on_step=step)
        assessment_id = assessment_flow.save_combined_assessment(result, record, on_step=step)

        # Per-feature failure flags — feature names passed to flash_ai_unavailable in assess_result
        ai_warnings = []
        if result.get("typologies") is None:
            ai_warnings.append("Crime scan")
        if result["benchmark"]["findings"] is None:
            ai_warnings.append("Benchmark")
        if result.get("forecast") is None:
            ai_warnings.append("Forecasting")

        job["result"] = {
            "record":        record,
            "report":        result["report"],
            "assessment_id": assessment_id,
            "assessed_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "ai_warnings":   ai_warnings,
            "crime_result":  result.get("crime_result"),
            "typologies":    result.get("typologies"),
            "benchmark":     result.get("benchmark"),
        }
        job["status"] = "done"
    except Exception as exc:
        logging.getLogger(__name__).error("_run_assessment_job %s failed: %s", job_id, repr(exc))
        logging.exception("Assessment of %s failed", record.get("client_ref"))
        job["status"] = "error"
        job["error"] = str(exc)


def show_report(record: dict, report: dict | None, assessed_at: str = "", assessment_id: str | None = None,
                crime_result=None, typologies=None, benchmark=None):
    """The assessment page: the same report the command line prints (logic_manager.verdict_report)."""
    symbols = {entry["mark"]: entry["symbol"] for entry in (report or {}).get("legend", [])}
    typologies_by_id = {t.get("typology_id"): t for t in (typologies or [])}
    return render_template("assess_result.html", record=record, report=report, symbols=symbols,
                           assessed_at=assessed_at, assessment_id=assessment_id,
                           crime_result=crime_result, typologies_by_id=typologies_by_id, benchmark=benchmark)


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
 
 
def _documents_on_save(form, files) -> list[dict]:
    """Documents to attach when the form is saved: ones kept from auto-fill, plus any
    files chosen in the upload box without pressing "Read documents"."""
    stored, _ = store_uploads(files)
    return pending_documents(form) + stored


@app.route("/client/new", methods=["GET", "POST"])
def new_client():
    if request.method == "POST":
        values = values_from_form(request.form)
        documents = _documents_on_save(request.form, request.files.getlist("autofill_files"))
        record, problems, field_errors = build_record(values, generate_client_id())
        if not problems:
            problems = check_record(record)
            field_errors = {}
        if not problems:
            record["supporting_documents"] = documents
            if save_case_record(record):
                flash("Client saved.")
                return redirect(url_for("view_client", client_ref=record["client_ref"]))
            problems = ["The client could not be saved. Check the database connection."]
        return show_form("New client", values, problems, field_errors, documents=documents)
 
    return show_form("New client", values_from_record({"current_date": today()}), [])


@app.route("/client/new/autofill", methods=["POST"])
def new_client_autofill():
    return autofill_form("New client", request.form, request.files.getlist("autofill_files"))
 
 
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
    record = get_record(saved["client_ref"]) or {"client_ref": saved["client_ref"], "name": saved["client_ref"]}
    typologies = (saved.get("crime_scan") or {}).get("typologies", {})
    typology_list = typologies.get("sector_typologies") if isinstance(typologies, dict) else None
    return show_report(record, saved.get("report"), saved.get("assessed_at", ""), assessment_id,
                       crime_result=saved.get("crime_result"), typologies=typology_list,
                       benchmark=saved.get("benchmark"))
 
 
@app.route("/client/<client_ref>/edit", methods=["GET", "POST"])
def edit_client(client_ref):
    old = get_record(client_ref)
    if old is None:
        abort(404)
 
    if request.method == "POST":
        values = values_from_form(request.form)
        documents = _documents_on_save(request.form, request.files.getlist("autofill_files"))
        record, problems, field_errors = build_record(values, client_ref, old)
        if not problems:
            problems = check_record(record)
            field_errors = {}
        if not problems:
            known = {d.get("file_id") for d in record.get("supporting_documents", [])}
            record["supporting_documents"] = list(record.get("supporting_documents", [])) + [
                d for d in documents if d["file_id"] not in known]
            if save_case_record(record):
                flash("Changes saved.")
                return redirect(url_for("view_client", client_ref=client_ref))
            problems = ["The changes could not be saved. Check the database connection."]
        return show_form(f"Edit {client_ref}", values, problems, field_errors, documents=documents)
 
    return show_form(f"Edit {client_ref}", values_from_record(old), [])


@app.route("/client/<client_ref>/autofill", methods=["POST"])
def edit_client_autofill(client_ref):
    if get_record(client_ref) is None:
        abort(404)
    return autofill_form(f"Edit {client_ref}", request.form, request.files.getlist("autofill_files"))
 
 
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
 
 
@app.route("/client/<client_ref>/documents/<path:file_id>")
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
    ok = download_supporting_document(document.get("file_path") or file_id, path)   # local copy
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
    if not ai_manager.has_api_key():
        logging.getLogger(__name__).warning("assess_client: no API key configured (client_ref=%s)", client_ref)
        flash_ai_unavailable("Assessment")
        return redirect(url_for("view_client", client_ref=client_ref))

    user_key = request.remote_addr or "unknown"
    allowed, seconds_left = utilities.check_rate_limit(user_key)
    if not allowed:
        flash_rate_limit_error(seconds_left)
        return redirect(url_for("view_client", client_ref=client_ref))

    utilities.record_assessment(user_key)
    # The same case the command line assesses, dated today (logic_manager.forecasting_case).
    # The command line asks for anything missing; here the officer completes the record first.
    case, missing = logic_manager.forecasting_case(record, today())
    problems = validate_case(case) if case is not None else []
    if case is None or problems:
        flash("Complete the client record before assessing (Edit): " + "; ".join(missing or problems))
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
        target=_run_assessment_job, args=(job_id, record, case), daemon=True
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
        flash_unexpected_error()
        return redirect(url_for("view_client", client_ref=job["client_ref"]))
    for feature in job["result"].get("ai_warnings", []):
        flash_ai_unavailable(feature)
    r = job["result"]
    return show_report(r["record"], r["report"], r.get("assessed_at", ""), r.get("assessment_id"),
                       crime_result=r.get("crime_result"), typologies=r.get("typologies"),
                       benchmark=r.get("benchmark"))


@app.route("/assessments")
def list_assessments_view():
    return render_template("assessments.html", assessments=load_all_assessments())


@app.route("/sync", methods=["POST"])
def sync_to_cloud():
    stats = sync_json_to_mongodb()
    cases = stats["cases_synced"]
    assessments = stats["assessments_synced"]
    skipped = stats["skipped"]
    errors = stats["errors"]
    synced = cases + assessments

    if not errors:
        msg = f"Sync complete — {cases} case(s) and {assessments} assessment(s) pushed to cloud."
        if skipped:
            msg += f" {skipped} already up to date."
        flash(msg)
    elif synced > 0:
        flash(
            f"Partial sync — {cases} case(s) and {assessments} assessment(s) pushed to cloud. "
            "Some records could not be synced — please try again later or contact your administrator."
        )
    else:
        flash("Sync unsuccessful — please try again later or contact your administrator.")
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
  .autofill { border-color:#b9d3e6; background:#f5f9fc; }
  .autofilled { background:#fff8db !important; border-color:#e0c25a !important; }
  .notes { background:#fff8db; border:1px solid #e9d68f; border-radius:6px; padding:10px 14px; margin-top:12px; font-size:.88rem; }
  .notes ul { margin:6px 0 0; padding-left:18px; }
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
 
{% if request.endpoint in ('new_client', 'new_client_autofill') %}
  {% set autofill_url = url_for('new_client_autofill') %}
  {% set save_url = url_for('new_client') %}
{% else %}
  {% set autofill_url = url_for('edit_client_autofill', client_ref=request.view_args.get('client_ref', '')) %}
  {% set save_url = url_for('edit_client', client_ref=request.view_args.get('client_ref', '')) %}
{% endif %}

<form method="post" action="{{ save_url }}" enctype="multipart/form-data" id="client-form">
  <fieldset class="autofill">
    <legend>Fill in from documents</legend>
    <p class="muted" style="margin:0 0 10px">
      Upload the client's documents (CV, payslips, employment letters, tax notices, property deeds,
      bank or brokerage statements, KYC forms). The AI reads them and fills in the empty boxes below.
      Nothing you've typed is overwritten, and every filled box is highlighted for you to check.
    </p>
    <div class="actions">
      <input type="file" name="autofill_files" id="autofill-files" multiple
             accept=".pdf,.docx,.xlsx,.xlsm,.txt,.csv,.md,.json,.png,.jpg,.jpeg,.webp,.gif"
             style="flex:1; min-width:240px; margin:0">
      <button type="submit" id="autofill-button" formaction="{{ autofill_url }}" formnovalidate>
        Read documents &amp; fill in
      </button>
    </div>
    <p id="autofill-status" class="muted" style="display:none; margin:10px 0 0">
      Reading documents... this can take up to a minute.
    </p>
    {% if documents %}
    <p style="margin:12px 0 4px; font-size:.88rem"><strong>Attached to this client when saved:</strong></p>
    <ul style="margin:0; padding-left:18px; font-size:.88rem">
      {% for d in documents %}
      <li>{{ d.file_name }}
        <input type="hidden" name="doc.file_name" value="{{ d.file_name }}">
        <input type="hidden" name="doc.file_id" value="{{ d.file_id }}"></li>
      {% endfor %}
    </ul>
    {% endif %}
    {% if notes %}
    <div class="notes">
      <strong>Notes from reading the documents</strong>
      <ul>{% for n in notes %}<li>{{ n }}</li>{% endfor %}</ul>
    </div>
    {% endif %}
  </fieldset>

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
 
  <p class="muted">Files chosen above are kept as supporting documents when you save. More can be added on the client page later.</p>
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
(function () {
  // Highlight the boxes the documents filled in; the highlight clears once the officer edits the box.
  var filled = {{ autofilled | tojson }};
  filled.forEach(function (key) {
    var parts = key.split("."), input;
    if (parts.length === 3 && /^\\d+$/.test(parts[1])) {
      input = document.querySelectorAll('[name="' + parts[0] + "." + parts[2] + '"]')[parseInt(parts[1], 10)];
    } else {
      input = document.querySelector('[name="' + key + '"]');
    }
    if (!input) return;
    input.classList.add("autofilled");
    input.title = "Filled in from the uploaded documents - please check";
    input.addEventListener("input", function () { input.classList.remove("autofilled"); });
    input.addEventListener("change", function () { input.classList.remove("autofilled"); });
  });

  var button = document.getElementById("autofill-button");
  var files = document.getElementById("autofill-files");
  document.getElementById("client-form").addEventListener("submit", function (event) {
    if (event.submitter !== button) return;
    if (!files.files.length) { event.preventDefault(); alert("Choose at least one document first."); return; }
    button.disabled = true;
    button.textContent = "Reading...";
    document.getElementById("autofill-status").style.display = "block";
    // a disabled submitter isn't sent, so submit through the form action ourselves
    event.preventDefault();
    var form = event.target;
    form.action = button.getAttribute("formaction");
    form.submit();
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
      <td>{{ (a.get('outcome') or (a.get('assessment') or {}).get('outcome') or '—').replace('_', ' ') }}</td>
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
{% set MARK_COLORS = {"fail": "#b91c1c", "explained": "#1d4f7a", "weak": "#b45309", "pass": "#1a7a3f", "skip": "#616e7c", "info": "#616e7c"} %}
{% block title %}Assessment — {{ record.get('name', '') }}{% endblock %}
{% block content %}
<style>
  .report-items { list-style:none; margin:0; padding:0; }
  .report-items li { display:flex; gap:10px; padding:8px 0; border-bottom:1px solid var(--line); font-size:.92rem; }
  .report-items li:last-child { border-bottom:0; }
  .mark { width:18px; flex-shrink:0; text-align:center; font-weight:700; }
  .sub { color:var(--muted); font-size:.85rem; margin-top:3px; }
  .legend { display:flex; flex-wrap:wrap; gap:6px 16px; font-size:.8rem; color:var(--muted); margin-top:12px; }
  details { border-top:1px solid var(--line); padding:8px 0; }
  details:first-of-type { border-top:0; }
  summary { cursor:pointer; font-weight:600; font-size:.92rem; }
  .evidence { margin:8px 0 0; padding-left:18px; font-size:.86rem; }
  .evidence li { white-space:pre-wrap; padding:2px 0; }
</style>
<h1>Assessment — {{ record.get('name', '') }}
  <span class="muted" style="font-size:1rem">{{ record.get('client_ref', '') }}</span>
</h1>
<div class="actions" style="margin-bottom:16px">
  <a class="button secondary" href="{{ url_for('view_client', client_ref=record.get('client_ref', '')) }}">Back to client</a>
</div>

{% if report %}
{% set h = report['header'] %}
{% set colour = COLORS.get(h['verdict'], '#333') %}
<div class="panel" style="border-left:6px solid {{ colour }}">
  <div class="muted" style="font-size:.78rem; text-transform:uppercase; letter-spacing:.06em">Verdict</div>
  <div style="font-size:1.7rem; font-weight:700; color:{{ colour }}; margin:2px 0 10px">{{ h['verdict_label'] }}</div>
  <table>
    <tr><th>Next step</th><td>{{ h['next_step'] }}</td></tr>
    <tr><th>Next review</th><td>{{ h['next_review_date'] }}</td></tr>
    <tr><th>Assessed on</th><td>{{ h['assessed_on'] }} (policy {{ h['policy_version'] }})</td></tr>
  </table>
  <p style="margin:12px 0 0">{{ h['summary'] }}</p>
  <div class="legend">
    {% for entry in report['legend'] %}
    <span><strong style="color:{{ MARK_COLORS[entry['mark']] }}">{{ entry['symbol'] }}</strong> {{ entry['meaning'] }}</span>
    {% endfor %}
  </div>
</div>

{% set INDICATOR_LABELS = {
  "counterparty_unnamed": "Money from unnamed counterparty",
  "gift_or_inheritance_unnamed": "Gift or inheritance from unnamed person",
  "source_contradicts_occupation": "Source of wealth contradicts occupation",
  "unvalued_source_present": "Source with no declared amount",
  "related_party_transaction": "Related party transaction"
} %}
{% set CAT_BG    = {"critical":"#fef2f2","high":"#fff7ed","medium":"#fffbeb","partial":"#eff6ff","weak_signal":"#f9fafb","no_match":"#f9fafb"} %}
{% set CAT_BORDER= {"critical":"#b91c1c","high":"#c2410c","medium":"#b45309","partial":"#1d4f7a","weak_signal":"#9ca3af","no_match":"#d1d5db"} %}
{% set CAT_LABEL = {"critical":"CRITICAL MATCH","high":"HIGH MATCH","medium":"MEDIUM MATCH","partial":"PARTIAL MATCH","weak_signal":"WEAK SIGNAL","no_match":"NO MATCH"} %}

{% for section in report['sections'] %}
{% if section['title'] == "Crime - known patterns for his industry" and crime_result %}
<div class="panel">
  <h2>{{ section['title'] }}</h2>
  <div style="display:flex; gap:16px; margin-bottom:18px; font-size:.85rem; border-bottom:1px solid var(--line); padding-bottom:12px">
    <span style="color:var(--muted)">{{ crime_result.get('match_results', []) | length }} typologies checked</span>
    <span style="color:#b91c1c; font-weight:600">{{ crime_result.get('match_count', 0) }} matched</span>
    <span style="color:var(--muted)">{{ (crime_result.get('match_results', []) | length) - crime_result.get('match_count', 0) }} no match</span>
  </div>
  {% for m in crime_result.get('match_results', []) %}
  {% set cat = m.get('category', 'no_match') %}
  {% set typo = typologies_by_id.get(m.get('typology_id'), {}) %}
  <div style="padding:14px 16px; border-radius:6px; margin-bottom:10px;
              background:{{ CAT_BG.get(cat,'#f9fafb') }};
              border-left:4px solid {{ CAT_BORDER.get(cat,'#d1d5db') }}">
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:6px">
      <span style="font-size:.7rem; font-weight:700; letter-spacing:.06em; padding:2px 8px; border-radius:3px;
                   background:{{ CAT_BORDER.get(cat,'#9ca3af') }}; color:#fff">{{ CAT_LABEL.get(cat, cat.upper()) }}</span>
      <strong style="font-size:.97rem; color:#111">{{ m.get('name', m.get('typology_id')) }}</strong>
      {% if typo.get('crime_category') %}
      <span style="font-size:.76rem; color:#6b7280; background:#f3f4f6; padding:1px 7px; border-radius:3px; border:1px solid #e5e7eb">{{ typo['crime_category'].replace('_', ' ') }}</span>
      {% endif %}
    </div>
    {% if typo.get('description') %}
    <p style="margin:0 0 10px; font-size:.83rem; color:#4a5568; line-height:1.5">{{ typo['description'] }}</p>
    {% endif %}
    <div style="display:flex; gap:28px; flex-wrap:wrap">
      {% if m.get('core_indicators') %}
      <div>
        <div style="font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#6b7280; margin-bottom:5px">Core</div>
        {% for ind in m['core_indicators'] %}
        {% if ind in m.get('core_fired', []) %}
        <div style="font-size:.83rem; display:flex; gap:7px; align-items:baseline; padding:2px 0; color:#111"><span style="color:#b91c1c; font-weight:700; flex-shrink:0">✓</span> {{ INDICATOR_LABELS.get(ind, ind.replace('_',' ')) }}</div>
        {% else %}
        <div style="font-size:.83rem; display:flex; gap:7px; align-items:baseline; padding:2px 0; color:#9ca3af"><span style="flex-shrink:0">—</span> {{ INDICATOR_LABELS.get(ind, ind.replace('_',' ')) }}</div>
        {% endif %}
        {% endfor %}
      </div>
      {% endif %}
      {% if m.get('supporting_indicators') %}
      <div>
        <div style="font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:#6b7280; margin-bottom:5px">Supporting</div>
        {% for ind in m['supporting_indicators'] %}
        {% if ind in m.get('supporting_fired', []) %}
        <div style="font-size:.83rem; display:flex; gap:7px; align-items:baseline; padding:2px 0; color:#111"><span style="color:#b45309; font-weight:700; flex-shrink:0">✓</span> {{ INDICATOR_LABELS.get(ind, ind.replace('_',' ')) }}</div>
        {% else %}
        <div style="font-size:.83rem; display:flex; gap:7px; align-items:baseline; padding:2px 0; color:#9ca3af"><span style="flex-shrink:0">—</span> {{ INDICATOR_LABELS.get(ind, ind.replace('_',' ')) }}</div>
        {% endif %}
        {% endfor %}
      </div>
      {% endif %}
    </div>
    {% if typo.get('source_quotes') %}{% set q = typo['source_quotes'][0] %}{% if q.get('quote') %}
    <blockquote style="margin:10px 0 0; padding:8px 12px; border-left:3px solid {{ CAT_BORDER.get(cat,'#d1d5db') }}; background:rgba(255,255,255,.55); font-size:.8rem; color:#4a5568; font-style:italic; line-height:1.5">"{{ q['quote'] }}"{% if q.get('report_id') %}<footer style="margin-top:3px; font-style:normal; font-size:.74rem; color:#9ca3af">[{{ q['report_id'] }}]</footer>{% endif %}</blockquote>
    {% endif %}{% endif %}
  </div>
  {% endfor %}
  {% if not crime_result.get('match_results') %}<p class="muted">No typologies were checked.</p>{% endif %}
</div>
{% else %}
<div class="panel">
  <h2>{{ section['title'] }}</h2>
  <ul class="report-items">
    {% for item in section['items'] %}
    <li>
      <span class="mark" style="color:{{ MARK_COLORS[item['mark']] }}">{{ symbols[item['mark']] }}</span>
      <div>{{ item['text'] }}
        {% for sub in item['sub'] %}<div class="sub">{{ sub }}</div>{% endfor %}
      </div>
    </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
{% endfor %}
{% endif %}

{% if benchmark %}
<div class="panel">
  <h2>Full evidence</h2>
  {% for part in report['evidence'] %}
  <details>
    <summary>{{ part['title'] }}</summary>
    <ul class="evidence">{% for line in part['lines'] %}<li>{{ line }}</li>{% endfor %}</ul>
  </details>
  {% endfor %}
</div>
{% else %}
<div class="panel">
  <p class="muted" style="margin:0">This assessment was saved before the combined verdict. Run the assessment
  again to get one verdict with its reasons.</p>
</div>
{% endif %}
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
    {% set outcome = a.get('outcome') or (a.get('assessment') or {}).get('outcome', '') %}
    <tr>
      <td>{{ a.get('client_ref', '') }}</td>
      <td>{{ (a.get('assessed_at') or a.get('assessment', {}).get('assessed_on', ''))[:10] }}</td>
      <td>{{ 'combined assessment' if a.get('verdict') else a.get('kind', 'crime scan + benchmark') }}</td>
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
  <p style="color:var(--muted); margin:0 0 18px">The crime scan, forecast and benchmark run together as one assessment in the background. A first run can take a few minutes; saved AI replies make reruns quick.</p>
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
    "assessments.html":   ASSESSMENTS,
    "error.html":         ERROR,
})
# available inside every template and macro
app.jinja_env.globals.update(PEP_OPTIONS=PEP_OPTIONS, PAYMENT_OPTIONS=PAYMENT_OPTIONS)
 
 
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)