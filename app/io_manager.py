"""
io_manager — all print() and input() calls live here and nowhere else.
"""


def show_main_menu() -> str:
    #Display the main menu and return the user's choice.
    print("\n--- SOW Screening System ---")
    print("1. New case assessment")
    print("2. View all cases")
    print("3. Exit")
    choice = input("Enter choice: ").strip()
    return choice


def show_message(text: str) -> None:
    #Print an informational message.
    print(text)


def show_error(text: str) -> None:
    #Print an error message.
    print(f"[ERROR] {text}")


def collect_profile() -> dict:
    """Prompt the officer for the client profile fields needed for assessment."""
    print("\n--- New Case: Sector Typology Assessment ---")
    print("Enter the client profile below. Press Enter to accept defaults where shown.\n")

    occupation = input("Occupation: ").strip()
    industry = input("Industry: ").strip()
    age = int(input("Age: ").strip())
    career_start_year = int(input("Career start year: ").strip())
    country = input("Country of residence [Singapore]: ").strip() or "Singapore"
    declared_net_worth = float(input("Declared net worth (SGD): ").strip())

    print("\nPaste the client's Source of Wealth declaration.")
    print("Enter a blank line when done.\n")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    declaration_text = " ".join(lines)

    return {
        "case_ref": f"SOW-LIVE-{age}-{career_start_year}",
        "age": age,
        "nationality": country,
        "country_of_residence": country,
        "occupation": occupation,
        "industry": industry,
        "career_start_year": career_start_year,
        "country": country,
        "pep_status": "none",
        "declared_net_worth_sgd": declared_net_worth,
        "expected_aum_sgd": declared_net_worth,
        "asset_composition": {
            "property_pct": 0, "cpf_pct": 0, "equities_pct": 0,
            "offshore_pct": 0, "cash_pct": 0,
        },
        "wealth_countries": [country],
        "declaration_text": declaration_text,
        "evidence": [],
    }


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


def show_assessment(result: dict) -> None:
    """Display the full assessment result in the terminal."""
    outcome = result.get("outcome", "UNKNOWN")
    findings = result.get("findings", [])
    documents = result.get("documents", [])

    print(f"\n{'=' * 50}")
    print(f"  Assessment Result: {outcome}")
    print(f"{'=' * 50}")

    numeric = [f for f in findings if f.get("dimension") != "typology"]
    typology = [f for f in findings if f.get("dimension") == "typology"]

    if numeric:
        print("\n--- Dimension Findings ---")
        for f in numeric:
            print(f"  [{f['rule_id']}] {f['detail']}")

    if typology:
        print("\n--- Typology Findings ---")
        print("  This case matches a pattern associated with:")
        for f in typology:
            print(f"    {f['detail']}")
            for ev in f.get("evidence", []):
                print(f"      - {ev}")

    if not findings:
        print("\n  No findings — wealth structure is consistent with the career benchmark.")

    if documents:
        print("\n--- Recommended Documents ---")
        for i, doc in enumerate(documents, 1):
            print(f"  {i}. {doc}")

    print()
