#!/bin/sh
# Launcher used by the desktop shortcut. Output goes to logs/app.log for troubleshooting.
cd "$(dirname "$0")" || exit 1
exec python3 main.py >> logs/app.log 2>&1
