#!/usr/bin/env bash
# Lesson 4 — Context engineering: the window is a budget.
# Headless slice: memory injection + LSP code intelligence. Compaction and the
# footer gauge only show in the REPL — see README.md → Playbook.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 4a: memory is injected, not retrieved ───────────────────"
echo "AGENTS.md + .decode/MEMORY.md load into context at session start."
echo
uv run decode run "Without reading any file, what project conventions do you already know from your memory? Quote three and name where they came from."

echo
echo "── Lesson 4b: LSP — code intelligence as a context move ───────────"
uv run decode run "Use the lsp tool to find the definition of build_agent, then its references, and summarize how the agent is wired into the runtime."
