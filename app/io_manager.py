"""
io_manager.py - the input/output layer: every boundary between the system and the user.

    * Client input and editing (New Client form, Find / Edit Existing Client,
      supporting documents) - saved locally as JSON through data_manager (synced to MongoDB on request)
    * The combined assessment: progress steps and the one-verdict report (the web app
      shows the same report, built by logic_manager.verdict_report)
    * Forecasting: turns a saved client record into a forecasting case (asking the
      officer only for what's missing), collects periodic reviews, and prints the
      review reports
    * ALL print() calls in the system live in this file

The forecasting ask_* functions take an `ask` function (normally read_answer, i.e.
typing) so tests can feed in typed answers.
"""

import datetime
import os
import textwrap
import uuid
from typing import Callable

from jsonschema import Draft202012Validator

from app import utilities

# The file picker needs tkinter, which isn't installed everywhere (e.g. the Docker image).
# Without it the app still runs; only choosing files to upload or download is unavailable.
try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    tk = filedialog = None

Ask = Callable[[str], str]
LINE = "-" * 72
PAYMENT_METHODS = ["cash", "loan", "asset_sale"]

def show_app_menu() -> str:
    """Display the unified main menu and return the user's choice (1-9)."""
    print("\n========================================")
    print("     SOURCE OF WEALTH SCREENING SYSTEM")
    print("========================================")
    print("1. New Client (type in, then the combined assessment)")
    print("2. New Client from Documents (auto-fill from a folder of documents)")
    print("3. Find / Edit Existing Client")
    print("4. Assess an Existing Client (crime scan + forecast + benchmark -> one verdict)")
    print("5. Run a Periodic Review")
    print("6. List Saved Assessments")
    print("7. View All Clients")
    print("8. Manual Sync to Cloud Database")
    print("9. Quit")
    while True:
        choice = input("\nEnter your choice (1-9): ").strip()
        if choice in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            return choice
        print("Invalid choice. Please enter 1 to 9.")

def show_message(text: str) -> None:
    #Print an informational message.
    print(text)

def show_error(text: str) -> None:
    #Print an error message.
    print(f"[ERROR] {text}")

def show_step(number: int, total: int, label: str) -> None:
    """One progress line of the combined assessment (the web app shows the same steps)."""
    print(f"  [{number:>2}/{total}] {label}...")


REPORT_WIDTH = 96


def format_verdict_report(report: dict) -> str:
    """The combined assessment as text: the same header, sections and evidence the web app shows."""
    head = report["header"]
    symbols = {entry["mark"]: entry["symbol"] for entry in report["legend"]}
    rule = "\u2550" * REPORT_WIDTH
    lines = [rule, f"  COMBINED ASSESSMENT  {head['client_ref']}  {head['name']}  ({head['assessed_on']})", rule,
             f"  VERDICT:      {head['verdict_label']}",
             f"  Next step:    {head['next_step']}",
             f"  Next review:  {head['next_review_date']}",
             f"  Policy:       {head['policy_version']}", ""]
    lines += textwrap.wrap(head["summary"], REPORT_WIDTH - 2, initial_indent="  ", subsequent_indent="  ")
    lines += ["", "  " + "   ".join(f"{e['symbol']} {e['meaning']}" for e in report["legend"])]

    def section(title: str, items: list[dict]) -> None:
        lines.extend(["", f"  {title.upper()}", "  " + "\u2500" * (REPORT_WIDTH - 2)])
        for item in items:
            lines.extend(textwrap.wrap(item["text"], REPORT_WIDTH - 2,
                                       initial_indent=f"  {symbols[item['mark']]} ", subsequent_indent="    "))
            for sub in item["sub"]:
                lines.extend(textwrap.wrap(sub, REPORT_WIDTH - 2, initial_indent="      ",
                                           subsequent_indent="      "))

    for part in report["sections"]:
        section(part["title"], part["items"])
    lines.extend(["", "  FULL EVIDENCE: " + " \u00b7 ".join(report.get("evidence_titles", [])), rule])
    return "\n".join(lines)


def format_evidence(report: dict) -> str:
    """The full evidence behind the verdict (the web app shows the same sections, collapsed)."""
    rule = "\u2550" * REPORT_WIDTH
    lines = [rule, f"  FULL EVIDENCE  {report['header']['client_ref']}", rule]
    for part in report["evidence"]:
        lines.extend(["", f"  {part['title']}"])
        for line in part["lines"]:
            lines.extend(textwrap.wrap(line, REPORT_WIDTH - 2, initial_indent="    ", subsequent_indent="      "))
    lines.append(rule)
    return "\n".join(lines)


def show_verdict_report(report: dict, ask_for_evidence: bool = True) -> None:
    """The short report, then (if the officer wants it) the full evidence."""
    print("\n" + format_verdict_report(report) + "\n")
    if ask_for_evidence and ask_yes_no("Show the full evidence?"):
        print("\n" + format_evidence(report) + "\n")


def generate_client_id() -> str:
    """Generate a unique client reference ID."""

    return "CL-" + uuid.uuid4().hex[:8].upper()


def get_client_profile() -> dict:
    """Collect name, nationality and current position."""
    print("\n=== Client Profile ===")
    name        = ask_words("Full name")
    nationality = ask_words("Nationality")
    print("\n=== Current Position ===")
    occupation    = ask_words("Current job title", LABEL)
    seniority     = ask_words("Seniority (e.g. Senior Manager, C-suite)", LABEL)
    industry      = ask_words("Industry", LABEL)
    employer_name = ask_words("Employer name", LABEL)
    employer_size = ask_count("Employer size (number of employees, e.g. 2500)")
    residence     = ask_words("Country of residence")
    age           = ask_int("Age", minimum=18, maximum=100)
    while True:
        start = ask_year("Year first career started")
        if start >= datetime.date.today().year - age + 14:
            break
        show_error("That would mean the client started working before age 14. Please check the age and year.")
    career = {"occupation": occupation, "seniority": seniority, "industry": industry,
              "employer_name": employer_name, "employer_size": employer_size,
              "country_of_residence": residence, "age": age, "career_start_year": start}
    return {"name": name, "nationality": nationality, "career": career}


def _ask_job() -> dict:
    start_year = ask_year("Start year")
    end_year   = ask_year("End year (leave blank if current)", minimum=start_year, allow_blank=True)
    return {
        "start_year": start_year, "end_year": end_year,
        "occupation":    ask_words("Job title", LABEL),
        "seniority":     ask_words("Seniority", LABEL),
        "industry":      ask_words("Industry", LABEL),
        "employer_name": ask_words("Employer name", LABEL),
        "employer_size": ask_count("Employer size (number of employees)"),
        "country":       ask_words("Country"),
    }


def get_career_history() -> list:
    """Collect previous jobs (before the current one)."""
    career_history = []
    prompt = "\nDoes the client have a previous job to add?"
    while ask_yes_no(prompt):
        print("\n=== Previous Job ===")
        career_history.append(_ask_job())
        prompt = "\nAdd another previous job?"
    return career_history


