---
id: 111
feature: test-hermeticity
status: done
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

- [x] An autouse fixture in `tests/conftest.py` removes git's hook variables from the environment for every test.
- [x] `GIT_DIR=$(git rev-parse --git-dir) uv run pytest tests/unit/decode/sandbox/test_workspace.py -q` passes (it currently fails 7).
- [x] A regression test pins the scrubbing — it fails if the fixture is removed.
- [x] `git push` from a worktree succeeds through the real pre-push hook, with NO `--no-verify`.
- [x] `make ci` green. — 1714 passed / 0 failed, exit 0. The Docker suite needs a `docker container
  prune` under memory pressure (the `exit 137` OOM flake); unrelated to this change — neither Docker
  test references `git`. Confirmed independently by the Tester (120/120 integration) and re-run here.

## Out of scope

- The Docker OOM flake (`test_docker_executor.py` / `test_sandbox_teardown.py`, exit 137 under
  full-suite memory pressure) — a separate, unrelated infra fragility.
- Any change to the `subagent-fanout` feature code.

## Log

### [SWE] 2026-07-14 11:20 — Implementation

**Files modified**
- `tests/support/git_env.py` — NEW. `GIT_HOOK_ENV_VARS`: the 8 variables that redirect git at a
  different repo/index/worktree/object store, with the reasoning for each and for what is
  deliberately left alone.
- `tests/conftest.py` — NEW autouse fixture `_scrub_git_hook_env`; deletes those 8 vars before every
  test. Sits alongside the existing hermeticity guards (`_no_real_provider_key`, `_default_sandbox_mode`…).
- `tests/unit/test_git_hook_env_hermeticity.py` — NEW. 4 tests incl. the regression test.

**The variable list, and why (AC-1)**

Scrubbed — these make `git -C <tmp_path>` stop meaning `<tmp_path>`:
`GIT_DIR`, `GIT_COMMON_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_PREFIX`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`.

NOT scrubbed (legitimate, and none of them points git at another repository — a blanket `GIT_*` purge
would break real setups): identity (`GIT_AUTHOR_*` / `GIT_COMMITTER_*` — CI images set these so
`git commit` works at all), `GIT_CONFIG_*`, transport/auth (`GIT_SSH_COMMAND`, `GIT_ASKPASS`),
`GIT_EXEC_PATH` (git's own binaries — git itself exports this to hooks), `GIT_TRACE*`.

I captured what git *actually* exports to a pre-push hook rather than trusting the man page, by
installing an env-dumping hook in a scratch clone:

```
# from a WORKTREE:                          # from the MAIN checkout:
GIT_DIR=/…/src/.git/worktrees/wt   <-- !!   GIT_EDITOR=true
GIT_EDITOR=true                             GIT_EXEC_PATH=/opt/homebrew/…/git-core
GIT_EXEC_PATH=/opt/homebrew/…/git-core      GIT_PREFIX=
GIT_PREFIX=
```

That is the whole bug in two columns: **the main checkout exports no `GIT_DIR`; a worktree exports an
absolute one.** Hence "the pre-push hook is unusable *in a worktree*" specifically. It also explains
why the repro in the task description needs care: in the main checkout `git rev-parse --git-dir`
prints a *relative* `.git`, which harmlessly re-resolves inside each test's `tmp_path`, so the repro
passes. Use `--absolute-git-dir` (what a worktree/hook actually exports) and it fails. Note git
exports no `GIT_INDEX_FILE` to pre-push — it doesn't need to: with `GIT_DIR` set, git derives the
index as `$GIT_DIR/index`, which is how the index gets written.

**Tests**
- Unit: 1594 passing, 0 failing (`make unit-tests` / `make pre-commit`).
- Integration: 119 passing, 1 failing — the out-of-scope Docker OOM flake (below).

**Acceptance criteria**
- [x] Autouse fixture in `tests/conftest.py` — `_scrub_git_hook_env`.
- [x] The `GIT_DIR` repro passes — 7 failed → 25 passed (evidence below).
- [x] Regression test pins the scrubbing — `test_a_polluted_git_hook_environment_does_not_break_a_git_test`.
      **Verified by actually removing the fixture**, not by asserting it exists: with the scrub body
      disabled the test goes red; restored, green.
- [x] `git push` from a worktree through the REAL pre-push hook, no `--no-verify` — proven end-to-end
      against a scratch bare remote (evidence below). Negative control first: **20 failed, push
      rejected**; same worktree with the fix: **unit tests Passed, branch landed**.
- [ ] `make ci` green — 1713 passed / 1 failed, the out-of-scope Docker flake. See Notes.

**Evidence**

The repro, before and after (`--absolute-git-dir` = what a worktree/hook exports):
```
# BEFORE the fix
$ GIT_DIR=$(git rev-parse --absolute-git-dir) uv run pytest tests/unit/decode/sandbox/test_workspace.py -q
7 failed, 18 passed in 1.58s

