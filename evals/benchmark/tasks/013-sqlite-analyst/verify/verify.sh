#!/usr/bin/env bash
# Oracle for 013-sqlite-analyst: PASS iff answer.txt names the customer with the highest total
# revenue. The expected name is computed live from orders.db (not hard-coded), so the oracle stays
# correct even if the fixture data changes; the submitted answer is compared stripped of surrounding
# whitespace so a trailing newline is fine (content, not bytes). Uses only python3 + its stdlib
# sqlite3 (allowed tool set: bash + python3 + git + sqlite3).
set -euo pipefail

python3 - <<'PY'
import sqlite3
import sys

try:
    answer = open("answer.txt", encoding="utf-8").read().strip()
except FileNotFoundError:
    print("FAIL: answer.txt is missing")
    sys.exit(1)

conn = sqlite3.connect("orders.db")
row = conn.execute(
    """
    SELECT c.name
    FROM customers AS c
    JOIN orders AS o ON o.customer_id = c.id
    GROUP BY c.id
    ORDER BY SUM(o.amount) DESC, c.name ASC
    LIMIT 1
    """
).fetchone()
conn.close()

expected = row[0]
if answer != expected:
    print(f"FAIL: top customer by total revenue is {expected!r}, but answer.txt says {answer!r}")
    sys.exit(1)

print("PASS")
PY
