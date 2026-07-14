#!/usr/bin/env bash
# Oracle for 003-csv-to-json: PASS iff people.json parses and is deep-equal to expected.json. A deep
# equality over parsed JSON (not a text diff) enforces the typed contract — age must be a JSON number
# and active a JSON boolean, so a stringified "30" or "true" fails.
set -euo pipefail

python3 - <<'PY'
import json
import sys

try:
    with open("people.json", encoding="utf-8") as fh:
        got = json.load(fh)
except FileNotFoundError:
    print("FAIL: people.json is missing")
    sys.exit(1)
except json.JSONDecodeError as exc:
    print(f"FAIL: people.json is not valid JSON: {exc}")
    sys.exit(1)

with open("expected.json", encoding="utf-8") as fh:
    expected = json.load(fh)

if got == expected:
    print("PASS")
    sys.exit(0)

print("FAIL: people.json does not match the expected typed structure")
print(f"  got:      {got}")
print(f"  expected: {expected}")
sys.exit(1)
PY
