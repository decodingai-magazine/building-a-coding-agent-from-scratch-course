#!/usr/bin/env bash
# Lesson 7 — Is the agent good? The eval stack.
# Runs the two offline tracks. Both need OPIK_API_KEY + the provider key and
# cost real money (they run the real agent); without keys each target skips
# with one friendly line — that behavior is itself part of the lesson.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 7a: outcome benchmark (hidden verify.sh oracles) ────────"
echo "One easy task, one trial — the cheapest possible real run:"
make eval-benchmark ARGS='--difficulty easy --nb-samples 1'

echo
echo "── Lesson 7b: behavior regression probes (threshold gate) ─────────"
make eval-regression

echo
echo "── Go further ──────────────────────────────────────────────────────"
echo "  make eval-benchmark ARGS='--trials 5'     # pass@1 / pass@k / pass^k reliability"
echo "  python -m evals online                    # judge live traces already emitted"
echo "  /demo-2-bug-hunt (in the REPL)            # the human-judged track"
