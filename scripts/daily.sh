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
if ! uv run python -m scanner run >>"$log" 2>&1; then
  # A crashed run must still leave a visible note where the report would be.
  report="private/jobs/$(TZ=Asia/Dhaka date +%F).md"
  mkdir -p private/jobs
  { echo; echo "## ⚠ Daily scan failed at $(TZ=Asia/Dhaka date +%H:%M)"; echo; echo '```'; tail -n 20 "$log"; echo '```'; } >>"$report"
  exit 1
fi
