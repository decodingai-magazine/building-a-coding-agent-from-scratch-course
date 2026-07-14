#!/usr/bin/env bash
# Oracle for 010-git-hygiene: PASS iff the repo is on branch ``add-search`` with a single new commit
# whose message is Conventional-Commits shaped, that commit adds EXACTLY the two src/ files, and the
# two scratch files are still uncommitted (untracked). Only bash + git + python3 are used (python3
# does the git-porcelain parsing and the message regex).
#
# The ``make_gold.sh`` block runs ONLY under the oracle-sanity harness, which overlays solution/'s
# gold git script; in a real agent run solution/ is never injected, so this file is absent and the
# agent's own repository is judged.
set -euo pipefail

if [[ -f make_gold.sh ]]; then
  bash make_gold.sh
fi

python3 - <<'PY'
import re
import subprocess
import sys


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"FAIL: git {' '.join(args)} failed: {proc.stderr.strip()}")
        sys.exit(1)
    return proc.stdout


branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
if branch != "add-search":
    print(f"FAIL: expected to be on branch 'add-search', but HEAD is '{branch}'")
    sys.exit(1)

# The new branch must carry exactly one commit on top of the seeded initial commit.
revs = git("rev-list", "HEAD").split()
if len(revs) != 2:
    print(f"FAIL: expected 2 commits on the branch (initial + feature), found {len(revs)}")
    sys.exit(1)

subject = git("log", "-1", "--format=%s").strip()
conventional = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: .+"
)
if not conventional.match(subject):
    print(f"FAIL: commit subject is not Conventional-Commits shaped: {subject!r}")
    sys.exit(1)

# Files added by the HEAD commit, relative to its parent.
changed = set(git("show", "--name-only", "--format=", "HEAD").split())
expected = {"src/search.py", "src/search_index.py"}
if changed != expected:
    print(f"FAIL: the commit must add exactly {sorted(expected)}, but it changed {sorted(changed)}")
    sys.exit(1)

# The scratch files must still be untracked (uncommitted).
porcelain = git("status", "--porcelain").splitlines()
untracked = {line[3:] for line in porcelain if line.startswith("?? ")}
for scratch in ("scratch.txt", "debug_output.txt"):
    if scratch not in untracked:
        print(f"FAIL: {scratch} should still be untracked, but it is not in `git status`")
        sys.exit(1)

print("PASS")
PY
