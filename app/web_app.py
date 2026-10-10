"""
web_app.py - simple web version of the input layer (Flask).

Put this file in the same folder as io_manager.py and data_manager.py (the app/ folder).
Run it from the PROJECT ROOT (the folder that contains app/):

    pip install flask
    python -m app.web_app

Then open http://127.0.0.1:5000 in a browser.

It reuses save_case_record / load_all_records / upload_supporting_document /
download_supporting_document from data_manager, so your friend's JSON + sync
changes keep working as long as those function names stay the same.
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
from app.io_manager import generate_client_id, _number

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")


# ============================================================================
# What the form contains.  Each field is (key, label, kind).
# kind: text | date | yesno | pep | textarea
# ============================================================================

PEP_OPTIONS = ["None", "Domestic PEP", "Foreign PEP",
               "International organisation PEP", "Family member / close associate"]

PROFILE_FIELDS = [
    ("full_name", "Full name", "text"),
    ("age", "Age", "text"),
    ("nationality", "Nationality", "text"),
    ("country_of_residence", "Country of residence", "text"),
    ("latest_occupation", "Latest occupation", "text"),
    ("latest_seniority", "Latest seniority", "text"),
    ("latest_industry", "Latest industry", "text"),
    ("current_company", "Current company", "text"),
    ("career_start_year", "Career start year", "text"),
    ("years_of_employment", "Years of employment", "text"),
    ("date_profile_created", "Date profile created", "date"),
]
CAREER_FIELDS = [
    ("start_year", "Start year", "text"),
    ("end_year", "End year (blank if current)", "text"),
    ("job_title", "Job title", "text"),
    ("seniority", "Seniority", "text"),
    ("industry", "Industry", "text"),
    ("company_name", "Company name", "text"),
    ("company_size", "Company size", "text"),
    ("country", "Country", "text"),
]
ASSET_FIELDS = [
    ("asset_type", "Asset type", "text"),
    ("asset_description", "Asset description", "text"),
    ("country", "Country", "text"),
    ("year_acquired", "Year acquired", "text"),
    ("price_paid", "Price paid", "text"),
    ("payment_method", "Payment method", "text"),
    ("year_sold", "Year sold (blank if still owned)", "text"),
    ("sale_price", "Sale price (blank if still owned)", "text"),
    ("current_value", "Current value", "text"),
    ("yearly_income", "Yearly income", "text"),
    ("currency", "Currency", "text"),
]
COMPOSITION_FIELDS = [
    ("property", "Property (%)", "text"),
    ("listed_equities", "Listed equities (%)", "text"),
    ("private_business", "Private business (%)", "text"),
    ("cash", "Cash (%)", "text"),
]
DECLARATION_FIELDS = [
    ("declared_net_worth", "Declared net worth", "text"),
    ("expected_assets_under_management", "Expected assets under management", "text"),
    ("owns_property", "Owns property?", "yesno"),
    ("owns_investments", "Owns investments?", "yesno"),
    ("pep_status", "PEP status", "pep"),
    ("countries_where_wealth_was_generated",
     "Countries where wealth was generated (comma separated)", "text"),
    ("date_declared", "Date declared", "date"),
]
SOW_FIELDS = [
    ("declaration_text", "Declaration text", "textarea"),
    ("written_by", "Written by", "text"),
    ("date_written", "Date written", "date"),
]

# (form prefix, key in the record, title, fields, "dict" = one set of boxes / "list" = many entries)
SECTIONS = [
    ("profile", "client_profile", "Client profile", PROFILE_FIELDS, "dict"),
    ("career", "career_timeline", "Career history", CAREER_FIELDS, "list"),
    ("assets", "asset_timeline", "Asset history", ASSET_FIELDS, "list"),
    ("composition", "asset_composition", "Asset composition", COMPOSITION_FIELDS, "dict"),
    ("declarations", "client_declarations", "Client declarations", DECLARATION_FIELDS, "dict"),
    ("sow", "sow_declaration", "Source of wealth declaration", SOW_FIELDS, "dict"),
]


# ============================================================================
# Helpers
# ============================================================================

def today() -> str:
    return datetime.date.today().isoformat()


def get_record(client_ref: str):
    """Find one client record by reference. Returns None if not found."""
    for record in load_all_records():
        if record.get("client_ref") == client_ref:
            return record
    return None


def blank_record() -> dict:
    """An empty record for the New Client form, with today's date pre-filled."""
    return {
        "client_profile": {"date_profile_created": today()},
        "client_declarations": {"date_declared": today()},
        "sow_declaration": {"date_written": today()},
    }


