"""
io_manager — all print() and input() calls live here and nowhere else.
"""



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


