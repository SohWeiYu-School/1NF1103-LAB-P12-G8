"""
io_manager.py - the input/output layer: every boundary between the system and the user.

    * Client input and editing (New Client form, Find / Edit Existing Client,
      supporting documents) - saved to MongoDB through data_manager
    * Crime scan and research output
    * Forecasting: turns a saved client record into a forecasting case (asking the
      officer only for what's missing), collects periodic reviews, and prints the
      onboarding and review reports
    * ALL print() calls in the system live in this file

The forecasting ask_* functions take an `ask` function (normally read_answer, i.e.
typing) so tests can feed in typed answers.
"""

import datetime
import json
import os
import uuid
from typing import Callable

from jsonschema import Draft202012Validator

from app import common
from app.data_manager import (
    save_case_record,
    upload_supporting_document,
    load_all_records,
    download_supporting_document
)

# The file picker needs tkinter, which isn't installed everywhere (e.g. the Docker image).
# Without it the app still runs; only choosing files to upload or download is unavailable.
try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    tk = filedialog = None

Ask = Callable[[str], str]
LINE = "-" * 72


def show_app_menu() -> str:
    """Display the unified main menu and return the user's choice (1-7)."""
    print("\n========================================")
    print("     SOURCE OF WEALTH SCREENING SYSTEM")
    print("========================================")
    print("1. New Client (enter, then crime scan, benchmark and forecasting)")
    print("2. Find / Edit Existing Client")
    print("3. Assess an Existing Client (crime scan, benchmark and forecasting)")
    print("4. Run a Periodic Review")
    print("5. List Saved Assessments")
    print("6. Manual Sync to Cloud Database")
    print("7. Quit")
    while True:
        choice = input("\nEnter your choice (1-7): ").strip()
        if choice in ("1", "2", "3", "4", "5", "6", "7"):
            return choice
        print("Invalid choice. Please enter 1 to 7.")

def show_message(text: str) -> None:
    #Print an informational message.
    print(text)

def show_error(text: str) -> None:
    #Print an error message.
    print(f"[ERROR] {text}")

def show_research_output(research_response: dict) -> None:
    """Print a short summary of the research results."""
    reports = research_response.get("reports", [])
    print(f"\nResearch: {len(reports)} report(s) found")
    for r in reports:
        rid = r.get("report_id", "?")
        org = r.get("organisation", "")
        title = r.get("title", "")
        year = r.get("year", "")
        print(f"  {rid}  {org} — {title} ({year})")
    print()

def show_ai_output(case_input: dict, typology_response: dict, declaration: dict) -> None:
    """Print a short summary of AI results for the officer."""
    client_ref = case_input.get("client_ref", "—")
    print(f"\n--- Sector Crime Scan ---")
    print(f"Client: {client_ref} | Fields sent to AI: 5 (safe fields only)")

    typologies = typology_response.get("sector_typologies", [])
    print(f"\nTypologies: {len(typologies)} found")
    for i, t in enumerate(typologies, 1):
        ref = t.get("source_reference", "")
        suffix = f" | {ref}" if ref else ""
        print(f"  {i}. {t.get('name', '—')}{suffix}")

    sources = declaration.get("sources", [])
    print(f"\nDeclaration: {len(sources)} source(s) found")

    cache_path = typology_response.get("_cache_path", "")
    if cache_path:
        print(f"Cache: {cache_path}")
    print()

def generate_client_id() -> str:
    """Generate a unique client reference ID."""

    return "CL-" + uuid.uuid4().hex[:8].upper()


