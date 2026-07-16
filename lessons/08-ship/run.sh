#!/usr/bin/env bash
# Lesson 8 — Ship it to your team.
# Builder → operator: the same agent, hydrated from an environment-scoped
# secret bucket instead of your laptop's .env. Proves the two invariants that
# matter in production: no silent .env backfill, loud failure on a missing
# bucket. The full cloud pipeline is getting_started/infra.md (costs money).
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 8a: create the staging Environment Bucket ───────────────"
make sync-secrets ENV=staging

echo
echo "── Lesson 8b: run against the bucket — .env is OUT of the chain ───"
echo "(unsetting GEMINI_API_KEY from the process env to prove the point)"
env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode run "say hi in exactly three words"

echo
echo "── Lesson 8c: a missing bucket fails LOUD, never backfills ────────"
echo "Try it: DECODE_ENV=prod uv run decode run 'hi'"
echo "Expected: ONE friendly stderr line naming the fix, exit 1, no traceback."
