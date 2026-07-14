#!/usr/bin/env bash
# Oracle for 016-implement-from-spec: PASS iff every hidden test in _verify_intervals.py passes when
# run against the agent's intervals.py. The tests are executed WITHOUT pytest (import the module,
# call each test_* function) so the oracle needs only python3 — the slim sandbox image ships no
# pytest. Allowed tool set: bash + python3 + git + sqlite3.
set -euo pipefail

python3 - <<'PY'
import sys

sys.path.insert(0, ".")
try:
    import _verify_intervals as suite
except Exception as exc:  # a broken/absent implementation fails the import of the test module
    print(f"FAIL: could not import the hidden test module (is merge_intervals implemented?): {exc!r}")
    sys.exit(1)

for name in sorted(n for n in dir(suite) if n.startswith("test_")):
    try:
        getattr(suite, name)()
    except Exception as exc:
        print(f"FAIL: {name} did not pass: {exc!r}")
        sys.exit(1)

print("PASS")
PY
