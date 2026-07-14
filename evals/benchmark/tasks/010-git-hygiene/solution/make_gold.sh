#!/usr/bin/env bash
# The gold git operations for oracle-sanity ONLY. A git task's solved state is a set of git actions,
# not passive files, so it cannot be overlaid the way other tasks overlay a solution/ file. The
# oracle-sanity harness overlays this script and verify.sh runs it to reach the gold state; it is
# NEVER present in a real agent run (solution/ is never injected there). Mirrors a correct answer:
# a new branch, ONLY the src/ files staged, one Conventional-Commits commit, scratch files untouched.
set -euo pipefail

git checkout -q -b add-search
git add src/search.py src/search_index.py
git commit -q -m "feat: add search feature"
