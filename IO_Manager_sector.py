import uuid
import os
import tkinter as tk
from tkinter import filedialog

from app.data_manager import save_case_record


def show_main_menu() -> str:
    """Display the main menu and return the user's choice."""

    print("\n========================================")
    print("     SOURCE OF WEALTH SCREENING SYSTEM")
    print("========================================")
    print("1. New Client")
    print("2. Existing Client")
    print("3. Quit")

    while True:
        choice = input("\nEnter your choice (1-3): ").strip()

        if choice in ("1", "2", "3"):
            return choice

        print("Invalid choice. Please enter 1, 2, or 3.")


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
    If yes, open a Tkinter file picker.
    """

    print("\n=== Supporting Documents ===")

    answer = input(
        "Does the client have any supporting documents? (y/n): "
    ).strip().lower()

    if answer != "y":
        return []

    print("\nOpening file explorer...")

    # Create a hidden Tkinter root window
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

    for path in file_paths:
        documents.append({
            "file_name": os.path.basename(path),
            "file_path": path
        })

    print(f"\n{len(documents)} document(s) selected.")

    for document in documents:
        print(f"- {document['file_name']}")

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

    documents = record["supporting_documents"]

    if not documents:
        print("No supporting documents provided.")

    else:
        for index, document in enumerate(
            documents,
            start=1
        ):

            print(
                f"{index}. {document['file_name']}"
            )

            print(
                f"   Path: {document['file_path']}"
            )

    print("\n" + "=" * 60)


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

    input("\nPress Enter to return to the main menu...")


def main():
    """Main program loop."""

    while True:

        choice = show_main_menu()

        if choice == "1":

            create_new_client()

        elif choice == "2":

            print("\n=== Existing Client ===")

            client_ref = input(
                "Enter client reference: "
            ).strip()

            print(
                f"\nExisting client lookup for "
                f"{client_ref} is not implemented yet."
            )

            input(
                "\nPress Enter to return to the main menu..."
            )

        elif choice == "3":

            print(
                "\nThank you for using the "
                "Source of Wealth Screening System."
            )

            break


if __name__ == "__main__":
    main()