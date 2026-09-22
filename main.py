"""
main.py — entry point and orchestrator for the SOW Screening System.

Imports all four managers and calls them in sequence.
Managers never import each other — all coordination happens here.
"""

import logging
import os
from datetime import datetime

from dotenv import load_dotenv

from app import io_manager


def main() -> None:
    while True:
        choice = io_manager.show_main_menu()

        if choice == "1":
            io_manager.show_message("New case assessment — not implemented yet.")
        elif choice == "2":
            io_manager.show_message("View all cases — not implemented yet.")
        elif choice == "3":
            io_manager.show_message("Goodbye.")
            break
        else:
            io_manager.show_error("Invalid choice. Please enter 1, 2, or 3.")


if __name__ == "__main__":
    main()