def record_from_form(form, client_ref: str, old: dict | None = None) -> dict:
    """Turn the submitted form into a client record.

    'old' is the existing record when editing, so fields the form doesn't know
    about (documents, assessment results, ...) are kept.
    """
    record = dict(old) if old else {}
    record["client_ref"] = client_ref

    for prefix, record_key, title, fields, kind in SECTIONS:
        keys = [field[0] for field in fields]

        if kind == "dict":
            data = dict(record.get(record_key, {}))
            for key in keys:
                data[key] = form.get(f"{prefix}.{key}", "").strip()
            record[record_key] = data

        else:
            # every entry repeats the same field names, so getlist gives one column per field
            columns = [form.getlist(f"{prefix}.{key}") for key in keys]
            items = []
            for values in zip(*columns):
                item = {key: value.strip() for key, value in zip(keys, values)}
                if any(item.values()):          # skip completely empty entries
                    items.append(item)
            record[record_key] = items

    record.setdefault("supporting_documents", [])
    return record


def find_problems(record: dict) -> list:
    """Check the record. Returns a list of problems ([] means it's fine)."""
    problems = []
    profile = record["client_profile"]
    declarations = record["client_declarations"]
    composition = record["asset_composition"]
    this_year = datetime.date.today().year

    required = [("full_name", "Full name"), ("nationality", "Nationality"),
                ("country_of_residence", "Country of residence"),
                ("latest_occupation", "Latest occupation"),
                ("latest_industry", "Latest industry")]
    for key, label in required:
        if not profile[key]:
            problems.append(f"{label} is required.")

    age = profile["age"]
    if not age.isdigit() or not 18 <= int(age) <= 120:
        problems.append("Age must be a whole number between 18 and 120.")

    start = profile["career_start_year"]
    if not start.isdigit() or not 1900 <= int(start) <= this_year:
        problems.append(f"Career start year must be a year between 1900 and {this_year}.")

    for key, label in [("declared_net_worth", "Declared net worth"),
                       ("expected_assets_under_management", "Expected assets under management")]:
        amount = _number(declarations[key])
        if amount is None or amount <= 0:
            problems.append(f"{label} must be a number above zero.")

    parts = [_number(composition[key]) for key in ("property", "listed_equities",
                                                   "private_business", "cash")]
    if None in parts or not 95 <= sum(parts) <= 105:
        problems.append("Asset composition must be numbers that add up to 100%.")

    if len(record["sow_declaration"]["declaration_text"]) < 50:
        problems.append("Source of wealth declaration must be at least 50 characters.")

    return problems


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
        record = record_from_form(request.form, generate_client_id())
        problems = find_problems(record)
        if not problems:
            if save_case_record(record):
                flash("Client saved.")
                return redirect(url_for("view_client", client_ref=record["client_ref"]))
            problems = ["The client could not be saved. Check the database connection."]
        return render_template("client_form.html", heading="New client", record=record,
                               problems=problems, SECTIONS=SECTIONS, PEP_OPTIONS=PEP_OPTIONS)

    return render_template("client_form.html", heading="New client", record=blank_record(),
                           problems=[], SECTIONS=SECTIONS, PEP_OPTIONS=PEP_OPTIONS)


@app.route("/client/<client_ref>")
def view_client(client_ref):
    record = get_record(client_ref)
    if record is None:
        abort(404)
    return render_template("client_view.html", record=record, SECTIONS=SECTIONS)


