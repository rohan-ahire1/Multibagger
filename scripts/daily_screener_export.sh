#!/bin/bash
# Auto-resume screener.in annual export until full coverage, then no-op.
# Run daily via cron; screener throttles ~180 exports/day so this fills in over
# a few days. Self-skips cached symbols and stops once ~complete.
#
# Requires a live session cookie in data/tmp/screener_cookies.txt (it expires;
# if the log shows mass FAILEDs, re-paste a fresh sessionid/csrftoken).
set -euo pipefail
REPO="/Users/rohanahire/Documents/Personal_Projects/multibagger"
cd "$REPO" || exit 1
LOG="data/tmp/daily_export.log"
mkdir -p data/tmp

ts() { date "+%Y-%m-%d %H:%M:%S"; }
have=$(ls data/raw/screener_xlsx/*.xlsx 2>/dev/null | wc -l | tr -d ' ')
echo "[$(ts)] start: $have/525 cached" >> "$LOG"

# Done? stop cleanly (cron entry can then be removed).
if [ "$have" -ge 515 ]; then
  echo "[$(ts)] coverage complete ($have/525) — nothing to do." >> "$LOG"
  exit 0
fi

# No cookie => can't fetch; warn and bail (don't burn the run).
if [ ! -s data/tmp/screener_cookies.txt ]; then
  echo "[$(ts)] MISSING data/tmp/screener_cookies.txt — re-paste session and retry." >> "$LOG"
  exit 0
fi

.venv/bin/python scripts/screener_export.py --sleep 3.0 >> "$LOG" 2>&1 || true
.venv/bin/python scripts/parse_screener_xlsx.py >> "$LOG" 2>&1 || true

now=$(ls data/raw/screener_xlsx/*.xlsx 2>/dev/null | wc -l | tr -d ' ')
echo "[$(ts)] done: $now/525 cached (+$((now - have)) this run)" >> "$LOG"
