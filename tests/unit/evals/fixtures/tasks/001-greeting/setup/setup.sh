#!/usr/bin/env bash
# Runs in the Workspace after seeding. Writes a marker that could not be a committed file (here a
# trivial stand-in for git history / a sqlite DB), so the oracle-sanity harness exercises setup.sh.
set -euo pipefail
echo "seeded" > seeded.txt
