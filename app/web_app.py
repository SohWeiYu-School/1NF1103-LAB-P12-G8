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
 
from flask import (Flask, abort, flash, redirect, render_template,
                   request, send_file, url_for)
from jinja2 import DictLoader
from werkzeug.utils import secure_filename
 
from app.data_manager import (
    save_case_record,
    load_all_records,
    upload_supporting_document,
    download_supporting_document,
)
from app.io_manager import generate_client_id, validate_case, _number
 
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")
 
 
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
    """Turn the form text into a record. Returns (record, problems)."""
    problems = []
    record = dict(old) if old else {}
    record["client_ref"] = client_ref
 
    # ---- client ----
    client = values["client"]
    for key, label in [("name", "Full name"), ("nationality", "Nationality"),
                       ("country_of_residence", "Country of residence")]:
        if not client[key]:
            problems.append(f"{label} is required.")
    record["name"] = client["name"]
    record["nationality"] = client["nationality"]
 
    try:
        datetime.date.fromisoformat(client["current_date"])
    except ValueError:
        problems.append("Assessment date must be a real date.")
    record["current_date"] = client["current_date"]
 
    if client["age"].isdigit() and 18 <= int(client["age"]) <= 120:
        age = int(client["age"])
    else:
        problems.append("Age must be a whole number between 18 and 120.")
        age = 0
 
    # ---- career history ----
    jobs = []
    for number, row in enumerate(values["jobs"], start=1):
        label = f"Job {number}"
        for key, text in [("occupation", "job title"), ("seniority", "seniority"),
                          ("industry", "industry"), ("employer_name", "employer"),
                          ("employer_size", "employer size"), ("country", "country")]:
            if not row[key]:
                problems.append(f"{label}: {text} is required.")
        jobs.append({
            "start_year": read_year(row["start_year"], f"{label}: start year", problems),
            "end_year": read_year(row["end_year"], f"{label}: end year", problems, blank_ok=True),
            "occupation": row["occupation"],
            "seniority": row["seniority"],
            "industry": row["industry"],
            "employer_name": row["employer_name"],
            "employer_size": row["employer_size"],
            "country": row["country"],
        })
    if not jobs:
        problems.append("Add at least one job (the current job has no end year).")
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
        for key, text in [("asset_type", "name"), ("country", "country")]:
            if not row[key]:
                problems.append(f"{label}: {text} is required.")
        if row["payment_method"] not in PAYMENT_OPTIONS:
            problems.append(f"{label}: choose how it was paid for.")
        investments.append({
            "asset_type": row["asset_type"],
            "country": row["country"],
            "year_acquired": read_year(row["year_acquired"], f"{label}: year bought", problems),
            "price_paid": read_amount(row["price_paid"], f"{label}: price paid", problems,
                                      allow_zero=False),
            "current_declared_value": read_amount(row["current_declared_value"],
                                                  f"{label}: declared value", problems),
            "payment_method": row["payment_method"],
            "income_produced": read_amount(row["income_produced"], f"{label}: income",
                                           problems, blank_zero=True),
        })
    record["investments"] = investments
 
    # ---- claims ----
    claims = dict(record.get("claims") or {})
    form_claims = values["claims"]
    claims["declared_net_worth"] = read_amount(form_claims["declared_net_worth"],
                                               "Declared net worth", problems, allow_zero=False)
    claims["expected_aum"] = read_amount(form_claims["expected_aum"],
                                         "Expected assets to be managed", problems,
                                         allow_zero=False)
    if not form_claims["pep_status"]:
        problems.append("PEP status is required.")
    claims["pep_status"] = form_claims["pep_status"]
 
    countries = [c.strip() for c in form_claims["wealth_countries"].split(",") if c.strip()]
    if not countries:
        problems.append("Enter at least one country the wealth came from.")
    claims["wealth_countries"] = countries
 
    split = {}
    for key, label, kind in COMPOSITION_FIELDS:
        split[key] = read_amount(values["composition"][key], label.replace(" (%)", ""), problems)
    if not 95 <= sum(split.values()) <= 105:
        problems.append(f"The wealth split adds up to {trim(sum(split.values()))}%, it must be 100%.")
    claims["asset_composition"] = {key: value / 100 for key, value in split.items()}
    record["claims"] = claims
 
    # ---- declaration ----
    record["sow_declaration"] = values["sow"]["sow_declaration"]
    if len(record["sow_declaration"]) < 50:
        problems.append("The source of wealth declaration must be at least 50 characters.")
 
    record.setdefault("reviews", [])
    record.setdefault("supporting_documents", [])
    return record, problems
 
 
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
 
 
def show_form(heading: str, values: dict, problems: list):
    return render_template("client_form.html", heading=heading, values=values,
                           problems=problems, SECTIONS=SECTIONS)
 
 
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
        record, problems = build_record(values, generate_client_id())
        if not problems:
            problems = check_record(record)
        if not problems:
            if save_case_record(record):
                flash("Client saved.")
                return redirect(url_for("view_client", client_ref=record["client_ref"]))
            problems = ["The client could not be saved. Check the database connection."]
        return show_form("New client", values, problems)
 
    return show_form("New client", values_from_record({"current_date": today()}), [])
 
 
@app.route("/client/<client_ref>")
def view_client(client_ref):
    record = get_record(client_ref)
    if record is None:
        abort(404)
    return render_template("client_view.html", record=record,
                           values=values_from_record(record), SECTIONS=SECTIONS)
 
 
