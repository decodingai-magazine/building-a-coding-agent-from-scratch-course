#!/usr/bin/env bash
# Lesson 2 — The agent loop & the human in it.
# Headless slice of the loop: model reasons → calls tools → observes → answers.
# The human-in-the-loop parts (steer / follow-up / abort / y-n gate) are
# interactive by nature — see README.md → Playbook.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 2: one turn through the ReAct loop ──────────────────────"
uv run decode run "List the python files under src/decode/agent and summarize what the loop does, step by step, citing file names."

echo
echo "── Same loop, different provider (one seam, ADR-0005) ─────────────"
echo "Re-run with: LLM_PROVIDER=openrouter $0   (needs OPENROUTER_API_KEY)"
echo "The loop code does not change — only the model behind the seam."