def get_client_profile() -> dict:
    """Collect basic client profile information."""

    print("\n=== Client Profile ===")

    full_name = input("Full name: ").strip()
    age = input("Age: ").strip()
    nationality = input("Nationality: ").strip()
    country_of_residence = input("Country of residence: ").strip()
    latest_occupation = input("Latest occupation: ").strip()
    latest_seniority = input("Latest seniority: ").strip()
    latest_industry = input("Latest industry: ").strip()
    current_company = input("Current company name: ").strip()
    career_start_year = input("Career start year: ").strip()
    years_of_employment = input("Years of employment: ").strip()

    date_profile_created = input(
        "Date profile created (YYYY-MM-DD): "
    ).strip()

    return {
        "full_name": full_name,
        "age": age,
        "nationality": nationality,
        "country_of_residence": country_of_residence,
        "latest_occupation": latest_occupation,
        "latest_seniority": latest_seniority,
        "latest_industry": latest_industry,
        "current_company": current_company,
        "career_start_year": career_start_year,
        "years_of_employment": years_of_employment,
        "date_profile_created": date_profile_created
    }


def get_career_timeline() -> list:
    """Collect the client's previous career history."""

    career_timeline = []

    while True:

        print("\n=== Previous Career History ===")

        start_year = input("Start year: ").strip()
        end_year = input("End year: ").strip()
        job_title = input("Job title: ").strip()
        seniority = input("Seniority: ").strip()
        industry = input("Industry: ").strip()
        company_name = input("Company name: ").strip()
        company_size = input("Company size: ").strip()
        country = input("Country: ").strip()

        career_timeline.append({
            "start_year": start_year,
            "end_year": end_year,
            "job_title": job_title,
            "seniority": seniority,
            "industry": industry,
            "company_name": company_name,
            "company_size": company_size,
            "country": country
        })

        another = input(
            "\nAdd another previous job? (y/n): "
        ).strip().lower()

        if another != "y":
            break

    return career_timeline


def get_asset_timeline() -> list:
    """Collect the client's asset history."""

    asset_timeline = []

    while True:

        print("\n=== Asset Information ===")

        asset_type = input("Asset type: ").strip()
        asset_description = input("Asset description: ").strip()
        country = input("Country: ").strip()
        year_acquired = input("Year acquired: ").strip()
        price_paid = input("Price paid: ").strip()
        payment_method = input("Payment method: ").strip()

        year_sold = input(
            "Year sold (leave blank if still owned): "
        ).strip()

        sale_price = input(
            "Sale price (leave blank if still owned): "
        ).strip()

        current_value = input("Current value: ").strip()
        yearly_income = input("Yearly income: ").strip()
        currency = input("Currency: ").strip()

        asset = {
            "asset_type": asset_type,
            "asset_description": asset_description,
            "country": country,
            "year_acquired": year_acquired,
            "price_paid": price_paid,
            "payment_method": payment_method,
            "year_sold": year_sold,
            "sale_price": sale_price,
            "current_value": current_value,
            "yearly_income": yearly_income,
            "currency": currency
        }

        asset_timeline.append(asset)

        another = input(
            "\nAdd another asset? (y/n): "
        ).strip().lower()

        if another != "y":
            break

    return asset_timeline


def get_asset_composition() -> dict:
    """Collect the client's asset composition."""

    print("\n=== Asset Composition ===")
    print("Enter the percentage of total wealth for each category.")

    property_share = input(
        "Property (%): "
    ).strip()

    listed_equities_share = input(
        "Listed Equities (%): "
    ).strip()

    private_business_share = input(
        "Private Business (%): "
    ).strip()

    cash_share = input(
        "Cash (%): "
    ).strip()

    return {
        "property": property_share,
        "listed_equities": listed_equities_share,
        "private_business": private_business_share,
        "cash": cash_share
    }


def get_client_declarations() -> dict:
    """Collect the client's declarations."""

    print("\n=== Client Declarations ===")

    declared_net_worth = input(
        "Declared net worth: "
    ).strip()

    expected_aum = input(
        "Expected Assets Under Management: "
    ).strip()

    owns_property = input(
        "Owns property? (yes/no): "
    ).strip().lower()

    owns_investments = input(
        "Owns investments? (yes/no): "
    ).strip().lower()

    pep_status = input(
        "PEP status: "
    ).strip()

    wealth_countries = input(
        "Countries where wealth was generated "
        "(separate multiple countries with commas): "
    ).strip()

    date_declared = input(
        "Date declared (YYYY-MM-DD): "
    ).strip()

    return {
        "declared_net_worth": declared_net_worth,
        "expected_assets_under_management": expected_aum,
        "owns_property": owns_property,
        "owns_investments": owns_investments,
        "pep_status": pep_status,
        "countries_where_wealth_was_generated": wealth_countries,
        "date_declared": date_declared
    }