def _ask_asset() -> dict:
    asset_type  = ask_words("Asset type (e.g. Property, Shares)", LABEL)
    description = ask_words("Description", LABEL)
    country     = ask_words("Country")
    year_acquired = ask_year("Year acquired")
    price_paid    = ask_money("Price paid", allow_zero=False)
    payment_method = PAYMENT_METHODS[ask_choice("Payment method", PAYMENT_METHODS)]
    year_sold = sale_price = None
    if ask_yes_no("Has this asset been sold?"):
        year_sold  = ask_year("Year sold", minimum=year_acquired)
        sale_price = ask_money("Sale price")
    # Same layout as the web form and the documents auto-fill: "Property - Condo", current_declared_value,
    # income_produced. year_sold, sale_price and currency are extra details only this screen asks for.
    return {
        "asset_type": f"{asset_type} - {description}" if description.lower() not in asset_type.lower() else asset_type,
        "country": country,
        "year_acquired": year_acquired, "price_paid": price_paid,
        "payment_method": payment_method, "year_sold": year_sold, "sale_price": sale_price,
        "current_declared_value": ask_money("Current value (0 if sold)"),
        "income_produced":        ask_money("Yearly income from this asset (0 if none)"),
        "currency":               ask_currency(),
    }


def get_investments() -> list:
    investments = []
    prompt = "\nDoes the client have any investments or assets?"
    while ask_yes_no(prompt):
        print("\n=== Investment / Asset ===")
        investments.append(_ask_asset())
        prompt = "\nAdd another investment or asset?"
    return investments


def get_claims() -> dict:
    """Collect financial claims and asset composition."""
    print("\n=== Financial Claims ===")
    declared_net_worth = ask_money("Declared net worth (SGD)", allow_zero=False)
    expected_aum       = ask_money("Expected assets to be managed by the bank (SGD)", allow_zero=False)
    pep_options = ["None", "Domestic PEP", "Foreign PEP", "International organisation PEP",
                   "Family member / close associate of a PEP"]
    pep_status = pep_options[ask_choice("PEP status", pep_options)]
    wealth_countries = ask_countries("Countries where wealth was generated (comma-separated)")

    print("\n=== Asset Composition ===")
    print("Enter the percentage of total wealth for each category (must add up to 100%).")
    asset_composition = ask_composition()

    print("\n=== Wealth Velocity ===")
    biggest_wealth_jump = wealth_jump_years = None
    if ask_yes_no("Is there a known biggest single-period wealth increase?"):
        biggest_wealth_jump = ask_money("Biggest single-period wealth increase (SGD)", allow_zero=False)
        wealth_jump_years   = ask_int("Over how many years did this increase occur", minimum=1, maximum=60)

    print("\n=== Counterparties ===")
    cp_str = input("Known counterparties, banks/brokers/firms (comma-separated, blank if none): ").strip()
    declared_counterparties = [c.strip() for c in cp_str.split(",") if c.strip()]

    return {
        "declared_net_worth": declared_net_worth, "expected_aum": expected_aum,
        "pep_status": pep_status, "wealth_countries": wealth_countries,
        "asset_composition": asset_composition,
        "biggest_wealth_jump": biggest_wealth_jump, "wealth_jump_years": wealth_jump_years,
        "declared_counterparties": declared_counterparties,
    }


def get_sow_declaration() -> str:
    print("\n=== Source of Wealth Declaration ===")
    return ask_text("Describe how the client accumulated their wealth (min 50 characters)", min_length=50)

def get_supporting_documents(upload_fn=None) -> list:
    """
    Ask whether the client has supporting documents.

    If yes, open a Tkinter file picker and upload
    the selected documents.
    """

    print("\n=== Supporting Documents ===")

    if not ask_yes_no("Does the client have any supporting documents?"):
        return []

    if tk is None:
        print("The file picker isn't available on this computer (tkinter is not installed).")
        return []

    print("\nOpening file explorer...")

    root = tk.Tk()
    root.withdraw()

    file_paths = filedialog.askopenfilenames(
        title="Select Supporting Documents",
        filetypes=[
            ("All supported files", "*.*"),
            ("PDF files", "*.pdf"),
            ("Word documents", "*.docx"),
            ("Excel files", "*.xlsx"),
            ("Images", "*.png;*.jpg;*.jpeg")
        ]
    )

    root.destroy()

    if not file_paths:
        print("No documents were selected.")
        return []

    documents = []

    print("\nSaving documents locally...")

    for path in file_paths:

        stored_path = upload_fn(path) if upload_fn else None

        if stored_path is None:
            print(f"ERROR: Could not save {os.path.basename(path)}")
            continue

        documents.append({
            "file_name": os.path.basename(path),
            "file_path": stored_path
        })

        print(f"Saved locally: {os.path.basename(path)}")

    print(f"\n{len(documents)} document(s) saved locally. They upload to the cloud when you run Manual Sync.")

    return documents


def display_section(title: str):
    """Print a formatted section heading."""

    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def show_client_list(records: list) -> None:
    """Print a compact table of all saved clients."""
    if not records:
        print("\n  No clients saved yet.\n")
        return
    W = 68
    print("\n" + "─" * W)
    print(f"  {'#':<4}{'Reference':<18}{'Name':<28}Industry")
    print("─" * W)
    for i, r in enumerate(records, start=1):
        ref      = r.get("client_ref", "—")
        name     = r.get("name") or r.get("client_profile", {}).get("full_name", "—")
        industry = r.get("career", {}).get("industry") or r.get("client_profile", {}).get("industry", "—")
        print(f"  {i:<4}{ref:<18}{name:<28}{industry}")
    print("─" * W + "\n")


FIELD_NAMES = {"expected_aum": "Expected AUM", "pep_status": "PEP Status",
               "current_declared_value": "Current Value (SGD)", "income_produced": "Yearly Income (SGD)"}


def _field_name(key: str) -> str:
    return FIELD_NAMES.get(key, key.replace("_", " ").title())


