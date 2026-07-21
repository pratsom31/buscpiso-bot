#!/bin/bash
# Install the bot as a launchd agent that runs now and every 5 hours.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.prate.bcn-rental-bot.plist"

mkdir -p "$DIR/log" "$HOME/Library/LaunchAgents"
sed "s|__DIR__|$DIR|g" "$DIR/launchd.plist.template" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed. Runs immediately and then every 5 hours (while the Mac is awake)."
echo "Logs: $DIR/log/bot.log"
echo "To stop it:  launchctl unload $PLIST"
