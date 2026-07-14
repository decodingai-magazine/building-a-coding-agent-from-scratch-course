---
id: 111
feature: test-hermeticity
status: pending
---

# Scrub git's hook environment in tests — the pre-push hook is unusable in a worktree

Found during the `subagent-fanout` run (PR #33), not caused by it. Filed as a standalone infra fix.

## Scope

Git exports `GIT_DIR` (and friends) to every hook it runs. The pre-push hook runs `pytest`, so any
test that shells out to `git` **inherits `GIT_DIR` and operates against the wrong repository**.

Reproduced deterministically:

```
uv run pytest tests/unit/decode/sandbox/test_workspace.py -q          # 25 passed
GIT_DIR=$(git rev-parse --git-dir) uv run pytest ... -q               # 7 failed, 18 passed
```

Under the real hook the blast radius is ~20 failures across `test_workspace.py` and
`test_app_e2e.py`. The suite itself is green — the *hook environment* is what breaks it.

Two consequences, both already observed:

- **`--no-verify` becomes routine.** Every push from a worktree must skip the hook, which is
  precisely how a genuine failure eventually slips through unnoticed. The hook currently trains
  people to ignore it.
- **It can corrupt a worktree's index.** During PR #33 the `subagent-fanout` worktree ended up
  with 252 files staged as deleted while the identical files sat on disk (repaired with a mixed
  `git reset` — no work lost, the pushed commits were always intact). `GIT_DIR` pointing a
  git-invoking test at the wrong repo is the most plausible cause.

**Fix:** an autouse fixture in `tests/conftest.py` that scrubs git's hook variables from the
environment for every test — `GIT_DIR`, `GIT_INDEX_FILE`, `GIT_WORK_TREE`, `GIT_PREFIX` (check
`git --help environment` for others worth including, e.g. `GIT_OBJECT_DIRECTORY`). Tests that
shell out to `git` should be hermetic no matter who invokes them.

Do NOT fix this by changing the tests that shell out to git, and do NOT weaken the pre-push hook.
The environment leak is the bug.

## Acceptance Criteria

- [ ] An autouse fixture in `tests/conftest.py` removes git's hook variables from the environment for every test.
- [ ] `GIT_DIR=$(git rev-parse --git-dir) uv run pytest tests/unit/decode/sandbox/test_workspace.py -q` passes (it currently fails 7).
- [ ] A regression test pins the scrubbing — it fails if the fixture is removed.
- [ ] `git push` from a worktree succeeds through the real pre-push hook, with NO `--no-verify`.
- [ ] `make ci` green.

## Out of scope

- The Docker OOM flake (`test_docker_executor.py` / `test_sandbox_teardown.py`, exit 137 under
  full-suite memory pressure) — a separate, unrelated infra fragility.
- Any change to the `subagent-fanout` feature code.

## Log