def _field_value(key: str, value) -> str:
    """A saved value as a person would write it: 6,000,000 not 6000000.0, Singapore not ['Singapore']."""
    if key == "end_year" and value in (None, ""):
        return "present (current job)"
    if value is None or value == "" or value == []:
        return "-"
    if key == "asset_composition" and isinstance(value, dict):
        return ", ".join(f"{k.replace('_', ' ').title()} {float(v or 0) * 100:.0f}%" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not key.endswith(("year", "_years", "age")) and key not in ("year_acquired", "year_sold"):
        return f"{value:,}"
    return str(value)


def display_client_record(record: dict):
    """Display all information collected for the client."""

    display_section("CLIENT INFORMATION REVIEW")

    print(f"Client Reference: {record['client_ref']}")

    # -------------------------------------------------
    # Client Profile
    # -------------------------------------------------

    display_section("CLIENT PROFILE")
    print(f"Name:        {record.get('name', '—')}")
    print(f"Nationality: {record.get('nationality', '—')}")

    # -------------------------------------------------
    # Career History
    # -------------------------------------------------

    display_section("CAREER HISTORY")

    for index, career in enumerate(record.get("career_history", []), start=1):
        print(f"\n--- Job {index} ---")
        for key, value in career.items():
            print(f"{_field_name(key)}: {_field_value(key, value)}")

    # -------------------------------------------------
    # Investments
    # -------------------------------------------------

    investments = record.get("investments", [])
    if investments:
        display_section("INVESTMENTS")
        for index, inv in enumerate(investments, start=1):
            print(f"\n--- Investment {index} ---")
            for key, value in inv.items():
                if key == "asset_description" and value == inv.get("asset_type"):
                    continue   # filled from documents: same as the asset type
                print(f"{_field_name(key)}: {_field_value(key, value)}")

    # -------------------------------------------------
    # Claims
    # -------------------------------------------------

    claims = record.get("claims", {})
    if claims:
        display_section("CLAIMS")
        for key, value in claims.items():
            print(f"{_field_name(key)}: {_field_value(key, value)}")

    # -------------------------------------------------
    # Source of Wealth Declaration
    # -------------------------------------------------

    sow = record.get("sow_declaration")
    if sow:
        display_section("SOURCE OF WEALTH DECLARATION")
        print(sow)

    # -------------------------------------------------
    # Supporting Documents
    # -------------------------------------------------

    display_section("SUPPORTING DOCUMENTS")

    documents = record.get("supporting_documents", [])

    if not documents:
        print("No supporting documents provided.")
    else:
        for index, document in enumerate(documents, start=1):

            print(f"{index}. {document.get('file_name', 'Unknown file')}")
            if document.get("file_id"):
                print(f"   File ID: {document['file_id']}")

            elif document.get("file_path"):
                print(
                    f"   File Path: {document['file_path']}"
                )

            else:
                print("   No document reference found.")


def create_new_client(save_fn=None, upload_fn=None):
    """Collect, review and optionally save a new client."""

    print("\n")
    print("=" * 60)
    print("                    NEW CLIENT")
    print("=" * 60)

    # Generate client reference
    client_ref = generate_client_id()

    print(f"\nGenerated Client Reference: {client_ref}")

    # Collect information
    profile_data         = get_client_profile()
    career_history       = get_career_history()
    investments          = get_investments()
    claims               = get_claims()
    sow_declaration      = get_sow_declaration()
    supporting_documents = get_supporting_documents(upload_fn)

    # Create complete record
    client_record = {
        "client_ref":          client_ref,
        "name":                profile_data["name"],
        "nationality":         profile_data["nationality"],
        "current_date":        datetime.date.today().isoformat(),
        "career":              profile_data["career"],
        "career_history":      career_history,
        "investments":         investments,
        "claims":              claims,
        "sow_declaration":     sow_declaration,
        "reviews":             [],
        "supporting_documents": supporting_documents,
    }

    # Show everything before saving
    display_client_record(client_record)

    # Ask whether to save
    if ask_yes_no("\nWould you like to save this client locally?"):

        print("\nSaving client locally...")

        success = save_fn(client_record) if save_fn else False

        if success:
            print("\nClient successfully saved!")
            print(f"Client Reference: {client_ref}")

            next_step = ask_choice(
                "\nWhat would you like to do next?",
                ["Run the combined assessment now (crime scan, forecast and benchmark -> one verdict)",
                 "Return to the main menu"],
            )
            if next_step == 1:
                print("\nReturning to the main menu. You can run the assessment later with option 4.")
                client_record = None   # None tells main.py not to start the assessment

        else:
            print("\nERROR: The client could not be saved to the local file.")
            client_record = None
            input("\nPress Enter to return to the main menu...")

    else:

        print("\nClient was NOT saved.")
        print("The information has been discarded.")
        client_record = None
        input("\nPress Enter to return to the main menu...")

    return client_record


# ---------------------------------------------------------------------------
# New client from documents (menu option 2). main.py does the reading, the AI
# call and the saving; these functions only ask and show.
# ---------------------------------------------------------------------------

PEP_OPTIONS = ["None", "Domestic PEP", "Foreign PEP", "International organisation PEP",
               "Family member / close associate of a PEP"]
SOURCE_TAGS = {"documents": "[DOC]   ", "edited": "[EDITED]", "entered": "[YOU]   ", "blank": "[ -- ]  "}


def ask_document_folder() -> str | None:
    """Ask which folder holds the client's documents. Returns the folder path, or None to cancel."""
    print("\n" + "=" * 60)
    print("              NEW CLIENT FROM DOCUMENTS")
    print("=" * 60)
    print("Put the client's documents in one folder (CV, payslips, employment letter, tax notices,")
    print("bank statements, property papers, KYC form). PDF, Word, Excel, text and images are read.")
    if tk is not None and ask_yes_no("\nChoose the folder in a file explorer window?"):
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="Select the folder with the client's documents")
        root.destroy()
        if folder:
            print(f"Folder: {folder}")
            return folder
        print("No folder was chosen.")
    folder = input("\nFolder path (or press Enter to go back to the menu): ").strip()
    folder = folder.strip('"').strip("'")          # paths dragged into the terminal come quoted
    return folder or None


def show_documents_found(paths: list[str]) -> None:
    print(f"\nFound {len(paths)} document(s):")
    for path in paths:
        print(f"  - {os.path.basename(path)}")


def ask_missing_field(field: dict):
    """Ask for one detail the documents didn't give. field comes from logic_manager.missing_client_fields."""
    label, kind = field["label"], field["kind"]
    if kind == "words":
        return ask_words(label)
    if kind == "label":
        return ask_words(label, LABEL)
    if kind == "count":
        return ask_count(label)
    if kind == "text":
        return ask_text(label)
    if kind == "age":
        return ask_int(label, minimum=18, maximum=100)
    if kind == "year":
        return ask_year(label)
    if kind == "money_positive":
        return ask_money(label, allow_zero=False)
    if kind == "money":
        return ask_money(label)
    if kind == "payment":
        return PAYMENT_METHODS[ask_choice(label, PAYMENT_METHODS)]
    if kind == "pep":
        return PEP_OPTIONS[ask_choice(label, PEP_OPTIONS)]
    if kind == "countries":
        return ask_countries(label)
    if kind == "composition":
        print(f"\n{label}. Enter the percentage of total wealth for each category.")
        return ask_composition()
    if kind == "sow":
        print("\nThe documents don't contain a usable source of wealth declaration "
              "(it must be at least 50 characters).")
        return ask_text("Describe how the client accumulated their wealth", min_length=50)
    return ask_text(label)


def show_missing_intro(count: int) -> None:
    if count:
        print(f"\n{count} required detail(s) weren't in the documents. Please fill them in:")
    else:
        print("\nEvery required detail was found in the documents.")


def _summary_value(row: dict) -> str:
    value, kind = row["value"], row["kind"]
    if value is None or value == "" or value == []:
        return "(blank)"
    if kind == "composition" and isinstance(value, dict):
        return ", ".join(f"{key.replace('_', ' ')} {float(value.get(key) or 0) * 100:.0f}%"
                         for key in ("property", "listed_equities", "private_business", "cash"))
    if kind in ("money", "money_positive") and isinstance(value, (int, float)):
        return f"{value:,.0f}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    text = str(value)
    return text if len(text) <= 90 else textwrap.shorten(text, 90, placeholder=" ...")


