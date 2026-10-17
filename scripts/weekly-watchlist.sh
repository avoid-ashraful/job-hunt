#!/usr/bin/env bash
# Weekly watchlist rebuild, run from cron: retries rate-limited/transient boards, re-checks board owners.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p private/state/logs
log="private/state/logs/weekly-watchlist-$(TZ=Asia/Dhaka date +%F).log"
uv run python -m scanner watchlist build >>"$log" 2>&1
