#!/usr/bin/env bash
# Lesson 3 — Durable execution, HITL & replay.
# Happy path scripted here: one durable run, inspect its checkpoints, print the
# what-if replay recipe. The kill -9 crash-resume and the --hitl wait are
# interactive — see README.md → Playbook.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 3: a durable run (checkpoint per model + tool call) ─────"
echo "stdout stays exactly the agent's answer; the exec_id rides stderr."
echo

STDERR_LOG=$(mktemp)
uv run decode run "Read the Makefile and list every target with a one-line description." \
  2> >(tee "${STDERR_LOG}" >&2)

LAST_ID=$(grep -E '^exec_id: ' "${STDERR_LOG}" | awk '{print $2}' | tail -1 || true)
rm -f "${STDERR_LOG}"

echo
echo "── Inspect the execution record ───────────────────────────────────"
if [ -n "${LAST_ID}" ]; then
  uv run kitaru executions get "${LAST_ID}"
  echo
  echo "── What-if replay (model swapped, upstream served from cache) ─────"
  echo "uv run decode replay ${LAST_ID} --from decode_runtime_model_request --model gemini-2.5-pro"
else
  echo "Could not auto-detect the exec_id — copy it from stderr above, then:"
  echo "  uv run kitaru executions get <ID>"
  echo "  uv run decode replay <ID> --from decode_runtime_model_request --model gemini-2.5-pro"
fi