def show_document_summary(rows: list[dict], files: list[str], notes: list[str], checks: list[str]) -> None:
    """The check-before-saving summary: every field, its value, and where it came from."""
    print("\n" + "=" * 72)
    print("  SUMMARY - please check every value against the documents")
    print("=" * 72)
    print("  [DOC] read from the documents   [YOU] typed by you   [EDITED] changed by you   [ -- ] blank")
    section = group = None
    for row in rows:
        if row["section"] != section:
            section, group = row["section"], None
            print(f"\n  {section.upper()}")
        if row["group"] and row["group"] != group:
            group = row["group"]
            print(f"   {group}")
        indent = "    " if row["group"] else ""
        print(f"  {SOURCE_TAGS[row['source']]} {indent}{row['label']}: {_summary_value(row)}")
    sow = next((row["value"] for row in rows if row["kind"] == "sow" and row["value"]), None)
    if sow and len(str(sow)) > 90:
        print("\n  Full source of wealth declaration:")
        for line in textwrap.wrap(str(sow), 68):
            print(f"    {line}")
    counts = {tag: sum(1 for r in rows if r["source"] == tag) for tag in SOURCE_TAGS}
    print(f"\n  {counts['documents']} from documents, {counts['entered']} typed, "
          f"{counts['edited']} changed, {counts['blank']} blank (optional).")
    print(f"  Documents read: {', '.join(os.path.basename(f) for f in files)}")
    if notes:
        print("\n  NOTES FROM READING THE DOCUMENTS")
        for note in notes:
            print(f"   - {note}")
    if checks:
        print("\n  PLEASE DOUBLE-CHECK")
        for check in checks:
            print(f"   ! {check}")
    print("=" * 72)


def ask_document_record_action() -> str:
    """After the summary: 'save', 'edit' or 'discard'."""
    options = ["Save this client", "Edit something first", "Discard (nothing is saved)"]
    return ["save", "edit", "discard"][ask_choice("\nIs everything correct?", options)]


def ask_after_save(record: dict) -> bool:
    """After a client is saved: view the saved details, run the assessment, or go back.
    Returns True if the assessment should run now."""
    options = ["View the saved client details",
               "Run the combined assessment now (documents, crime scan, forecast and benchmark -> one verdict)",
               "Return to the main menu (you can assess later with option 4)"]
    while True:
        choice = ask_choice("\nWhat would you like to do next?", options)
        if choice == 0:
            display_client_record(record)
            continue
        return choice == 1


def edit_client_profile(record):
    """Edit name, nationality and current career info."""
    print("\n=== Edit Client Profile ===")
    print("Press Enter to keep the existing value.")
    this_year = datetime.date.today().year

    record["name"]        = keep_words("Full name", record.get("name", ""))
    record["nationality"] = keep_words("Nationality", record.get("nationality", ""))

    career = record.setdefault("career", {})
    for key, label in [("occupation", "Current job title"), ("seniority", "Seniority"),
                       ("industry", "Industry"), ("employer_name", "Employer name")]:
        career[key] = keep_words(label, career.get(key, ""), LABEL)
    career["employer_size"]        = keep_count("Employer size (number of employees)", career.get("employer_size", ""))
    career["country_of_residence"] = keep_words("Country of residence", career.get("country_of_residence", ""))
    career["age"] = keep_int("Age", career.get("age", ""), 18, 100)
    career["career_start_year"] = keep_int("Year first career started",
                                           career.get("career_start_year", ""), 1940, this_year)

    print("\nClient profile updated.")


def add_career_record(record):
    """Add a job to career_history."""
    print("\n=== Add Career Record ===")
    record.setdefault("career_history", []).append(_ask_job())
    print("\nCareer record added.")


