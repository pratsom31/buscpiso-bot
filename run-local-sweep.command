#!/bin/bash
# Double-click this file in Finder to sweep Idealista on demand.
# (Both block cloud servers, so they can't run on Pipedream — this
# local run covers them whenever you feel like it.)
cd "$(dirname "$0")"
.venv/bin/python bot.py --sources idealista
echo
read -n 1 -s -r -p "Done — press any key to close this window."
