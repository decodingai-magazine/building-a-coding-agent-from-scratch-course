#!/usr/bin/env bash
# Lesson 6 — Agents catalog, subagents & parallel fan-out.
# Headless fan-out: the main loop spawns read-only Explore subagents in
# parallel; each hands back a compressed report instead of flooding the main
# context. The full showcase (/demo-4-review-swarm) is REPL-only — see
# README.md → Playbook.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "── Lesson 6: parallel Explore fan-out ─────────────────────────────"
uv run decode run "Spawn explore subagents in parallel: one maps src/decode/tools, one maps src/decode/permissions, one maps src/decode/sandbox. Merge their reports into a single table: module, responsibility, key entry point."
