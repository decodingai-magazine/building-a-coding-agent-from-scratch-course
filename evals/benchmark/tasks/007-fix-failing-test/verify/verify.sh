#!/usr/bin/env bash
# Oracle for 007-fix-failing-test: PASS iff (a) test_ranges.py is byte-identical to the seeded copy
# (its sha256 matches test_ranges.sha256 — the agent must not touch the tests), (b) the FAIL_TO_PASS
# test now passes, and (c) the PASS_TO_PASS tests still pass. Tests are run WITHOUT pytest (import the
# module, call each test fn) so the oracle needs only python — no extra dependency.
set -euo pipefail

python3 - <<'PY'
import hashlib
import sys

data = open("test_ranges.py", "rb").read()
digest = hashlib.sha256(data).hexdigest()
expected_digest = open("test_ranges.sha256", encoding="utf-8").read().strip()
if digest != expected_digest:
    print("FAIL: test_ranges.py was modified (checksum mismatch)")
    sys.exit(1)

sys.path.insert(0, ".")
try:
    import test_ranges
except Exception as exc:  # a broken import means the fix does not even load
    print(f"FAIL: could not import the test module: {exc!r}")
    sys.exit(1)

fail_to_pass = ["test_inclusive_end"]
pass_to_pass = ["test_starts_at_one", "test_contains_two", "test_no_zero"]

for name in fail_to_pass + pass_to_pass:
    fn = getattr(test_ranges, name)
    try:
        fn()
    except Exception as exc:
        print(f"FAIL: {name} did not pass: {exc!r}")
        sys.exit(1)

print("PASS")
PY