@app.route("/client/<client_ref>/edit", methods=["GET", "POST"])
def edit_client(client_ref):
    old = get_record(client_ref)
    if old is None:
        abort(404)
 
    if request.method == "POST":
        values = values_from_form(request.form)
        record, problems = build_record(values, client_ref, old)
        if not problems:
            problems = check_record(record)
        if not problems:
            if save_case_record(record):
                flash("Changes saved.")
                return redirect(url_for("view_client", client_ref=client_ref))
            problems = ["The changes could not be saved. Check the database connection."]
        return show_form(f"Edit {client_ref}", values, problems)
 
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
  :root { --ink:#1f2933; --muted:#616e7c; --line:#d9dee3; --bg:#f6f7f8; --accent:#1d4f7a; --bad:#a61b1b; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); line-height:1.5; }
  header { background:#fff; border-bottom:1px solid var(--line); padding:14px 24px; }
  header a { color:var(--ink); font-weight:600; text-decoration:none; }
  main { max-width:880px; margin:0 auto; padding:24px; }
  h1 { font-size:1.5rem; margin:0 0 16px; }
  h2 { font-size:1.1rem; margin:0 0 12px; }
  a { color:var(--accent); }
  .panel { background:#fff; border:1px solid var(--line); border-radius:6px; padding:16px 20px; margin-bottom:16px; }
  .msg { background:#e8f1f8; border:1px solid #b9d3e6; border-radius:6px; padding:10px 14px; margin-bottom:16px; }
  .errors { background:#fdeaea; border:1px solid #e3b0b0; color:var(--bad); border-radius:6px; padding:10px 14px 10px 30px; margin-bottom:16px; }
  .actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  button, .button { font:inherit; background:var(--accent); color:#fff; border:0; border-radius:5px; padding:8px 16px; cursor:pointer; text-decoration:none; display:inline-block; }
  button.secondary, .button.secondary { background:#fff; color:var(--accent); border:1px solid var(--accent); }
  button.danger { background:#fff; color:var(--bad); border:1px solid var(--bad); padding:4px 10px; }
  button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, a:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  label { display:block; font-size:.9rem; color:var(--muted); margin-bottom:10px; }
  input, select, textarea { display:block; width:100%; font:inherit; color:var(--ink); padding:7px 9px; margin-top:3px; border:1px solid #b8c0c8; border-radius:5px; background:#fff; }
  fieldset { border:1px solid var(--line); border-radius:6px; background:#fff; padding:16px 20px; margin:0 0 16px; }
  legend { font-weight:600; padding:0 6px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:0 16px; }
  .wide { grid-column:1 / -1; }
  .entry { border:1px solid var(--line); border-radius:6px; padding:12px 14px; margin-bottom:12px; background:#fafbfc; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:500; width:38%; }
  .search { display:flex; gap:10px; }
  .search input { margin:0; }
  .muted { color:var(--muted); }
  .savebar { position:sticky; bottom:0; background:var(--bg); padding:12px 0; border-top:1px solid var(--line); }
</style>
</head>
<body>
<header><a href="{{ url_for('home') }}">Source of Wealth Screening</a></header>
<main>
  {% for message in get_flashed_messages() %}<div class="msg">{{ message }}</div>{% endfor %}
  {% block content %}{% endblock %}
</main>
</body>
</html>
"""
 
HOME = """{% extends "base.html" %}
{% block content %}
<h1>Clients</h1>
 
<div class="panel">
  <div class="actions" style="justify-content:space-between">
    <form class="search" action="{{ url_for('find') }}" method="get">
      <input name="ref" placeholder="Client reference, e.g. CL-1A2B3C4D" aria-label="Client reference" style="width:300px">
      <button class="secondary" type="submit">Find client</button>
    </form>
    <a class="button" href="{{ url_for('new_client') }}">New client</a>
  </div>
</div>
 
<div class="panel">
  {% if not clients %}
    <p class="muted">No clients yet. Choose New client to add the first one.</p>
  {% else %}
  <table>
    <tr><th style="width:auto">Reference</th><th style="width:auto">Name</th><th style="width:auto">Occupation</th><th style="width:auto"></th></tr>
    {% for c in clients %}
    <tr>
      <td>{{ c.get('client_ref', '') }}</td>
      <td>{{ c.get('name', '') }}</td>
      <td>{{ (c.get('career') or {}).get('occupation', '') }}</td>
      <td><a href="{{ url_for('view_client', client_ref=c.get('client_ref', '')) }}">Open</a></td>
    </tr>
    {% endfor %}
  </table>
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
<ul class="errors">{% for problem in problems %}<li>{{ problem }}</li>{% endfor %}</ul>
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
 
<div class="actions" style="margin-bottom:16px">
  <a class="button" href="{{ url_for('edit_client', client_ref=record.get('client_ref', '')) }}">Edit client</a>
  <a class="button secondary" href="{{ url_for('home') }}">Back to clients</a>
</div>
 
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
 
app.jinja_env.loader = DictLoader({
    "base.html": BASE,
    "home.html": HOME,
    "client_form.html": FORM,
    "client_view.html": VIEW,
})
# available inside every template and macro
app.jinja_env.globals.update(PEP_OPTIONS=PEP_OPTIONS, PAYMENT_OPTIONS=PAYMENT_OPTIONS)
 
 
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)