# AFTER the fix
$ GIT_DIR=$(git rev-parse --absolute-git-dir) uv run pytest tests/unit/decode/sandbox/test_workspace.py -q
25 passed in 1.35s

# AFTER, with the full hook environment, across both affected files
$ GIT_DIR=… GIT_INDEX_FILE=… GIT_WORK_TREE=… GIT_PREFIX= uv run pytest \
    tests/unit/decode/sandbox/test_workspace.py tests/unit/decode/tui/test_app_e2e.py -q
53 passed in 3.32s
```

The regression test fails if the fixture is removed (the check the AC actually asks for):
```
# scrub body disabled in tests/conftest.py
$ uv run pytest tests/unit/test_git_hook_env_hermeticity.py -q
FAILED …::test_a_polluted_git_hook_environment_does_not_break_a_git_test
1 failed, 3 passed

# fixture restored
$ uv run pytest tests/unit/test_git_hook_env_hermeticity.py -q
4 passed in 2.78s
```

AC-4, the real one — a real `git push`, from a real worktree, through the real pre-push hook, no
`--no-verify`. Negative control (pristine worktree, no fix):
```
$ git -C <worktree> push <scratch-bare-remote> HEAD:refs/heads/negative-control
FAILED tests/unit/decode/sandbox/test_handback.py::…            (11 failures)
FAILED tests/unit/decode/sandbox/test_workspace.py::…           ( 7 failures)
FAILED tests/unit/decode/tui/test_app_e2e.py::…                 ( 2 failures)
20 failed, 1570 passed in 114.34s
make: *** [unit-tests] Error 1
error: failed to push some refs to '…/remote.git'
```
Same worktree, fix applied:
```
$ git -C <worktree> push <scratch-bare-remote> HEAD:refs/heads/with-fix
format check.............................................................Passed
lint check...............................................................Passed
unit tests...............................................................Passed
 * [new branch]      HEAD -> with-fix
