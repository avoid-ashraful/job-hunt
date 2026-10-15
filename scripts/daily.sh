#!/usr/bin/env bash
# Daily scan, run from cron. Writes private/jobs/YYYY-MM-DD.md and a log.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p private/state/logs
log="private/state/logs/$(TZ=Asia/Dhaka date +%F).log"
uv run python -m scanner run >>"$log" 2>&1