@app.route("/client/<client_ref>/edit", methods=["GET", "POST"])
def edit_client(client_ref):
    old = get_record(client_ref)
    if old is None:
        abort(404)

    if request.method == "POST":
        record = record_from_form(request.form, client_ref, old)
        problems = find_problems(record)
        if not problems:
            if save_case_record(record):
                flash("Changes saved.")
                return redirect(url_for("view_client", client_ref=client_ref))
            problems = ["The changes could not be saved. Check the database connection."]
        return render_template("client_form.html", heading=f"Edit {client_ref}", record=record,
                               problems=problems, SECTIONS=SECTIONS, PEP_OPTIONS=PEP_OPTIONS)

    return render_template("client_form.html", heading=f"Edit {client_ref}", record=old,
                           problems=[], SECTIONS=SECTIONS, PEP_OPTIONS=PEP_OPTIONS)


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
      <td>{{ c.get('client_profile', {}).get('full_name', '') }}</td>
      <td>{{ c.get('client_profile', {}).get('latest_occupation', '') }}</td>
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
  <label>{{ field[1] }}
    {% if field[2] == 'textarea' %}
      <textarea name="{{ prefix }}.{{ field[0] }}" rows="6">{{ value }}</textarea>
    {% elif field[2] == 'yesno' %}
      <select name="{{ prefix }}.{{ field[0] }}">
        <option value="">Choose</option>
        {% for option in ['yes', 'no'] %}<option value="{{ option }}" {{ 'selected' if value == option else '' }}>{{ option }}</option>{% endfor %}
      </select>
    {% elif field[2] == 'pep' %}
      <select name="{{ prefix }}.{{ field[0] }}">
        <option value="">Choose</option>
        {% for option in PEP_OPTIONS %}<option {{ 'selected' if value == option else '' }}>{{ option }}</option>{% endfor %}
        {% if value and value not in PEP_OPTIONS %}<option selected>{{ value }}</option>{% endif %}
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
  {% for prefix, record_key, title, fields, kind in SECTIONS %}
  <fieldset>
    <legend>{{ title }}</legend>
    {% if kind == 'dict' %}
      {% set data = record.get(record_key, {}) %}
      <div class="grid">
        {% for field in fields %}
          {% if field[2] == 'textarea' %}</div>{{ box(prefix, field, data.get(field[0], '')) }}<div class="grid">
          {% else %}{{ box(prefix, field, data.get(field[0], '')) }}{% endif %}
        {% endfor %}
      </div>
    {% else %}
      <div id="list-{{ prefix }}">
        {% for item in (record.get(record_key, []) or [{}]) %}{{ entry(prefix, fields, item) }}{% endfor %}
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
<h1>{{ record.get('client_profile', {}).get('full_name', 'Client') }}
  <span class="muted" style="font-size:1rem">{{ record.client_ref }}</span></h1>

<div class="actions" style="margin-bottom:16px">
  <a class="button" href="{{ url_for('edit_client', client_ref=record.client_ref) }}">Edit client</a>
  <a class="button secondary" href="{{ url_for('home') }}">Back to clients</a>
</div>

{% for prefix, record_key, title, fields, kind in SECTIONS %}
<div class="panel">
  <h2>{{ title }}</h2>
  {% if kind == 'dict' %}
    {% set data = record.get(record_key, {}) %}
    <table>
      {% for field in fields %}
      <tr><th>{{ field[1] }}</th><td>{{ data.get(field[0], '') }}</td></tr>
      {% endfor %}
    </table>
  {% else %}
    {% for item in record.get(record_key, []) %}
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
        - <a href="{{ url_for('download_document', client_ref=record.client_ref, file_id=document.file_id) }}">Download</a>
      {% endif %}
    </p>
  {% else %}
    <p class="muted">No documents uploaded yet.</p>
  {% endfor %}

  <form method="post" action="{{ url_for('upload_documents', client_ref=record.client_ref) }}"
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


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)