#!/usr/bin/env bash
# Oracle for 017-flaky-test-hunt: PASS iff the hidden test suite is green on THREE consecutive fresh
# runs AND the source root cause is genuinely fixed (not just papered over in the tests).
#
# Determinism (why the FAIL direction is not luck): the suite is run in SORTED test order in a fresh
# process each time. In that order test_collect_apple seeds the shared mutable-default bucket, then
# test_collect_banana (next alphabetically) observes the leaked ["apple"] and fails — every run. So an
# unfixed registry.py fails run 1 deterministically, and the 3-run loop catches the seeded flake.
#
# Only bash + python3 are used (allowed tool set: bash + python3 + git + sqlite3).
set -euo pipefail

RUN_SUITE='
import sys
sys.path.insert(0, ".")
import _verify_suite as suite
for name in sorted(n for n in dir(suite) if n.startswith("test_")):
    getattr(suite, name)()
'

for run in 1 2 3; do
  if ! python3 -c "$RUN_SUITE" >/dev/null 2>&1; then
    echo "FAIL: the test suite was not green on run ${run} of 3 (order-dependent shared state leaks)"
    exit 1
  fi
done

# Anti-cheat: prove the ROOT CAUSE is fixed in registry.py, not merely worked around in the tests.
# Two independent calls must each return a fresh single-item bucket; a leaking mutable default would
# make the second call return two items.
python3 - <<'PY'
import sys

sys.path.insert(0, ".")
from registry import collect

first = collect("x")
second = collect("y")
if first != ["x"] or second != ["y"]:
    print(f"FAIL: collect() still leaks state across calls: {first!r} then {second!r}")
    sys.exit(1)
PY

echo "PASS"
