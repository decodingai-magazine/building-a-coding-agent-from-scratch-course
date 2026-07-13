#!/usr/bin/env bash
# The hidden oracle: PASS iff greeting.txt exists in the Workspace and its first line is the exact
# text "hello world". Uses only bash + coreutils so it runs identically host-side and in-sandbox.
set -euo pipefail

if [[ ! -f greeting.txt ]]; then
  echo "FAIL: greeting.txt is missing"
  exit 1
fi

first_line="$(head -n 1 greeting.txt)"
if [[ "$first_line" != "hello world" ]]; then
  echo "FAIL: greeting.txt first line is '$first_line', expected 'hello world'"
  exit 1
fi

echo "PASS"
