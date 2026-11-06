#!/usr/bin/env python3
"""Personal Finance: run with `python3 main.py`.

On launch it connects to GridCraft (starting it with the control channel if
needed) and opens the master workbook in ~/Documents/PersonalFinance.
"""
import sys

try:
    import tkinter  # noqa: F401
    import matplotlib  # noqa: F401
except ImportError as e:
    sys.exit(f"Missing dependency ({e.name}). Install with:\n"
             "  sudo apt install python3-tk python3-matplotlib")

from ui.app import main

if __name__ == "__main__":
    main()
