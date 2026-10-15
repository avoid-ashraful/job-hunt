#!/usr/bin/env bash
# Pull the latest version of every linked open-source project (git submodules in vendor/),
# then commit the new pointers if anything changed.
set -euo pipefail
cd "$(dirname "$0")/.."
git submodule update --init --remote --depth 1
if ! git diff --quiet -- vendor; then
  git add vendor
  git commit -m "chore(vendor): update submodules to latest upstream" -- vendor
  git push
fi
git submodule status