def get_sow_declaration() -> dict:
    """Collect the client's Source of Wealth declaration."""

    print("\n=== Source of Wealth Declaration ===")

    declaration_text = input(
        "Declaration text: "
    ).strip()

    written_by = input(
        "Written by: "
    ).strip()

    date_written = input(
        "Date written (YYYY-MM-DD): "
    ).strip()

    return {
        "declaration_text": declaration_text,
        "written_by": written_by,
        "date_written": date_written
    }


def get_supporting_documents() -> list:
    """
    Ask whether the client has supporting documents.

    If yes, open a Tkinter file picker and upload
    the selected documents to MongoDB GridFS.
    """

    print("\n=== Supporting Documents ===")

    answer = input(
        "Does the client have any supporting documents? (y/n): "
    ).strip().lower()

    if answer != "y":
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

    print("\nUploading documents to database...")

    for path in file_paths:

        print(f"Uploading: {os.path.basename(path)}")

        file_id = upload_supporting_document(path)

        if file_id is None:
            print(
                f"ERROR: Could not upload "
                f"{os.path.basename(path)}"
            )
            continue

        documents.append({
            "file_name": os.path.basename(path),
            "file_id": file_id
        })

        print(
            f"Uploaded successfully: "
            f"{os.path.basename(path)}"
        )

    print(
        f"\n{len(documents)} document(s) uploaded successfully."
    )

    return documents


def display_section(title: str):
    """Print a formatted section heading."""

    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def display_client_record(record: dict):
    """Display all information collected for the client."""

    display_section("CLIENT INFORMATION REVIEW")

    print(f"Client Reference: {record['client_ref']}")

    # -------------------------------------------------
    # Client Profile
    # -------------------------------------------------

    profile = record["client_profile"]

    display_section("CLIENT PROFILE")

    for key, value in profile.items():
        print(f"{key.replace('_', ' ').title()}: {value}")

    # -------------------------------------------------
    # Career Timeline
    # -------------------------------------------------

    display_section("CAREER HISTORY")

    for index, career in enumerate(
        record["career_timeline"],
        start=1
    ):

        print(f"\n--- Previous Job {index} ---")

        for key, value in career.items():
            print(
                f"{key.replace('_', ' ').title()}: {value}"
            )

    # -------------------------------------------------
    # Asset Timeline
    # -------------------------------------------------

    display_section("ASSET HISTORY")

    for index, asset in enumerate(
        record["asset_timeline"],
        start=1
    ):

        print(f"\n--- Asset {index} ---")

        for key, value in asset.items():
            print(
                f"{key.replace('_', ' ').title()}: {value}"
            )

    # -------------------------------------------------
    # Asset Composition
    # -------------------------------------------------

    display_section("ASSET COMPOSITION")

    for key, value in record["asset_composition"].items():
        print(
            f"{key.replace('_', ' ').title()}: {value}%"
        )

    # -------------------------------------------------
    # Client Declarations
    # -------------------------------------------------

    display_section("CLIENT DECLARATIONS")

    for key, value in record["client_declarations"].items():
        print(
            f"{key.replace('_', ' ').title()}: {value}"
        )

    # -------------------------------------------------
    # Source of Wealth Declaration
    # -------------------------------------------------

    display_section("SOURCE OF WEALTH DECLARATION")

    for key, value in record["sow_declaration"].items():
        print(
            f"{key.replace('_', ' ').title()}: {value}"
        )

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


