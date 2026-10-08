# ===== Client input & editing (IO teammate) — merged as-is, pending review =====
# TODO: review rule breaks (data_manager import inside io_manager, field names)

import uuid
import os
import tkinter as tk
from tkinter import filedialog
from app.data_manager import (
    save_case_record,
    upload_supporting_document,
    load_all_records,
    download_supporting_document
)

def show_app_menu() -> str:
    """Display the unified main menu and return the user's choice (1-3)."""
    print("\n========================================")
    print("     SOURCE OF WEALTH SCREENING SYSTEM")
    print("========================================")
    print("1. New Client")
    print("2. Find / Edit Existing Client")
    print("3. Quit")
    while True:
        choice = input("\nEnter your choice (1-3): ").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("Invalid choice. Please enter 1 to 3.")

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

def show_ai_output(
    case_input: dict,
    typology_response: dict,
    declaration: dict,
    research: dict | None = None,
) -> None:
    """Print a short summary of AI results for the officer."""
    client_ref = case_input.get("client_ref", "—")
    print(f"\n--- Sector Crime Scan ---")
    print(f"Client: {client_ref} | Fields sent to AI: 5 (safe fields only)")

    # Build lookup: report_id → "Organisation Year" for display
    report_lookup: dict[str, str] = {}
    if research:
        for r in research.get("reports", []):
            rid = r.get("report_id", "")
            org = r.get("organisation", "")
            year = r.get("year", "")
            if rid:
                report_lookup[rid] = f"{org} {year}".strip()

    typologies = typology_response.get("sector_typologies", []) if typology_response else []
    print(f"\nSector Crime Scan: {len(typologies)} pattern(s)")
    for i, t in enumerate(typologies, 1):
        source_ids = t.get("source_ids", [])
        outdated_ids = set(t.get("outdated_source_ids", []))
        resolved = []
        for rid in source_ids:
            label = report_lookup.get(rid)
            base = f"{rid} ({label})" if label else f"{rid} (unknown source)"
            if rid in outdated_ids:
                base += ", may be outdated"
            resolved.append(base)
        suffix = f" — sources: {', '.join(resolved)}" if resolved else ""
        print(f"  {i}. {t.get('name', '—')}{suffix}")

        corroborated = t.get("corroborated")
        if corroborated is True:
            print(f"       Corroborated: 2+ independent organisations")
        elif corroborated is False:
            print(f"       Single source organisation")

        for sq in t.get("source_quotes", []):
            print(f"       [{sq.get('report_id','')}] \"{sq.get('quote','')}\"")

    sources = declaration.get("sources", [])
    print(f"\nDeclaration: {len(sources)} source(s) found")

    cache_path = typology_response.get("_cache_path", "") if typology_response else ""
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
