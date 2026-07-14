#!/usr/bin/env bash
# Oracle for 019-patch-conflict-resolve: PASS iff (a) no conflict markers remain in any .py file and
# (b) greet.greet combines BOTH intents — the tree's capitalization AND the patch's new wording and
# punctuation — so greet("bob") == "Hi there, Bob!" and greet("ANNA") == "Hi there, Anna!". The
# behaviour asserts are authoritative (the oracle does not trust the possibly-edited test file). Only
# bash + python3 are used (allowed tool set: bash + python3 + git + sqlite3).
set -euo pipefail

python3 - <<'PY'
import glob
import importlib
import sys

# (a) no leftover conflict markers anywhere in the Python sources.
for path in sorted(glob.glob("**/*.py", recursive=True)):
    text = open(path, encoding="utf-8").read()
    if "<<<<<<<" in text or ">>>>>>>" in text:
        print(f"FAIL: unresolved conflict markers remain in {path}")
        sys.exit(1)

# (b) both intents present in the resolved greeting.
sys.path.insert(0, ".")
try:
    greet_mod = importlib.import_module("greet")
except Exception as exc:
    print(f"FAIL: could not import greet.py: {exc!r}")
    sys.exit(1)

cases = {"bob": "Hi there, Bob!", "ANNA": "Hi there, Anna!"}
for name, expected in cases.items():
    got = greet_mod.greet(name)
    if got != expected:
        print(f"FAIL: greet({name!r}) returned {got!r}, expected {expected!r} (both intents kept?)")
        sys.exit(1)

print("PASS")
PY