def create_new_client():
    """Collect, review and optionally save a new client."""

    print("\n")
    print("=" * 60)
    print("                    NEW CLIENT")
    print("=" * 60)

    # Generate client reference
    client_ref = generate_client_id()

    print(f"\nGenerated Client Reference: {client_ref}")

    # Collect information
    profile = get_client_profile()

    career_timeline = get_career_timeline()

    asset_timeline = get_asset_timeline()

    asset_composition = get_asset_composition()

    client_declarations = get_client_declarations()

    sow_declaration = get_sow_declaration()

    supporting_documents = get_supporting_documents()

    # Create complete record
    client_record = {
        "client_ref": client_ref,
        "client_profile": profile,
        "career_timeline": career_timeline,
        "asset_timeline": asset_timeline,
        "asset_composition": asset_composition,
        "client_declarations": client_declarations,
        "sow_declaration": sow_declaration,
        "supporting_documents": supporting_documents
    }

    # Show everything before saving
    display_client_record(client_record)

    # Ask whether to save
    while True:

        save_choice = input(
            "\nWould you like to save this client "
            "to the database? (y/n): "
        ).strip().lower()

        if save_choice in ("y", "n"):
            break

        print("Please enter y or n.")

    if save_choice == "y":

        print("\nSaving client to database...")

        success = save_case_record(client_record)

        if success:
            print("\nClient successfully saved!")
            print(f"Client Reference: {client_ref}")

        else:
            print(
                "\nERROR: The client could not be saved "
                "to the database."
            )

    else:

        print("\nClient was NOT saved to the database.")
        print("The information has been discarded.")
        client_record = None

    input("\nPress Enter to return to the main menu...")
    return client_record

def edit_client_profile(record):
    """Edit the client's basic profile information."""

    print("\n=== Edit Client Profile ===")
    profile = record["client_profile"]

    print("\nPress Enter to keep the existing value.")

    value = input(
        f"Full name [{profile['full_name']}]: "
    ).strip()
    if value:
        profile["full_name"] = value

    value = input(
        f"Age [{profile['age']}]: "
    ).strip()
    if value:
        profile["age"] = value

    value = input(
        f"Nationality [{profile['nationality']}]: "
    ).strip()
    if value:
        profile["nationality"] = value

    value = input(
        f"Country of residence [{profile['country_of_residence']}]: "
    ).strip()
    if value:
        profile["country_of_residence"] = value

    value = input(
        f"Latest occupation [{profile['latest_occupation']}]: "
    ).strip()
    if value:
        profile["latest_occupation"] = value

    value = input(
        f"Latest seniority [{profile['latest_seniority']}]: "
    ).strip()
    if value:
        profile["latest_seniority"] = value

    value = input(
        f"Latest industry [{profile['latest_industry']}]: "
    ).strip()
    if value:
        profile["latest_industry"] = value

    value = input(
        f"Current company [{profile['current_company']}]: "
    ).strip()
    if value:
        profile["current_company"] = value

    value = input(
        f"Career start year [{profile['career_start_year']}]: "
    ).strip()
    if value:
        profile["career_start_year"] = value

    value = input(
        f"Years of employment [{profile['years_of_employment']}]: "
    ).strip()
    if value:
        profile["years_of_employment"] = value

    value = input(
        f"Date profile created [{profile['date_profile_created']}]: "
    ).strip()
    if value:
        profile["date_profile_created"] = value

    print("\nClient profile updated.")


def add_career_record(record):
    """Add a new career record."""

    print("\n=== Add Career Record ===")

    start_year = input("Start year: ").strip()
    end_year = input("End year: ").strip()
    job_title = input("Job title: ").strip()
    seniority = input("Seniority: ").strip()
    industry = input("Industry: ").strip()
    company_name = input("Company name: ").strip()
    company_size = input("Company size: ").strip()
    country = input("Country: ").strip()

    record["career_timeline"].append({
        "start_year": start_year,
        "end_year": end_year,
        "job_title": job_title,
        "seniority": seniority,
        "industry": industry,
        "company_name": company_name,
        "company_size": company_size,
        "country": country
    })

    print("\nCareer record added.")