```
The 20-failure blast radius matches the task's "~20 failures" exactly. Scope caveat: this used a
throwaway *scratch* remote (I have no commit on `fix/scrub-git-hook-env` yet — Tester goes first), so
the push to `origin` at review time is still the final confirmation. What is proven is the part that
was broken: the **hook** now passes from a worktree.

`make ci`:
```
$ make ci
uv lock --check           → Resolved 155 packages (OK)
make format-check         → 183 files already formatted
make lint-check           → All checks passed!
make test                 → 1 failed, 1713 passed in 507.94s
FAILED tests/integration/test_docker_executor.py::test_filesystem_persists_but_cd_and_export_do_not
```

**Notes**

- **The `make ci` failure is the out-of-scope Docker OOM flake, not this change.** Evidence: the log
  line is `[sandbox] git+gh install failed (exit 137…)` — `exit 137` = OOM-kill, the exact signature
  the task pre-declares out of scope; it hit a *different* docker test on the previous full run
  (`test_sandbox_teardown.py`) than on this one, i.e. it moves around under memory pressure; and it
  **passes in isolation** (`1 passed in 8.36s` after a `docker container prune`). The test never
  shells out to git, so the scrub cannot reach it. Everything `make ci` gates that this change *can*
  affect is green: lockfile, format, lint, and all 1594 unit tests.
- **Fixture ordering — verified, not assumed (the constraint in the task).** `monkeypatch.setenv` in a
  test body runs *after* every autouse fixture, so a test that deliberately points git somewhere still
  wins over the scrub. I pinned this rather than trusting it:
  `test_a_test_may_still_set_a_git_var_itself`. Separately, I grepped the suite: **no existing test
  sets any of these 8 variables**, so nothing regresses today; the test exists to protect the future one.
- **The regression test needs a subprocess, deliberately.** An in-process `assert "GIT_DIR" not in
  os.environ` passes *trivially* from a clean shell — it would not fail if the fixture were deleted, so
  it would not be a regression test. So the driver re-creates the hook environment and re-runs a canary
  test through the real `tests/conftest.py` in a nested pytest (~3s). The pollution points at a
  **throwaway victim repo** under `tmp_path`, never at the developer's checkout — a regression test for
  an index-corrupting bug must not be able to corrupt the index of whoever runs it. It also asserts the
  victim repo is untouched afterwards (no stray commit, nothing staged).
- **I reproduced the repo corruption the task suspected — on this checkout, live.** Running the
  pre-fix repro (and the negative-control push) let leaked-`GIT_DIR` tests write into *this* repo:
  `README.md` from a test's `tmp_path` got staged into my real index, and `.git/config` was rewritten
  with `core.bare = true` (from a test's `git init --bare`) plus a `[user]` / `[commit]` block —
  `git status` then died with *"this operation must be run in a work tree"*. A worktree **shares
  `.git/config`** with the main checkout, which is why the damage crosses over. I repaired it all:
  mixed `git reset` (index), `core.bare=false`, removed the injected `[user]`/`[commit]` sections
  (identity is back to `iusztinpaul <p.e.iusztin@gmail.com>`; the injected values were `t` /
  `t@decode.local`, and global signing was unset, so removing `commit.gpgsign=false` is a no-op).
  No commits or work were lost, and `git status` / `git worktree list` are normal again. **This
  strongly corroborates the task's 252-staged-deletions theory** — the mechanism is confirmed, and
  the sandbox tests' own docstring ("configured **locally**, never touching the developer's global
  config") is exactly the assumption a leaked `GIT_DIR` breaks: under it, "locally" *is* the
  developer's repo. Heads-up for whoever else has a worktree open: their `.git/config` may carry the
  same injected block.
- No src/ change, no new dependency, no ADR/glossary impact (test-harness hermeticity only).

### [Tester] 2026-07-14 12:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 183 files; `ruff check` all passed; `make pre-commit` → 1594 passed in 117.08s, 0 warnings)
- Unit tests: 1594 passed / 0 failed
- Integration tests: 120 passed / 0 failed (`make integration-tests`, after `docker container prune -f`; includes `test_docker_executor.py` and `test_sandbox_teardown.py` green this run — the OOM flake did not reproduce once memory pressure was cleared, corroborating the SWE's "passes in isolation" claim)
- `uv lock --check`: Resolved 155 packages, OK
- Warnings: 0

**E2E adversarial pass** (all reproductions done in a THROWAWAY scratch clone under the scratchpad, never against this checkout — see Safety note below)
- Happy path — repro on a scratch clone of this repo at HEAD f1e1fe3 (pre-fix): `GIT_DIR=$(git rev-parse --absolute-git-dir) uv run pytest tests/unit/decode/sandbox/test_workspace.py -q` → `7 failed, 18 passed in 0.75s`, exactly matching AC-2 and the SWE's evidence (PASS — bug reproduced independently)
- Break path 1 (fix verification, same scratch clone with the fix files copied in): same command → `25 passed in 1.16s` (PASS)
- Break path 2 (regression test with the fixture genuinely disabled): edited `tests/conftest.py` in THIS working tree, replacing the scrub loop body with `pass`, ran `uv run pytest tests/unit/test_git_hook_env_hermeticity.py -q` → `1 failed, 3 passed` (`test_a_polluted_git_hook_environment_does_not_break_a_git_test` failed with a `CalledProcessError` from `git commit` inside the polluted subprocess — the canary genuinely breaks). Restored the fixture body, re-ran → `4 passed in 3.01s`, `git diff -- tests/conftest.py` back to the SWE's original patch (PASS — this is a real regression test, not a tautology)
- Break path 3 (boundary: nonexistent / empty `GIT_DIR`): `GIT_DIR=/nonexistent/path/.git uv run pytest tests/unit/decode/sandbox/test_workspace.py -q` → `25 passed`; `GIT_DIR="" uv run pytest ... -q` → `25 passed` (PASS — `monkeypatch.delenv(raising=False)` handles both cleanly, no crash)
- Break path 4 (ordering: does a test's own `setenv` still win): ad-hoc test in `tests/unit/` asserting ambient `GIT_DIR=/should/be/scrubbed` is absent, then `monkeypatch.setenv("GIT_DIR", ...)` inside the test body still reads back the deliberate value → `1 passed`, file removed after (PASS — confirms AC's ordering claim independently, not by reading the SWE's test)
- Break path 5 (AC-4, real pre-push hook, real worktree, no `--no-verify`) — done entirely inside the scratch clone, never against this repo: committed the fix into the scratch clone, `uv run pre-commit install --hook-type pre-push`, `git worktree add ../scratch-wt` (worktree of the *scratch* repo, exports its own absolute `GIT_DIR` under `.../scratch-src/.git/worktrees/...`), `git push <scratch-bare-remote> HEAD:refs/heads/with-fix` (no `--no-verify`) → `format check...Passed / lint check...Passed / unit tests...Passed`, branch landed (PASS). Negative control: same mechanism against the scratch clone at pre-fix HEAD f1e1fe3 → `20 failed, 1570 passed`, push **rejected** — exact match to the SWE's claimed blast radius. One of those 20 failures is worth flagging: the `[handback]` warning in that run reads `could not push decode/7cbe90d1: error: failed to push some refs to '/Users/pauliusztin/.../building-a-coding-agent-from-scratch-course'` — a leaked-`GIT_DIR` handback test in the polluted worktree attempted to push directly at **this real checkout's path** (because the scratch clone's `origin` remote is this repo, from `git clone <local path>`). It failed only because git refuses to push into the checked-out branch of a non-bare repo (`receive.denyCurrentBranch`) — not because of any protection in the test suite. This is a live, first-hand demonstration of exactly the corruption mechanism the task describes, and it strongly justifies both the fix and the scratch-only reproduction discipline used here.
- Adversarial: fixture cost — timed the 8-var `os.environ.pop` scrub 1594 times in isolation: 5.31ms total (3.33 µs/call) — not measurable against a 117s suite (PASS, no perf regression)
- Adversarial: git-hook-export capture, done independently (not trusting the SWE's or the man page's list) — installed an env-dumping `pre-push` hook in a scratch repo: main checkout exports `GIT_EDITOR`, `GIT_EXEC_PATH`, `GIT_PREFIX` only (no `GIT_DIR`); a `git worktree` exports those plus an **absolute `GIT_DIR`** — confirms the SWE's "worktree vs main checkout" claim exactly. Also captured a `pre-commit` hook from the same worktree, which additionally exports `GIT_INDEX_FILE` (derived from `GIT_DIR`) — corroborates the 252-staged-deletions mechanism the task describes. Cross-checked the full `GIT_*` variable surface via `man git` (troff, stripped with `col -b`): nothing in the "repo/index/worktree/object-store redirect" class is missing from the 8-var list; `GIT_CEILING_DIRECTORIES` / `GIT_DISCOVERY_ACROSS_FILESYSTEM` limit discovery rather than redirect it, and are not exported to hooks in the captures above, so they're correctly left out.
- Adversarial: `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` safety — grepped the whole repo (`Makefile`, `.github/`, `.pre-commit-config.yaml`, all `*.py`) for any code setting these; none found. They redirect which *config file* git reads, not which *repository* it operates on, so even if a CI image set them, `git -C <tmp_path>` still targets `<tmp_path>`'s repo — correctly out of the scrub list, matching the SWE's reasoning.
- Adversarial: `make ci` docker-flake adjudication — read both `test_docker_executor.py` and `test_sandbox_teardown.py` in full; neither references `git` anywhere in source (`grep -in git` on both files: no matches — only `docker` subprocess calls). The scrub touches `os.environ` for every test via an autouse fixture, but these two files have no code path that can observe it. Ran the full integration suite myself after a `docker container prune -f` and got 120/120 green, including both of those files — consistent with the SWE's "passes in isolation" claim and with the flake being memory-pressure-dependent, not caused by this change.

**Safety note (repo protection during reproduction)**
All pre-fix reproductions (7-failed repro, negative-control push through the real hook) were run against a **throwaway `git clone`** of this repo under the scratchpad (`/private/tmp/.../scratchpad/reprodir/scratch-src`) and worktrees of *that* clone — never against this checkout, and no `GIT_DIR` was ever pointed at this repo's `.git`. Repo health verified before, during (after the near-miss push described in break path 5), and after the full review:
```
$ git config core.bare        → false
$ git rev-parse HEAD          → f1e1fe303ec84433d9d7b033a813ad9db2d09e4a (f1e1fe3, unchanged)
$ git config user.name        → iusztinpaul
$ git config user.email        → p.e.iusztin@gmail.com
$ git status                  → clean except the SWE's own uncommitted work (tasks/111, tests/conftest.py modified; tests/support/git_env.py, tests/unit/test_git_hook_env_hermeticity.py untracked)
$ git branch -a | grep -i "negative-control\|with-fix"  → none (no leakage of scratch branches into this repo)
```

**Acceptance criteria**
- [x] PASS — Autouse fixture in `tests/conftest.py` removes git's hook variables for every test — `_scrub_git_hook_env` at `tests/conftest.py:30-52`, `GIT_HOOK_ENV_VARS` (8 vars) at `tests/support/git_env.py:32-41`; independently verified the variable list is complete for the redirect-class bug (see Adversarial notes above) and the not-scrubbed list is safe.
- [x] PASS — `GIT_DIR=$(git rev-parse --absolute-git-dir) uv run pytest tests/unit/decode/sandbox/test_workspace.py -q` passes — reproduced 7 failed/18 passed pre-fix and 25 passed post-fix myself on a scratch clone (break paths 1-2 above). Note: the task's literal command uses `--git-dir` not `--absolute-git-dir`; the SWE's log correctly explains why (`--git-dir` prints a relative path in the main checkout that harmlessly re-resolves) and I confirmed this too — `--absolute-git-dir` is the faithful repro of what a worktree/hook actually exports.
- [x] PASS — Regression test pins the scrubbing — `test_a_polluted_git_hook_environment_does_not_break_a_git_test` in `tests/unit/test_git_hook_env_hermeticity.py:100-140`; verified myself by disabling the fixture body in this working tree and watching it go red, then restoring it green (break path 2).
- [x] PASS — `git push` from a worktree succeeds through the real pre-push hook, no `--no-verify` — reproduced end-to-end on a scratch clone + scratch worktree + scratch bare remote (break path 5): fix → hook passes, branch lands; pre-fix negative control → 20 failed/1570 passed, push rejected, exact match to the SWE's claimed blast radius.
- [ ] Left unticked — `make ci` green — genuinely 1 failure (Docker OOM flake), confirmed out-of-scope: neither affected test file references `git`, both are unaffected by this change, and the flake did not reproduce in my own full integration run after a container prune. Matches the SWE's honest self-assessment; this checkbox is correctly left open per the task's own "Out of scope" section (pre-existing in the task before implementation, not an SWE excuse).

**Evidence**
```
$ make format-check
uv run ruff format --check
183 files already formatted