def edit_career_record(record):
    """Edit a job in career_history."""
    careers = record.get("career_history", [])
    if not careers:
        print("\nNo career records found.")
        return
    print("\n=== Career History ===")
    for i, c in enumerate(careers, start=1):
        print(f"{i}. {c.get('occupation','?')} - {c.get('employer_name','?')} "
              f"({c.get('start_year','?')} - {c.get('end_year','present')})")
    choice = input("\nSelect career record to edit: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(careers)):
        print("Invalid selection.")
        return
    career = careers[int(choice) - 1]
    print("\nPress Enter to keep the existing value.")
    this_year = datetime.date.today().year
    career["start_year"] = keep_int("Start year", career.get("start_year", ""), 1940, this_year)
    start = _year(career["start_year"]) or 1940
    while True:
        v = input(f"End year [{career.get('end_year') or 'present'}] (type 'present' for current job): ").strip()
        if not v:
            break
        if v.lower() in ("present", "now"):
            career["end_year"] = None
            break
        if v.isdigit() and start <= int(v) <= this_year:
            career["end_year"] = int(v)
            break
        show_error(f"Enter a year between {start} and {this_year}, 'present', or press Enter to keep.")
    for key, label in [("occupation", "Job title"), ("seniority", "Seniority"),
                       ("industry", "Industry"), ("employer_name", "Employer name")]:
        career[key] = keep_words(label, career.get(key, ""), LABEL)
    career["employer_size"] = keep_count("Employer size (number of employees)", career.get("employer_size", ""))
    career["country"]       = keep_words("Country", career.get("country", ""))
    print("\nCareer record updated.")


def add_asset_record(record):
    """Add an entry to investments."""
    print("\n=== Add Investment / Asset ===")
    record.setdefault("investments", []).append(_ask_asset())
    print("\nAsset added.")


def edit_asset_record(record):
    """Edit an entry in investments."""
    assets = record.get("investments", [])
    if not assets:
        print("\nNo investment records found.")
        return
    print("\n=== Investments ===")
    for i, a in enumerate(assets, start=1):
        print(f"{i}. {a.get('asset_type') or '?'}")
    choice = input("\nSelect asset to edit: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(assets)):
        print("Invalid selection.")
        return
    asset = assets[int(choice) - 1]
    print("\nPress Enter to keep the existing value.")
    this_year = datetime.date.today().year
    asset["asset_type"]        = keep_words("Asset (e.g. Property - Condo)", asset.get("asset_type", ""), LABEL)
    asset["country"]           = keep_words("Country", asset.get("country", ""))
    asset["year_acquired"] = keep_int("Year acquired", asset.get("year_acquired", ""), 1940, this_year)
    asset["price_paid"]    = keep_money("Price paid", asset.get("price_paid", ""))
    while True:
        v = input(f"Payment method {PAYMENT_METHODS} [{asset.get('payment_method', '')}]: ").strip().lower()
        if not v:
            break
        if v in PAYMENT_METHODS:
            asset["payment_method"] = v
            break
        show_error(f"Please type one of: {', '.join(PAYMENT_METHODS)}.")
    bought = _year(asset.get("year_acquired")) or 1940
    while True:
        v = input(f"Year sold [{asset.get('year_sold') or 'not sold'}] (type 'none' if still owned): ").strip().lower()
        if not v:
            break
        if v == "none":
            asset["year_sold"] = None
            asset["sale_price"] = None
            break
        if v.isdigit() and bought <= int(v) <= this_year:
            asset["year_sold"] = int(v)
            asset["sale_price"] = keep_money("Sale price", asset.get("sale_price") or 0)
            break
        show_error(f"Enter a year between {bought} and {this_year}, 'none', or press Enter to keep.")
    asset["current_declared_value"] = keep_money("Current value", asset.get("current_declared_value", ""))
    asset["income_produced"]        = keep_money("Yearly income", asset.get("income_produced", ""))
    while True:
        v = input(f"Currency [{asset.get('currency', '')}]: ").strip().upper()
        if not v:
            break
        if len(v) == 3 and v.isalpha():
            asset["currency"] = v
            break
        show_error("Please enter a 3-letter currency code like SGD.")
    print("\nAsset updated.")


def edit_asset_composition(record):
    """Edit asset composition inside claims."""
    print("\n=== Edit Asset Composition ===")
    composition = record.setdefault("claims", {}).setdefault("asset_composition", {})
    print("Press Enter to keep a value. Percentages must add up to 100%.")
    record["claims"]["asset_composition"] = ask_composition(current=composition)
    print("\nAsset composition updated.")



def edit_client_declarations(record):
    """Edit claims fields."""
    print("\n=== Edit Financial Claims ===")
    claims = record.setdefault("claims", {})
    print("Press Enter to keep the existing value.")

    claims["declared_net_worth"] = keep_money("Declared net worth (SGD)", claims.get("declared_net_worth", ""))
    claims["expected_aum"]       = keep_money("Expected AUM (SGD)", claims.get("expected_aum", ""))

    pep_options = ["None", "Domestic PEP", "Foreign PEP", "International organisation PEP",
                   "Family member / close associate of a PEP"]
    print(f"PEP status [{claims.get('pep_status', '')}]")
    if ask_yes_no("Change PEP status?"):
        claims["pep_status"] = pep_options[ask_choice("PEP status", pep_options)]

    v = input(f"Wealth countries [{', '.join(claims.get('wealth_countries', []))}]: ").strip()
    if v:
        items = [c.strip() for c in v.split(",") if c.strip()]
        if items and all(_only(c, LETTERS) for c in items):
            claims["wealth_countries"] = items
        else:
            show_error("Not changed. Countries must be letters only, separated by commas.")

    print("\nClaims updated.")


def edit_sow_declaration(record):
    """Edit the Source of Wealth declaration string."""
    print("\n=== Edit Source of Wealth Declaration ===")
    current = record.get("sow_declaration", "")
    print(f"Current: {current}")
    v = keep_text("New declaration text (min 50 characters, Enter to keep)", "", min_length=50)
    if v: record["sow_declaration"] = v
    print("\nDeclaration updated.")


def add_supporting_documents(record, upload_fn=None):
    """Add supporting documents to an existing client."""

    new_documents = get_supporting_documents(upload_fn)

    if not new_documents:
        return

    if "supporting_documents" not in record:
        record["supporting_documents"] = []

    record["supporting_documents"].extend(new_documents)

    print(
        f"\n{len(new_documents)} document(s) added."
    )

def edit_existing_client(record, upload_fn=None):
    """Allow the user to edit or add information to an existing client."""

    while True:

        print("\n========================================")
        print("       EDIT / ADD CLIENT INFORMATION")
        print("========================================")

        print("\n1. Client Profile")
        print("2. Career History")
        print("3. Asset History")
        print("4. Asset Composition")
        print("5. Client Declarations")
        print("6. Source of Wealth Declaration")
        print("7. Supporting Documents")
        print("8. Finish Editing")

        choice = input(
            "\nWhat would you like to edit or add? "
        ).strip()

        if choice == "1":

            edit_client_profile(record)

        elif choice == "2":

            print("\n1. Edit existing career")
            print("2. Add new career")

            career_choice = input(
                "\nChoose an option: "
            ).strip()

            if career_choice == "1":
                edit_career_record(record)

            elif career_choice == "2":
                add_career_record(record)

            else:
                print("Invalid choice.")

        elif choice == "3":

            print("\n1. Edit existing asset")
            print("2. Add new asset")

            asset_choice = input(
                "\nChoose an option: "
            ).strip()

            if asset_choice == "1":
                edit_asset_record(record)

            elif asset_choice == "2":
                add_asset_record(record)

            else:
                print("Invalid choice.")

        elif choice == "4":

            edit_asset_composition(record)

        elif choice == "5":

            edit_client_declarations(record)

        elif choice == "6":

            edit_sow_declaration(record)

        elif choice == "7":

            add_supporting_documents(record, upload_fn)

        elif choice == "8":

            print("\nFinished editing.")
            break

        else:

            print("\nInvalid choice.")

        if not ask_yes_no("Make another change?"):
            break

def find_existing_client(load_fn=None, save_fn=None, download_fn=None, upload_fn=None):
    """Find, display, edit and manage an existing client from MongoDB."""

    print("\n=== Existing Client ===")

    client_ref = input(
        "Enter client reference (0 to go back): "
    ).strip()

    if client_ref == "0":
        return

    print("\nSearching saved clients...")

    records = load_fn() if load_fn else []

    if not records:
        print("No client records found in the database.")
        input("\nPress Enter to return to the main menu...")
        return

    client_record = None

    for record in records:
        if record.get("client_ref", "").upper() == client_ref.upper():
            client_record = record
            break

    if client_record is None:
        print(
            f"\nNo client found with reference: {client_ref}"
        )
        input("\nPress Enter to return to the main menu...")
        return

    print("\nClient found!")

    # -------------------------------------------------
    # Initial display
    # -------------------------------------------------

    try:
        display_client_record(client_record)
    except Exception as e:
        print(f"\n[ERROR] Could not display client record: {e}")
        input("\nPress Enter to return to the main menu...")
        return

    # -------------------------------------------------
    # Existing Client Options
    # -------------------------------------------------

    while True:

        print("\n========================================")
        print("       CLIENT RECORD OPTIONS")
        print("========================================")
        print("1. Edit or Add Information")
        print("2. Download Supporting Document")
        print("3. Finish")

        choice = input(
            "\nEnter your choice (1-3): "
        ).strip()

        # ---------------------------------------------
        # EDIT / ADD
        # ---------------------------------------------

        if choice == "1":

            edit_existing_client(client_record, upload_fn)

            print("\nSaving changes locally...")

            success = save_fn(client_record) if save_fn else False

            if success:
                print(
                    "\nChanges saved successfully."
                )
            else:
                print(
                    "\nERROR: Changes could not be saved."
                )

            print("\n=== UPDATED CLIENT RECORD ===")

            display_client_record(client_record)

        # ---------------------------------------------
        # DOWNLOAD DOCUMENT
        # ---------------------------------------------

        elif choice == "2":

            documents = client_record.get(
                "supporting_documents",
                []
            )

            if not documents:
                print(
                    "\nNo supporting documents found."
                )
                continue

            while True:

                print("\n=== Supporting Documents ===")

                for index, document in enumerate(
                    documents,
                    start=1
                ):
                    print(
                        f"{index}. "
                        f"{document.get('file_name', 'Unknown file')}"
                    )

                print("0. Return")

                document_choice = input(
                    "\nSelect a document to download: "
                ).strip()

                if document_choice == "0":
                    break

                if not document_choice.isdigit():
                    print("Invalid choice.")
                    continue

                document_index = int(document_choice) - 1

                if (
                    document_index < 0
                    or document_index >= len(documents)
                ):
                    print("Invalid document selection.")
                    continue

                selected_document = documents[
                    document_index
                ]

                file_name = selected_document.get(
                    "file_name",
                    "Unknown file"
                )

                file_ref = selected_document.get("file_path")
                file_id = selected_document.get("file_id")

                if not file_ref and not file_id:
                    print(
                        "\nThis document does not have "
                        "a file reference."
                    )
                    input(
                        "\nPress Enter to continue..."
                    )
                    continue

                print(
                    f"\nSelected document: {file_name}"
                )

                if tk is None:
                    print("\nThe file picker isn't available on this computer "
                          "(tkinter is not installed).")
                    continue

                # Create a temporary Tkinter root
                root = tk.Tk()
                root.withdraw()

                output_path = filedialog.asksaveasfilename(
                    title="Save Supporting Document",
                    initialfile=file_name
                )

                root.destroy()

                if not output_path:
                    print("Download cancelled.")
                    continue

                success = False
                if download_fn:
                    # Local copy first, then the MongoDB archive
                    success = bool(file_ref and download_fn(file_ref, output_path)) \
                        or bool(file_id and download_fn(file_id, output_path))

                if success:
                    print(
                        "\nDocument downloaded successfully!"
                    )
                    print(
                        f"Saved to: {output_path}"
                    )
                else:
                    print(
                        "\nERROR: Could not download document."
                    )

                input(
                    "\nPress Enter to continue..."
                )

        # ---------------------------------------------
        # FINISH
        # ---------------------------------------------

        elif choice == "3":

            print(
                "\nFinished viewing client."
            )

            break

        else:

            print(
                "\nInvalid choice. "
                "Please enter 1, 2, or 3."
            )

    input(
        "\nPress Enter to return to the main menu..."
    )


# ============================================================================
# Forecasting: shared input helpers (bad answers are rejected and asked again)
# ============================================================================

OUTCOME_LABELS = {
    "PASSED": "PASSED",
    "MANUAL_REVIEW": "MANUAL REVIEW",
    "RISKY": "RISKY",
    "SERIOUS_RISK": "SERIOUS RISK",
}


def show(text: str = "") -> None:
    print(text)


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def money_range(values: dict) -> str:
    return f"{money(values['cautious'])} to {money(values['optimistic'])} (typical {money(values['typical'])})"


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


def ask_choice(question: str, options: list[str], ask: Ask = read_answer, allow_back: bool = False) -> int | None:
    """Show a numbered list and return the index picked. With allow_back, 0 returns None."""
    show(question)
    for number, option in enumerate(options, start=1):
        show(f"  {number}. {option}")
    if allow_back:
        show("  0. Back to the main menu")
    number = ask_int("Choose a number", ask, 0 if allow_back else 1, len(options))
    return None if number == 0 else number - 1


def ask_yes_no(question: str, ask: Ask = read_answer) -> bool:
    while True:
        answer = ask(f"{question} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        show_error("Please answer y or n.")

def ask_year(question: str, minimum: int = 1940, maximum: int | None = None,
             allow_blank: bool = False) -> int | None:
    maximum = maximum or datetime.date.today().year
    return ask_int(question, read_answer, minimum, maximum, allow_blank)


def ask_currency(ask: Ask = read_answer) -> str:
    while True:
        code = ask("Currency (3-letter code, e.g. SGD): ").strip().upper()
        if len(code) == 3 and code.isalpha():
            return code
        show_error("Please enter a 3-letter currency code like SGD or USD.")


def ask_composition(current: dict | None = None) -> dict:
    """Four percentages that must add up to 100. Enter keeps the current value when editing."""
    labels = [("property", "Property"), ("listed_equities", "Listed Equities"),
              ("private_business", "Private Business"), ("cash", "Cash")]
    while True:
        split = {}
        for key, label in labels:
            if current is None:
                split[key] = ask_money(f"{label} (%)") / 100
            else:
                shown = f"{current.get(key, 0) * 100:.0f}"
                v = input(f"{label} (%) [{shown}]: ").strip().replace(",", "")
                try:
                    split[key] = float(v) / 100 if v else current.get(key, 0.0)
                except ValueError:
                    show_error("Please enter a number, or press Enter to keep.")
                    split = None
                    break
                if split[key] < 0:
                    show_error("Percentages can't be negative.")
                    split = None
                    break
        if split is None:
            continue
        total = sum(split.values())
        if abs(total - 1.0) <= 0.005:
            return split
        show_error(f"That adds up to {total:.1%}. It must be 100%. Please re-enter.")


def keep_text(label: str, current, min_length: int = 2) -> str:
    while True:
        v = input(f"{label} [{current}]: ").strip()
        if not v:
            return current
        if len(v) >= min_length:
            return v
        show_error(f"Enter at least {min_length} characters, or press Enter to keep.")


def keep_int(label: str, current, minimum: int, maximum: int):
    while True:
        v = input(f"{label} [{current}]: ").strip()
        if not v:
            return current
        try:
            n = int(v)
        except ValueError:
            show_error("Please enter a whole number, or press Enter to keep.")
            continue
        if minimum <= n <= maximum:
            return n
        show_error(f"Please enter a value between {minimum} and {maximum}.")


def keep_money(label: str, current):
    while True:
        v = input(f"{label} [{current}]: ").strip().replace(",", "")
        if not v:
            return current
        try:
            n = float(v)
        except ValueError:
            show_error("Please enter an amount like 1500000, or press Enter to keep.")
            continue
        if n >= 0:
            return n
        show_error("Amounts can't be negative.")

LETTERS = " '.-"                    # allowed besides letters: names, nationality, countries
LABEL   = " '.-&/,()%#0123456789"     # job titles, industries, employers, asset descriptions


def _only(text: str, allowed: str) -> bool:
    """Must contain at least one letter, and nothing outside letters + the allowed characters."""
    return any(c.isalpha() for c in text) and all(c.isalpha() or c in allowed for c in text)


def _words_error(allowed: str) -> str:
    if allowed == LETTERS:
        return "Letters only please (spaces, - ' . are fine). No numbers."
    return "Please enter a proper description. It must contain letters and can't be only numbers."


def ask_words(question: str, allowed: str = LETTERS, min_length: int = 2) -> str:
    while True:
        answer = input(f"{question}: ").strip()
        if len(answer) >= min_length and _only(answer, allowed):
            return answer
        show_error(_words_error(allowed))


def keep_words(label: str, current, allowed: str = LETTERS, min_length: int = 2) -> str:
    while True:
        v = input(f"{label} [{current}]: ").strip()
        if not v:
            return current
        if len(v) >= min_length and _only(v, allowed):
            return v
        show_error(_words_error(allowed))


def ask_countries(question: str) -> list:
    while True:
        items = [c.strip() for c in ask_text(question).split(",") if c.strip()]
        if items and all(_only(c, LETTERS) for c in items):
            return items
        show_error("Enter country names separated by commas, letters only (no numbers).")

MAX_EMPLOYEES = 10_000_000


def _parse_count(text: str) -> int | None:
    """'2,500' -> 2500. Returns None for ranges, text, zero or absurdly large numbers."""
    cleaned = text.strip().replace(",", "")
    if cleaned.isdigit() and 1 <= int(cleaned) <= MAX_EMPLOYEES:
        return int(cleaned)
    return None


def ask_count(question: str, ask: Ask = read_answer) -> int:
    while True:
        number = _parse_count(ask(f"{question}: "))
        if number is not None:
            return number
        show_error("Please enter the number of employees as a single whole number, e.g. 2500 "
                   "(no ranges or text).")


def keep_count(label: str, current):
    while True:
        v = input(f"{label} [{current}]: ").strip()
        if not v:
            return current
        number = _parse_count(v)
        if number is not None:
            return number
        show_error("Please enter a single whole number, e.g. 2500, or press Enter to keep.")


def validate_case(case: object) -> list[str]:
    """Everything wrong with a case (from a file or typed in). [] means it's fine."""
    if not isinstance(case, dict):
        return ["the case must be a JSON object"]
    schema = utilities.load_schema("case_input.schema.json")
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
# Forecasting: turning a client record from the form into a forecasting case
# ============================================================================
# The New Client form saves everything as typed text, in its own layout
# (client_profile, career_timeline, asset_timeline, ...). The forecasting needs
# numbers in its own layout (career_history, investments, claims, ...).
# case_from_client_record() converts one into the other and asks the officer
# only for what the form doesn't have or what can't be read.





def _number(text: object) -> float | None:
    """Read an amount typed in the form: '1,650,000', 'S$1.65m', '2.5 million', '410k'. None if unreadable."""
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = str(text or "").strip().lower().replace(",", "").replace(" ", "")
    for prefix in ("sgd", "s$", "$"):
        cleaned = cleaned.removeprefix(prefix)
    multiplier = 1.0
    for suffix, factor in (("million", 1e6), ("mil", 1e6), ("m", 1e6), ("k", 1e3), ("%", 1.0)):
        if cleaned.endswith(suffix):
            cleaned, multiplier = cleaned[: -len(suffix)], factor
            break
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _year(text: object) -> int | None:
    value = _number(text)
    return int(value) if value is not None and 1900 <= value <= 2100 else None


def _payment_method(text: object) -> str | None:
    """Map what was typed ('Cash', 'mortgage', 'bank loan', 'sold my flat') to cash / loan / asset_sale."""
    words = str(text or "").lower()
    if any(w in words for w in ("loan", "mortgage", "financ", "borrow")):
        return "loan"
    if any(w in words for w in ("sale", "sold", "proceeds")):
        return "asset_sale"
    if "cash" in words or "savings" in words:
        return "cash"
    return None


def _number_or_ask(value: object, question: str, ask: Ask, allow_zero: bool = True) -> float:
    number = _number(value)
    if number is not None and number >= 0 and (allow_zero or number > 0):
        return number
    if str(value or "").strip():
        show_error(f"Couldn't read '{value}' as an amount.")
    return ask_money(question, ask, allow_zero=allow_zero)


def _year_or_ask(value: object, question: str, ask: Ask, minimum: int | None = None,
                 maximum: int | None = None) -> int:
    year = _year(value)
    if year is not None and (minimum is None or year >= minimum) and (maximum is None or year <= maximum):
        return year
    return ask_int(question, ask, minimum, maximum)


def _text_or_ask(value: object, question: str, ask: Ask) -> str:
    text = str(value or "").strip()
    return text if text else ask_text(question, ask)


def _jobs_from_record(record: dict, this_year: int, ask: Ask) -> list[dict]:
    """Career history entries plus the current job from career dict."""
    career = record.get("career", {})
    jobs = []
    for number, entry in enumerate(record.get("career_history", []), start=1):
        label = f"Job {number} ({entry.get('occupation') or 'no title'})"
        start = _year_or_ask(entry.get("start_year"), f"{label}: start year", ask, 1900, this_year)
        end_text = str(entry.get("end_year") or "").strip().lower()
        end = None if end_text in ("", "present", "now", "current", "-", "none") else \
            _year_or_ask(entry.get("end_year"), f"{label}: end year", ask, start, this_year)
        jobs.append({
            "start_year":    start, "end_year": end,
            "occupation":    _text_or_ask(entry.get("occupation"),    f"{label}: job title", ask),
            "seniority":     _text_or_ask(entry.get("seniority"),     f"{label}: seniority", ask),
            "industry":      _text_or_ask(entry.get("industry"),      f"{label}: industry", ask),
            "employer_name": _text_or_ask(entry.get("employer_name"), f"{label}: employer", ask),
            "employer_size": _text_or_ask(entry.get("employer_size"), f"{label}: employer size", ask),
            "country":       _text_or_ask(entry.get("country"),       f"{label}: country", ask),
        })
    jobs.sort(key=lambda job: job["start_year"])

    # Add current job from career dict if not already present as an open-ended entry
    if not jobs or jobs[-1]["end_year"] is not None:
        earliest = jobs[-1]["end_year"] if jobs else 1900
        show("\nCurrent job (from client profile)")
        jobs.append({
            "start_year": ask_int(
                f"  Year started as {career.get('occupation') or 'current role'} "
                f"at {career.get('employer_name') or 'current employer'}",
                ask, earliest, this_year),
            "end_year":      None,
            "occupation":    _text_or_ask(career.get("occupation"),           "  Current job title", ask),
            "seniority":     _text_or_ask(career.get("seniority"),            "  Current seniority", ask),
            "industry":      _text_or_ask(career.get("industry"),             "  Current industry", ask),
            "employer_name": _text_or_ask(career.get("employer_name"),        "  Current employer", ask),
            "employer_size": _text_or_ask(career.get("employer_size"),        "  Current employer size", ask),
            "country":       _text_or_ask(career.get("country_of_residence"), "  Country of current job", ask),
        })
    return jobs


def _first_filled(*values: object) -> object:
    """The first value that isn't None or blank text, else None."""
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def _assets_from_record(record: dict, first_year: int, this_year: int, ask: Ask) -> list[dict]:
    """Investments still held, amounts in SGD. Sold assets are excluded."""
    investments = []
    for number, entry in enumerate(record.get("investments", []), start=1):
        name = str(entry.get("asset_type") or "").strip() or f"Asset {number}"
        if str(entry.get("year_sold") or "").strip():
            show(f"  {name}: sold in {entry['year_sold']}, so it's left out of the forecast.")
            continue
        currency = str(entry.get("currency") or "SGD").strip().upper()
        in_sgd = currency in ("", "SGD", "S$")
        if not in_sgd:
            show(f"\n{name} is in {currency}. Please give its amounts in SGD.")
        # current_declared_value / income_produced, as the web form saves them; older command-line
        # records called them current_value / yearly_income (data_manager upgrades those on load).
        current_value = _first_filled(entry.get("current_declared_value"), entry.get("current_value"))
        yearly_income = _first_filled(entry.get("income_produced"), entry.get("yearly_income"))
        method = _payment_method(entry.get("payment_method"))
        if method is None:
            method = PAYMENT_METHODS[ask_choice(f"{name}: how was it paid for?", PAYMENT_METHODS, ask)]
        investments.append({
            "asset_type":             name,
            "country":                _text_or_ask(entry.get("country"), f"{name}: country", ask),
            "year_acquired":          _year_or_ask(entry.get("year_acquired"), f"{name}: year bought", ask, first_year, this_year),
            "price_paid":             _number_or_ask(entry.get("price_paid") if in_sgd else None,
                                                     f"{name}: price paid (SGD)", ask, allow_zero=False),
            "current_declared_value": _number_or_ask(current_value if in_sgd else None,
                                                     f"{name}: current value (SGD)", ask),
            "payment_method":         method,
            "income_produced":        _number_or_ask((yearly_income or "0") if in_sgd else None,
                                                     f"{name}: yearly income (SGD, 0 if none)", ask),
        })
    return investments


def _composition_from_record(record: dict, ask: Ask) -> dict:
    """Asset composition as fractions (0.60 = 60%). Asks if missing or doesn't add up."""
    typed = record.get("claims", {}).get("asset_composition", {})
    split = {key: _number(typed.get(key)) for key in ("property", "listed_equities", "private_business", "cash")}
    # Values may already be fractions (0-1) — check sum
    if all(v is not None for v in split.values()):
        total = sum(split.values())
        if 0.95 <= total <= 1.05:
            return split   # already fractions
        if 95 <= total <= 105:
            return {k: v / 100 for k, v in split.items()}  # were percentages
    show_error("The asset composition is missing or doesn't add up to 100%.")
    while True:
        show("\nHow the client says his wealth is split (percent)")
        split = {key: ask_money(f"  {label} %", ask) / 100
                 for key, label in [("property", "Property"), ("listed_equities", "Listed shares"),
                                    ("private_business", "Private business"), ("cash", "Cash")]}
        if 0.95 <= sum(split.values()) <= 1.05:
            return split
        show_error(f"That adds up to {sum(split.values()):.0%}. Please make it 100%.")


def case_from_client_record(record: dict, ask: Ask = read_answer) -> dict | None:
    """Build the forecasting case from a saved client record.

    Reads directly from the new flat structure. The officer is asked only for
    what's missing or unreadable.
    Returns None if the case fails validation.
    """
    if not record:
        return None
    career     = record.get("career", {})
    claims     = record.get("claims", {})
    show(LINE)
    show(f"Preparing {record.get('client_ref')} for the forecasting assessment")
    show(LINE)

    # The assessment is always dated today - the web app does the same (web_app.assess_client).
    current_date = datetime.date.today().isoformat()
    show(f"Assessment date: {current_date} (today)")
    this_year = int(current_date[:4])

    age  = int(_number_or_ask(career.get("age"), "Age", ask, allow_zero=False))
    jobs = _jobs_from_record(record, this_year, ask)
    first_year = jobs[0]["start_year"]
    current    = jobs[-1]

    case = {
        "client_ref":   record["client_ref"],
        "current_date": current_date,
        "name":         _text_or_ask(record.get("name"),        "Client name", ask),
        "nationality":  _text_or_ask(record.get("nationality"), "Nationality", ask),
        "career": {
            "occupation":           current["occupation"],
            "seniority":            current["seniority"],
            "industry":             current["industry"],
            "employer_name":        current["employer_name"],
            "employer_size":        current["employer_size"],
            "country_of_residence": _text_or_ask(career.get("country_of_residence"), "Country of residence", ask),
            "age":                  age,
            "career_start_year":    first_year,
        },
        "career_history": jobs,
        "investments":    _assets_from_record(record, first_year, this_year, ask),
        "claims": {
            "declared_net_worth": _number_or_ask(claims.get("declared_net_worth"),
                                                 "Declared net worth (SGD)", ask, allow_zero=False),
            "expected_aum":       _number_or_ask(claims.get("expected_aum"),
                                                 "Expected AUM (SGD)", ask, allow_zero=False),
            "asset_composition":  _composition_from_record(record, ask),
            "wealth_countries":   claims.get("wealth_countries")
                                  or [c.strip() for c in ask_text(
                                      "Countries his wealth came from (comma separated)", ask
                                  ).split(",") if c.strip()],
            "pep_status":         _text_or_ask(claims.get("pep_status"), "PEP status (e.g. None)", ask),
            # not needed by the forecast, but the benchmark uses them (logic_manager.forecasting_case keeps them too)
            "biggest_wealth_jump":     _number(claims.get("biggest_wealth_jump")),
            "wealth_jump_years":       _number(claims.get("wealth_jump_years")),
            "declared_counterparties": list(claims.get("declared_counterparties") or []),
        },
        "sow_declaration": _text_or_ask(record.get("sow_declaration"), "Source of wealth declaration", ask),
        "reviews": [],
    }
    if len(case["sow_declaration"]) < 50:
        show_error("The source of wealth declaration is too short (50 characters minimum).")
        case["sow_declaration"] = ask_text("Source of wealth declaration (one paragraph)", ask, min_length=50)

    problems = validate_case(case)
    if problems:
        show_case_problems(case["client_ref"], problems)
        return None
    return case


def show_case_problems(where: str, problems: list[str]) -> None:
    show_error(f"{where} can't be assessed yet:")
    for problem in problems:
        show(f"    - {problem}")
    show("  Fix the client record (Find / Edit Existing Client) and try again.")


def ask_client_ref(ask: Ask = read_answer) -> str | None:
    ref = ask_text("Client reference (e.g. CL-1A2B3C4D, 0 to go back)", ask)
    return None if ref == "0" else ref


# ============================================================================
# Forecasting: progress and reports
# ============================================================================

def show_ai_progress(results: dict) -> None:
    """One line per AI request: where the reply came from and whether it passed the checks."""
    for name, result in results.items():
        source = "saved reply" if result["from_cache"] else f"API, {result['attempts']} attempt(s)"
        status = "ok" if result["ok"] else f"FAILED - {result['error']}"
        show(f"  {name:<40} {source:<20} {status}")


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
        if row.get("enabled_by") and row["type_id"] != "T1":
            lines.append(f"      possible through: {row['enabled_by']}")
        lines.append(f"      wealth today: {money_range(row['wealth_now'])}; "
                     f"purchases affordable: {row['purchases_affordable']}")

    lines += ["", "Purchases (honest version):"]
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
        verdict = record.get("verdict")
        assessment = record.get("assessment") or {}
        if verdict:                                   # the combined assessment: its one verdict
            date, outcome = verdict.get("assessed_on", ""), verdict.get("verdict")
        else:
            date = assessment.get("assessed_on") or assessment.get("review_date", "")
            outcome = assessment.get("outcome")
        lines.append(f"{record['client_ref']:<16} {record['kind']:<20} {date:<12} "
                     f"{OUTCOME_LABELS.get(outcome, '?')}")
    return "\n".join(lines)


def show_onboarding(assessment: dict) -> None:
    """A forecast-only report (assessments saved before the combined verdict)."""
    W = 68
    print("\n" + "═" * W)
    print("  FORECASTING — ONBOARDING ASSESSMENT (saved before the combined verdict)")
    print("═" * W)
    show(format_onboarding(assessment))
    print("═" * W + "\n")


def show_review(assessment: dict) -> None:
    show(format_review(assessment))


def show_assessment_list(records: list[dict]) -> None:
    show(format_assessment_list(records))