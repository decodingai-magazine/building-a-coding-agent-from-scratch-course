#!/usr/bin/env bash
# Oracle for 014-cli-flag-add: PASS iff the hidden test_cli_modes.py tests all pass — the default text
# mode is unchanged AND the new --json mode emits the required object. The hidden test file ships in
# verify/ and is injected into the Workspace only at grade time, so the agent never sees it. verify.sh
# runs it by importing the module and calling each ``test_`` function directly (the task-007 pattern),
# so only python3 is needed — no pytest install in the sandbox (allowed tool set: bash + python3 +
# git + sqlite3).
set -euo pipefail

python3 - <<'PY'
import sys

sys.path.insert(0, ".")

try:
    import test_cli_modes
except Exception as exc:
    print(f"FAIL: could not import the hidden test module: {exc!r}")
    sys.exit(1)

test_functions = sorted(name for name in dir(test_cli_modes) if name.startswith("test_"))
if not test_functions:
    print("FAIL: the hidden test module exposes no test_ functions")
    sys.exit(1)

for name in test_functions:
    try:
        getattr(test_cli_modes, name)()
    except AssertionError as exc:
        print(f"FAIL: {name} failed: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"FAIL: {name} errored: {exc!r}")
        sys.exit(1)

print("PASS")
PY
