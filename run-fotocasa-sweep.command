#!/bin/bash
# Optional: double-click to sweep Fotocasa once (it's deliberately excluded
# from the automated cloud loop and the normal local sweep).
cd "$(dirname "$0")"
.venv/bin/python bot.py --sources fotocasa
echo
read -n 1 -s -r -p "Done — press any key to close this window."