def edit_career_record(record):
    """Edit an existing career record."""

    careers = record["career_timeline"]

    if not careers:
        print("\nNo career records found.")
        return

    print("\n=== Career History ===")

    for index, career in enumerate(careers, start=1):
        print(
            f"{index}. "
            f"{career['job_title']} - "
            f"{career['company_name']} "
            f"({career['start_year']} - {career['end_year']})"
        )

    choice = input(
        "\nSelect career record to edit: "
    ).strip()

    if not choice.isdigit():
        print("Invalid selection.")
        return

    index = int(choice) - 1

    if index < 0 or index >= len(careers):
        print("Invalid selection.")
        return

    career = careers[index]

    print("\nPress Enter to keep the existing value.")

    fields = [
        ("start_year", "Start year"),
        ("end_year", "End year"),
        ("job_title", "Job title"),
        ("seniority", "Seniority"),
        ("industry", "Industry"),
        ("company_name", "Company name"),
        ("company_size", "Company size"),
        ("country", "Country")
    ]

    for key, label in fields:
        value = input(
            f"{label} [{career[key]}]: "
        ).strip()

        if value:
            career[key] = value

    print("\nCareer record updated.")


def add_asset_record(record):
    """Add a new asset record."""

    print("\n=== Add Asset ===")

    asset = {
        "asset_type": input("Asset type: ").strip(),
        "asset_description": input("Asset description: ").strip(),
        "country": input("Country: ").strip(),
        "year_acquired": input("Year acquired: ").strip(),
        "price_paid": input("Price paid: ").strip(),
        "payment_method": input("Payment method: ").strip(),
        "year_sold": input(
            "Year sold (leave blank if still owned): "
        ).strip(),
        "sale_price": input(
            "Sale price (leave blank if still owned): "
        ).strip(),
        "current_value": input("Current value: ").strip(),
        "yearly_income": input("Yearly income: ").strip(),
        "currency": input("Currency: ").strip()
    }

    record["asset_timeline"].append(asset)

    print("\nAsset added.")


def edit_asset_record(record):
    """Edit an existing asset."""

    assets = record["asset_timeline"]

    if not assets:
        print("\nNo asset records found.")
        return

    print("\n=== Asset History ===")

    for index, asset in enumerate(assets, start=1):
        print(
            f"{index}. "
            f"{asset['asset_type']} - "
            f"{asset['asset_description']}"
        )

    choice = input(
        "\nSelect asset to edit: "
    ).strip()

    if not choice.isdigit():
        print("Invalid selection.")
        return

    index = int(choice) - 1

    if index < 0 or index >= len(assets):
        print("Invalid selection.")
        return

    asset = assets[index]

    print("\nPress Enter to keep the existing value.")

    fields = [
        ("asset_type", "Asset type"),
        ("asset_description", "Asset description"),
        ("country", "Country"),
        ("year_acquired", "Year acquired"),
        ("price_paid", "Price paid"),
        ("payment_method", "Payment method"),
        ("year_sold", "Year sold"),
        ("sale_price", "Sale price"),
        ("current_value", "Current value"),
        ("yearly_income", "Yearly income"),
        ("currency", "Currency")
    ]

    for key, label in fields:
        value = input(
            f"{label} [{asset[key]}]: "
        ).strip()

        if value:
            asset[key] = value

    print("\nAsset updated.")


def edit_asset_composition(record):
    """Edit asset composition."""

    print("\n=== Edit Asset Composition ===")

    composition = record["asset_composition"]

    print("\nPress Enter to keep the existing value.")

    value = input(
        f"Property (%) [{composition['property']}]: "
    ).strip()
    if value:
        composition["property"] = value

    value = input(
        f"Listed Equities (%) [{composition['listed_equities']}]: "
    ).strip()
    if value:
        composition["listed_equities"] = value

    value = input(
        f"Private Business (%) [{composition['private_business']}]: "
    ).strip()
    if value:
        composition["private_business"] = value

    value = input(
        f"Cash (%) [{composition['cash']}]: "
    ).strip()
    if value:
        composition["cash"] = value

    print("\nAsset composition updated.")


