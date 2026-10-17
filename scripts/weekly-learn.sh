#!/usr/bin/env bash
# Weekly learning report, run from cron.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p private/state/logs
log="private/state/logs/weekly-learn-$(TZ=Asia/Dhaka date +%F).log"
uv run python -m scanner learn --days 7 >>"$log" 2>&1
