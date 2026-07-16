#!/usr/bin/env bash
# Lesson 5 — Containing the agent: permissions → sandbox.
# Scripted here: a headless run whose *entire* tool scope lives inside an
# isolated Docker Workspace, cloned from this very repo, handed back as a git
# branch. The interactive permission gate (y/n, plan mode, settings.json rules)
# is in README.md → Playbook.
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon not running — start Docker Desktop and re-run." >&2
  echo "(Or read README.md → Playbook for the no-docker permission-gate demo.)" >&2
  exit 0
fi

echo "── Lesson 5: an isolated Workspace over this repo ─────────────────"
echo "The agent reads, edits, and runs bash ONLY inside /workspace"
echo "(a clone of this repo at HEAD). Your checkout is untouched."
echo

rm -rf .decode/sandbox   # force a fresh clone — a populated Workspace is reused, never re-cloned

SANDBOX_MODE=docker uv run decode run --repo . --local \
  "Create docs/HELLO_FROM_THE_SANDBOX.md containing one paragraph on what an isolated Workspace is, then run: git log --oneline -3"

echo
echo "── Hand-back: the work came home as a branch ──────────────────────"
git branch --list 'decode/*' | tail -3
echo "Inspect one with: git show <branch> --stat"