def edit_client_declarations(record):
    """Edit client declarations."""

    print("\n=== Edit Client Declarations ===")

    declarations = record["client_declarations"]

    print("\nPress Enter to keep the existing value.")

    value = input(
        f"Declared net worth [{declarations['declared_net_worth']}]: "
    ).strip()
    if value:
        declarations["declared_net_worth"] = value

    value = input(
        f"Expected Assets Under Management "
        f"[{declarations['expected_assets_under_management']}]: "
    ).strip()
    if value:
        declarations["expected_assets_under_management"] = value

    value = input(
        f"Owns property? [{declarations['owns_property']}]: "
    ).strip().lower()
    if value:
        declarations["owns_property"] = value

    value = input(
        f"Owns investments? [{declarations['owns_investments']}]: "
    ).strip().lower()
    if value:
        declarations["owns_investments"] = value

    value = input(
        f"PEP status [{declarations['pep_status']}]: "
    ).strip()
    if value:
        declarations["pep_status"] = value

    value = input(
        "Countries where wealth was generated "
        f"[{declarations['countries_where_wealth_was_generated']}]: "
    ).strip()
    if value:
        declarations["countries_where_wealth_was_generated"] = value

    value = input(
        f"Date declared [{declarations['date_declared']}]: "
    ).strip()
    if value:
        declarations["date_declared"] = value

    print("\nClient declarations updated.")


def edit_sow_declaration(record):
    """Edit Source of Wealth declaration."""

    print("\n=== Edit Source of Wealth Declaration ===")

    sow = record["sow_declaration"]

    print("\nPress Enter to keep the existing value.")

    value = input(
        f"Declaration text [{sow['declaration_text']}]: "
    ).strip()
    if value:
        sow["declaration_text"] = value

    value = input(
        f"Written by [{sow['written_by']}]: "
    ).strip()
    if value:
        sow["written_by"] = value

    value = input(
        f"Date written [{sow['date_written']}]: "
    ).strip()
    if value:
        sow["date_written"] = value

    print("\nSource of Wealth declaration updated.")


def add_supporting_documents(record):
    """Add supporting documents to an existing client."""

    new_documents = get_supporting_documents()

    if not new_documents:
        return

    if "supporting_documents" not in record:
        record["supporting_documents"] = []

    record["supporting_documents"].extend(new_documents)

    print(
        f"\n{len(new_documents)} document(s) added."
    )

def edit_existing_client(record):
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

            add_supporting_documents(record)

        elif choice == "8":

            print("\nFinished editing.")
            break

        else:

            print("\nInvalid choice.")

        print("\n----------------------------------------")
        print("Would you like to make another change?")
        print("----------------------------------------")

        again = input(
            "Enter y to continue or n to finish: "
        ).strip().lower()

        if again != "y":
            break

