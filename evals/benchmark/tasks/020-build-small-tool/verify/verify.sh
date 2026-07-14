#!/usr/bin/env bash
# Oracle for 020-build-small-tool: PASS iff every hidden test in _verify_wordfreq.py passes against
# the agent's wordfreq.py. The tests drive the CLI as a subprocess and are run WITHOUT pytest (import
# the module, call each test_* function) so the oracle needs only python3 — the slim sandbox image
# ships no pytest. Allowed tool set: bash + python3 + git + sqlite3.
set -euo pipefail

python3 - <<'PY'
import sys

sys.path.insert(0, ".")
try:
    import _verify_wordfreq as suite
except Exception as exc:
    print(f"FAIL: could not import the hidden test module: {exc!r}")
    sys.exit(1)

for name in sorted(n for n in dir(suite) if n.startswith("test_")):
    try:
        getattr(suite, name)()
    except Exception as exc:
        print(f"FAIL: {name} did not pass: {exc!r}")
        sys.exit(1)

print("PASS")
PY
