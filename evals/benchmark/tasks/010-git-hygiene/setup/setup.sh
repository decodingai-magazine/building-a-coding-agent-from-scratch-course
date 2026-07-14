#!/usr/bin/env bash
# Build the starting git state IN the Workspace (state that cannot be a committed file — a real repo
# history with a clean initial commit and a dirty working tree). Runs after seeding; only bash + git.
#   - branch main with one committed file (README.md)
#   - two RELEVANT uncommitted files under src/ (the search feature)
#   - two IRRELEVANT uncommitted scratch files that must stay out of the commit
set -euo pipefail

git init -q -b main .
git config user.email "eval@example.com"
git config user.name "Eval Fixture"

printf '# demo project\n' > README.md
git add README.md
git commit -q -m "chore: initial commit"

mkdir -p src
printf 'def search(query, items):\n    return [i for i in items if query in i]\n' > src/search.py
printf 'def build_index(items):\n    return {i: n for n, i in enumerate(items)}\n' > src/search_index.py

printf 'random debugging notes, do not commit\n' > scratch.txt
printf 'TRACE line 1\nTRACE line 2\n' > debug_output.txt