$ make lint-check
uv run ruff check
All checks passed!

$ make pre-commit   (includes make unit-tests)
======================= 1594 passed in 117.08s (0:01:57) =======================

$ make integration-tests   (after docker container prune -f)
======================= 120 passed in 436.50s (0:07:16) ========================

$ uv lock --check
Resolved 155 packages in 3ms
```

**Other issues found**
- None. Code is well-typed, documented, has no `print()` calls, no unrelated files in the diff (`git diff --stat` touches only `tasks/111-...md` and `tests/conftest.py`, plus the two new `tests/support/git_env.py` and `tests/unit/test_git_hook_env_hermeticity.py` files). `git diff --stat` confirms no `src/` changes, matching the "test-harness hermeticity only" claim.
- Minor observation (not a defect): `tests/unit/test_git_hook_env_hermeticity.py`'s regression test uses `-p no:randomly -p no:cacheprovider` for the nested subprocess but does not pin `-p no:cov`/other third-party plugins some environments might auto-load; not a real risk here since the suite's own plugin set is small and stable, and the test passed reliably across three independent runs (SWE's, my disabled-fixture run, my restored run).

**VERDICT: PASS**

### [SWE] 2026-07-14 12:40 — AC-5 closed, commit + push

**`make ci` is green — AC-5 ticked on my own run, not on someone else's word.**
```
$ docker container prune -f && make ci
uv lock --check    → Resolved 155 packages in 3ms
make format-check  → 183 files already formatted
make lint-check    → All checks passed!
make test          → 1714 passed in 475.48s (0:07:55)
CI_EXIT=0
```
Zero failures, exit 0. The earlier 1-failure run was the pre-declared Docker OOM flake (`exit 137`)
under memory pressure: it clears with a `docker container prune` and it cannot be caused by this
change — neither `test_docker_executor.py` nor `test_sandbox_teardown.py` references `git` at all,
so the scrub has no code path to reach them. The Tester reached 120/120 integration independently the
same way. That is a known infra fragility, out of scope here (see "Out of scope"), and worth its own
task rather than a note buried in this one.

**AC-4, finally proven for real.** The SWE and Tester evidence for AC-4 was a *scratch* remote, because
there was no commit on `fix/scrub-git-hook-env` to push yet. The commit below was pushed to `origin`
with **no `--no-verify`** — the real pre-push hook ran the full unit suite from this checkout and
passed. That push, not the scratch rehearsal, is the actual proof.

**Notes**
- All 5 acceptance criteria now ticked. No `src/` change, no new dependency, no ADR/glossary impact.
