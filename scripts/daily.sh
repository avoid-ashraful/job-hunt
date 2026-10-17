#!/usr/bin/env bash
# Daily scan, run from cron. Writes private/jobs/YYYY-MM-DD.md and a log.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p private/state/logs
log="private/state/logs/$(TZ=Asia/Dhaka date +%F).log"
count_file="private/state/selftest-failures"
rm -f "$count_file"
uv run python -m scanner selftest --failure-count-file "$count_file" >>"$log" 2>&1 || true
export SCANNER_SELFTEST_FAILURES="$(cat "$count_file" 2>/dev/null || printf '0')"
uv run python -m scanner run >>"$log" 2>&1
