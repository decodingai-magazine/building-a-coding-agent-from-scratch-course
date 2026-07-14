#!/usr/bin/env bash
# Oracle for 018-git-bisect-revert: PASS iff (a) the test suite passes, (b) a revert commit sits on
# top of the history, and (c) the original commits are intact — no amend/rebase/reset rewrote them.
# Intactness is proven by the seeded `original-head` tag still being an ancestor of HEAD and every
# original commit subject still present. Only bash + git + python3 are used (allowed tool set:
# bash + python3 + git + sqlite3).
#
# The make_gold.sh block runs ONLY under the oracle-sanity harness, which overlays solution/'s gold
# git script; in a real agent run solution/ is never injected, so this file is absent and the agent's
# own repository is judged.
set -euo pipefail

if [[ -f make_gold.sh ]]; then
  bash make_gold.sh
fi

python3 - <<'PY'
import subprocess
import sys


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"FAIL: git {' '.join(args)} failed: {proc.stderr.strip()}")
        sys.exit(1)
    return proc.stdout


# (a) the whole test suite must pass now that the breaking commit is reverted.
sys.path.insert(0, ".")
try:
    import test_calc
except Exception as exc:
    print(f"FAIL: could not import the tests: {exc!r}")
    sys.exit(1)
for name in sorted(n for n in dir(test_calc) if n.startswith("test_")):
    try:
        getattr(test_calc, name)()
    except Exception as exc:
        print(f"FAIL: {name} did not pass (was the breaking commit reverted?): {exc!r}")
        sys.exit(1)

subjects = git("log", "--format=%s").splitlines()

# (b) a revert commit must exist (git revert's default subject starts with "Revert").
if not any(subject.startswith("Revert") for subject in subjects):
    print("FAIL: no revert commit found — the fix must be recorded as a git revert")
    sys.exit(1)

# (c) the original history must be intact (no amend / rebase / reset).
if subprocess.run(
    ["git", "rev-parse", "-q", "--verify", "original-head"],
    capture_output=True,
    check=False,
).returncode != 0:
    print("FAIL: the seeded original-head tag is gone — history was rewritten")
    sys.exit(1)
if subprocess.run(
    ["git", "merge-base", "--is-ancestor", "original-head", "HEAD"], check=False
).returncode != 0:
    print("FAIL: the seeded commits are no longer ancestors of HEAD — history was rewritten")
    sys.exit(1)
for original in (
    "feat: add calculator",
    "docs: describe the calculator module",
    "refactor: simplify multiply",
    "feat: add power",
):
    if original not in subjects:
        print(f"FAIL: an original commit is missing from history (rewritten?): {original!r}")
        sys.exit(1)

print("PASS")
PY