def find_existing_client():
    """Find, display, edit and manage an existing client from MongoDB."""

    print("\n=== Existing Client ===")

    client_ref = input(
        "Enter client reference: "
    ).strip()

    print("\nSearching database...")

    records = load_all_records()

    if not records:
        print("No client records found in the database.")
        input("\nPress Enter to return to the main menu...")
        return

    client_record = None

    for record in records:
        if record.get("client_ref") == client_ref:
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

    display_client_record(client_record)

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

            edit_existing_client(client_record)

            print("\nSaving changes to MongoDB...")

            success = save_case_record(client_record)

            if success:
                print(
                    "\nChanges saved successfully to MongoDB."
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

                file_id = selected_document.get(
                    "file_id"
                )

                if not file_id:
                    print(
                        "\nThis document does not have "
                        "a GridFS file ID."
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

                success = download_supporting_document(
                    file_id,
                    output_path
                )

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

PAYMENT_METHODS = ["cash", "loan", "asset_sale"]


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
    """Previous jobs from the career timeline, plus the current job from the profile."""
    profile = record.get("client_profile", {})
    jobs = []
    for number, entry in enumerate(record.get("career_timeline", []), start=1):
        label = f"Job {number} ({entry.get('job_title') or 'no title'})"
        start = _year_or_ask(entry.get("start_year"), f"{label}: start year", ask, 1900, this_year)
        end_text = str(entry.get("end_year") or "").strip().lower()
        end = None if end_text in ("", "present", "now", "current", "-") else \
            _year_or_ask(entry.get("end_year"), f"{label}: end year", ask, start, this_year)
        jobs.append({
            "start_year": start, "end_year": end,
            "occupation": _text_or_ask(entry.get("job_title"), f"{label}: job title", ask),
            "seniority": _text_or_ask(entry.get("seniority"), f"{label}: seniority", ask),
            "industry": _text_or_ask(entry.get("industry"), f"{label}: industry", ask),
            "employer_name": _text_or_ask(entry.get("company_name"), f"{label}: employer", ask),
            "employer_size": _text_or_ask(entry.get("company_size"), f"{label}: employer size", ask),
            "country": _text_or_ask(entry.get("country"), f"{label}: country", ask),
        })
    jobs.sort(key=lambda job: job["start_year"])

    # The current job is in the profile unless the timeline already has a job with no end year.
    if not jobs or jobs[-1]["end_year"] is not None:
        earliest = jobs[-1]["end_year"] if jobs else 1900
        show("\nCurrent job (from the client profile)")
        jobs.append({
            "start_year": ask_int(f"  Year he started as {profile.get('latest_occupation') or 'his current job'} "
                                  f"at {profile.get('current_company') or 'his current employer'}",
                                  ask, earliest, this_year),
            "end_year": None,
            "occupation": _text_or_ask(profile.get("latest_occupation"), "  Current job title", ask),
            "seniority": _text_or_ask(profile.get("latest_seniority"), "  Current seniority", ask),
            "industry": _text_or_ask(profile.get("latest_industry"), "  Current industry", ask),
            "employer_name": _text_or_ask(profile.get("current_company"), "  Current employer", ask),
            "employer_size": ask_text("  Current employer size (e.g. 1,000-5,000 employees)", ask),
            "country": _text_or_ask(profile.get("country_of_residence"), "  Country of the current job", ask),
        })
    return jobs


def _assets_from_record(record: dict, first_year: int, this_year: int, ask: Ask) -> list[dict]:
    """Assets he still holds, with amounts in SGD. Sold assets are left out (their sale
    is money he received, which the declaration should mention)."""
    investments = []
    for number, entry in enumerate(record.get("asset_timeline", []), start=1):
        name = " - ".join(p for p in (str(entry.get("asset_type") or "").strip(),
                                      str(entry.get("asset_description") or "").strip()) if p) or f"Asset {number}"
        if str(entry.get("year_sold") or "").strip():
            show(f"  {name}: sold in {entry['year_sold']}, so it's left out of the forecast.")
            continue
        currency = str(entry.get("currency") or "SGD").strip().upper()
        in_sgd = currency in ("", "SGD", "S$")
        if not in_sgd:
            show(f"\n{name} is in {currency}. Please give its amounts in SGD.")
        method = _payment_method(entry.get("payment_method"))
        if method is None:
            method = PAYMENT_METHODS[ask_choice(f"{name}: how was it paid for?", PAYMENT_METHODS, ask)]
        investments.append({
            "asset_type": name,
            "country": _text_or_ask(entry.get("country"), f"{name}: country", ask),
            "year_acquired": _year_or_ask(entry.get("year_acquired"), f"{name}: year bought", ask, first_year, this_year),
            "price_paid": _number_or_ask(entry.get("price_paid") if in_sgd else None,
                                         f"{name}: price paid (SGD)", ask, allow_zero=False),
            "current_declared_value": _number_or_ask(entry.get("current_value") if in_sgd else None,
                                                     f"{name}: what the client says it's worth now (SGD)", ask),
            "payment_method": method,
            "income_produced": _number_or_ask((entry.get("yearly_income") or "0") if in_sgd else None,
                                              f"{name}: income it produces a year (SGD, 0 if none)", ask),
        })
    return investments


def _composition_from_record(record: dict, ask: Ask) -> dict:
    """The % split of his wealth as fractions (60 -> 0.60). Asked again if it doesn't add up to 100%."""
    typed = record.get("asset_composition", {})
    split = {key: _number(typed.get(key)) for key in ("property", "listed_equities", "private_business", "cash")}
    if all(value is not None for value in split.values()) and 95 <= sum(split.values()) <= 105:
        return {key: value / 100 for key, value in split.items()}
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
    """Build the forecasting case from a client record saved by the New Client form.

    Everything the form already has is reused; the officer is asked only for what's
    missing or unreadable (e.g. the assessment date and when the current job started).
    Returns None, after showing what's wrong, if the case still isn't valid.
    """
    if not record:
        return None
    profile = record.get("client_profile", {})
    declarations = record.get("client_declarations", {})
    show(LINE)
    show(f"Preparing {record.get('client_ref')} for the forecasting assessment")
    show(LINE)

    today = datetime.date.today().isoformat()
    answer = ask(f"Assessment date (YYYY-MM-DD, blank for today {today}): ").strip()
    current_date = answer if answer else today
    try:
        datetime.date.fromisoformat(current_date)
    except ValueError:
        current_date = ask_date("Assessment date", ask)
    this_year = int(current_date[:4])

    age = int(_number_or_ask(profile.get("age"), "Age", ask, allow_zero=False))
    jobs = _jobs_from_record(record, this_year, ask)
    first_year = jobs[0]["start_year"]
    current = jobs[-1]
    countries = str(declarations.get("countries_where_wealth_was_generated") or "")

    case = {
        "client_ref": record["client_ref"],
        "current_date": current_date,
        "name": _text_or_ask(profile.get("full_name"), "Client name", ask),
        "nationality": _text_or_ask(profile.get("nationality"), "Nationality", ask),
        "career": {"occupation": current["occupation"], "seniority": current["seniority"],
                   "industry": current["industry"], "employer_name": current["employer_name"],
                   "employer_size": current["employer_size"],
                   "country_of_residence": _text_or_ask(profile.get("country_of_residence"),
                                                        "Country of residence", ask),
                   "age": age, "career_start_year": first_year},
        "career_history": jobs,
        "investments": _assets_from_record(record, first_year, this_year, ask),
        "claims": {
            "declared_net_worth": _number_or_ask(declarations.get("declared_net_worth"),
                                                 "Declared net worth (SGD)", ask, allow_zero=False),
            "expected_aum": _number_or_ask(declarations.get("expected_assets_under_management"),
                                           "Expected assets to be managed by the bank (SGD)", ask, allow_zero=False),
            "asset_composition": _composition_from_record(record, ask),
            "wealth_countries": [c.strip() for c in countries.split(",") if c.strip()]
                                or [c.strip() for c in ask_text("Countries his wealth came from (comma separated)",
                                                                ask).split(",") if c.strip()],
            "pep_status": _text_or_ask(declarations.get("pep_status"), "PEP status (e.g. None)", ask),
        },
        "sow_declaration": str(record.get("sow_declaration", {}).get("declaration_text") or "").strip(),
        "reviews": [],
    }
    if len(case["sow_declaration"]) < 50:
        show_error("The source of wealth declaration is too short for the assessment (50 characters minimum).")
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


def ask_client_ref(ask: Ask = read_answer) -> str:
    return ask_text("Client reference (e.g. CL-1A2B3C4D)", ask)


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