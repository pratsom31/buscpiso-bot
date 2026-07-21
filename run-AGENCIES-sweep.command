#!/bin/bash
# Double-click to sweep the SAME sources Pipedream ran (agencies + Habitaclia +
# Pisos.com). Use this if Pipedream is paused/out of credits.
cd "$(dirname "$0")"
.venv/bin/python bot.py --sources housfy,shbarcelona,loca,teixidor,habitaclia,pisos
echo
read -n 1 -s -r -p "Done — press any key to close this window."
