#!/usr/bin/env bash
# The gold git operations for oracle-sanity ONLY. A git task's solved state is a set of git actions,
# not passive files, so it cannot be overlaid the way other tasks overlay a solution/ file. The
# oracle-sanity harness overlays this script and verify.sh runs it to reach the gold state; it is
# NEVER present in a real agent run (solution/ is never injected there). Mirrors a correct answer:
# find the breaking commit and revert it, adding a revert commit on top without rewriting anything.
set -euo pipefail

target="$(git log --format=%H --grep='simplify multiply' -n 1)"
git revert --no-edit "$target"
