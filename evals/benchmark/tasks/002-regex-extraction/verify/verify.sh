#!/usr/bin/env bash
# Oracle for 002-regex-extraction: PASS iff emails.txt is exactly the unique, ascending-sorted list
# of the emails buried in contacts.txt. Compares the LINE SEQUENCE (splitlines) against the gold
# expected.txt, so a missing/extra trailing newline is graded as content, not byte cosmetics — a
# valid "\n".join(sorted(emails)) with no final newline still passes.
set -euo pipefail

python3 - <<'PY'
import sys

try:
    got = open("emails.txt", encoding="utf-8").read().splitlines()
except FileNotFoundError:
    print("FAIL: emails.txt is missing")
    sys.exit(1)

expected = open("expected.txt", encoding="utf-8").read().splitlines()

if got == expected:
    print("PASS")
    sys.exit(0)

print("FAIL: emails.txt is not the expected unique, sorted email list")
print(f"  got:      {got}")
print(f"  expected: {expected}")
sys.exit(1)
PY
