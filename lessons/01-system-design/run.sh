#!/usr/bin/env bash
# Lesson 1 — What we're building + system design.
# One headless run through the whole harness: prompt → ReAct loop → tools → answer on stdout.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 1: the harness, end to end ──────────────────────────────"
echo "One decode run = the full anatomy: CLI → runtime driver → agent loop"
echo "→ gated tools → provider. Watch stderr for the exec_id (lesson 3)."
echo

uv run decode run "Map the src/decode package: one line per module explaining its role in the harness. Order them by the path a user prompt travels."

echo
echo "Everything the run produced lives under .decode/ (sessions, logs, memory):"
ls .decode 2>/dev/null || echo "  (no .decode yet — run from the repo root after a REPL session)"
