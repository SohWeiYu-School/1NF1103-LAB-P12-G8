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
