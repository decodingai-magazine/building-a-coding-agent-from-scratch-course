#!/usr/bin/env bash
# Oracle for 001-find-and-replace: PASS iff config.ini has timeout=60 (not 30) and every other line
# is byte-for-byte what it was. expected.ini is the whole file with only the timeout changed, so a
# single diff proves both "new value present / old absent" and "rest of file unchanged".
set -euo pipefail

if [[ ! -f config.ini ]]; then
  echo "FAIL: config.ini is missing"
  exit 1
fi

if ! grep -q '^timeout = 60$' config.ini; then
  echo "FAIL: config.ini does not set 'timeout = 60'"
  exit 1
fi

if grep -q '^timeout = 30$' config.ini; then
  echo "FAIL: the old 'timeout = 30' is still present"
  exit 1
fi

if ! diff -u expected.ini config.ini; then
  echo "FAIL: config.ini differs from the expected file beyond the timeout value"
  exit 1
fi

echo "PASS"
