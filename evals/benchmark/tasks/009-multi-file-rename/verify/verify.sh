#!/usr/bin/env bash
# Oracle for 009-multi-file-rename: PASS iff (a) the old name ``compute_total`` no longer occurs in
# ANY Python file in the Workspace, (b) the new name ``calculate_total`` is importable from billing,
# (c) every caller module still imports cleanly, and (d) the seeded tests pass against the new name.
# Tests run WITHOUT pytest (import the module, call each ``test_`` fn) so only python3 is needed
# (allowed tool set: bash + python3 + git + sqlite3). The grep is scoped to ``*.py`` so this script
# (a .sh file) can never match itself.
set -euo pipefail

if grep -rnI --include='*.py' '\bcompute_total\b' .; then
  echo "FAIL: the old name compute_total still occurs in a Python file (see matches above)"
  exit 1
fi

python3 - <<'PY'
import sys

sys.path.insert(0, ".")

try:
    from billing import calculate_total
except Exception as exc:  # the rename must expose the new name from billing
    print(f"FAIL: could not import calculate_total from billing: {exc!r}")
    sys.exit(1)

# Every caller must still import cleanly (a half-done rename breaks these imports).
for module_name in ("report", "cli"):
    try:
        __import__(module_name)
    except Exception as exc:
        print(f"FAIL: caller module {module_name}.py no longer imports: {exc!r}")
        sys.exit(1)

# Behaviour must be unchanged: the renamed function still totals correctly.
if calculate_total([1.0, 2.0, 3.0]) != 6.0 or calculate_total([100.0], 0.1) != 110.0:
    print("FAIL: calculate_total no longer computes the expected totals")
    sys.exit(1)

try:
    import test_billing
except Exception as exc:
    print(f"FAIL: could not import the test module: {exc!r}")
    sys.exit(1)

test_functions = [name for name in dir(test_billing) if name.startswith("test_")]
if not test_functions:
    print("FAIL: the test module exposes no test_ functions")
    sys.exit(1)

for name in sorted(test_functions):
    try:
        getattr(test_billing, name)()
    except Exception as exc:
        print(f"FAIL: {name} did not pass: {exc!r}")
        sys.exit(1)

print("PASS")
PY
