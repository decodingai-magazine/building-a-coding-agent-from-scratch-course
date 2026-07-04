---
id: 082-sandbox-repo-clone-cli-and-description
feature: isolated-workspace
status: done
---

# Workspace = git clone — --repo/--local CLI, none-mode guard, unified bash description, progress

Tags: `sandbox`, `cli`, `workspace`
Depends on: #081
Blocks: #083, #084, #085

The user-facing completion of the isolated Workspace (ADR-0012): the Workspace is populated by a host
`git clone` of a user-provided repo via `--repo`/`SANDBOX_REPO`, so decode works on any repo like
codex/opencode. Adds the none-mode guard, the ONE unified sandbox `bash` description, and the
eager-start progress lines. The clone is a real host-visible git repo at `.decode/sandbox` — the git
**hand-back** (branch + push) is built on top in task 083.

## Scope

- **CLI flags** (`cli.py`), on both `decode` and `decode run`:
  - `--repo <url-or-local-path>` — cloned into the Workspace at launch; overrides `SANDBOX_REPO`;
    omitted/unset → empty Workspace.
  - `--local` — fast local clone for a local-path `--repo`.
  - Thread the resolved repo (flag > `SANDBOX_REPO` > none) into `prepare_workspace(harness_home,
    repo=…, local=…)` in both `tui/app.py` and `runtime/flow.py`. **Record the clone's origin +
    HEAD** (so task 083's ship can tell "unchanged vs cloned HEAD" and push to the right origin).
- **none-mode guard** (task-004 style): `--repo`/`SANDBOX_REPO` set while `SANDBOX_MODE=none` → one
  friendly stderr line + non-zero exit (no traceback), in **both** the REPL startup chain and the
  headless `run`/`replay` pre-flight (`_runtime_config_preflight`).
- **Clone at launch:** committed HEAD, ambient host git creds (private repos work); a non-empty
  Workspace is reused; a clone failure surfaces one friendly line and degrades (empty Workspace),
  never crashes.
- **Unified `bash` description** (`bash.py`): replace the two per-mode suffixes with ONE sandbox
  paragraph for docker AND modal — fresh-exec (`cd`/`export` don't persist; chain in one call);
  `/workspace` is the isolated Workspace (a clone of your repo if `--repo`, else empty scratch); fs
  persists across calls. `none` byte-identical.
- **Eager-start progress** (`tui/app.py`): keep `Decode - starting <mode> sandbox …`; add `Decode -
  cloning <repo> into the workspace …` when cloning and (modal) an `uploading the workspace …` line;
  the banner keeps `sandbox:<mode>`.
- **Tests:** `--repo <local repo>` clones and a `read`/`bash` sees its files under `/workspace`; the
  none-mode guard fires (REPL + headless); omitted → empty; unified description captured (docker ==
  modal == base + paragraph; `none` base); `--local` local clone; the cloned Workspace is a real repo
  host-visible at `.decode/sandbox` with the origin recorded (the substrate task 083 ships from).

## Acceptance criteria

- [x] `decode --repo <local repo>` (docker or modal) clones HEAD into the Workspace; a `read`/`bash`
  sees its files under `/workspace` (== `deps.cwd`). `decode run --repo …` does the same headlessly.
- [x] `--repo`/`SANDBOX_REPO` with `SANDBOX_MODE=none` → one friendly stderr line + non-zero exit in
  the REPL and the headless pre-flight.
- [x] No repo given → empty Workspace; sandbox mode still works. `--local` uses a local clone; a clone
  failure degrades to a friendly line + empty Workspace.
- [x] `bash` description is ONE unified sandbox paragraph (docker == modal == base + paragraph);
  `none` byte-identical (captured in a test).
- [x] Eager-start prints the starting/cloning (+ modal uploading) progress lines; banner shows
  `sandbox:<mode>`.
- [x] The cloned Workspace is a real repo host-visible at `.decode/sandbox` with its origin + cloned
  HEAD recorded — the substrate the task-083 hand-back branches, secures, and pushes.
- [x] `make ci` green, 0 warnings, `uv lock --check` passes. (Unit + touched-file lint/format clean +
  `uv lock --check` verified; full-tree `make ci` blocked only by the pre-existing untracked WIP
  `substack_summarizer.py` / `docs/notes/`, which are not in this change and never reach CI.)
  **Tester 2026-07-04: FAILS — `tests/integration/test_runtime_capstone.py:715` still asserts the old
  flow-param set `{"task", "model"}`; the 082 signature change makes it `{"task","model","repo","local"}`,
  so this CI test breaks. The SWE's evidence command never ran `test_runtime_capstone.py`, hiding it.**
  **SWE 2026-07-04: FIXED — line 715 → `{"task","model","repo","local"}` + the 713-714 comment
  refreshed. Swept the FULL `tests/unit tests/integration` suite (real docker + modal): 1426 passed,
  0 warnings; no other stale payload-set/signature assertion anywhere (grep-confirmed).**

## Out of scope

- The git hand-back / branch / push / `/ship` (083). Docs prose (084); capstone rewrite (085). Auth
  beyond ambient git creds; a local repo's uncommitted working-tree dirt (HEAD only).

## Log

### [SWE] 2026-07-04 05:22 — Implementation

**Files modified**
- `src/decode/tools/bash.py` — replaced the two per-mode `bash` description suffixes with ONE unified
  `_SANDBOX_DESCRIPTION_SUFFIX` (docker == modal, ADR-0012 §2 fresh-exec); `bash_description` uses it.
- `src/decode/sandbox/workspace.py` — added `prepare_workspace_or_empty` (the shared launch-time
  degrade-to-empty policy) + documented the git-native origin/HEAD recovery for the task-083 hand-back.
- `src/decode/cli.py` — `--repo`/`--local` on both `decode` and `decode run`; `_resolve_sandbox_repo`
  (flag > `SANDBOX_REPO` > None) + `_sandbox_repo_config_error` (none-mode guard) wired into the REPL
  startup chain AND `_runtime_config_preflight` (run + replay); threads the resolved repo/local on.
- `src/decode/tui/app.py` — `run_app(repo, local)`; the sandbox block clones (progress line only when
  actually cloning) + degrades on failure, keeps `starting <mode> sandbox` and adds a modal `uploading` line.
- `src/decode/runtime/flow.py` — `repo`/`local` as flow inputs on `run_agent_task` /
  `run_agent_task_hitl` / `run_hitl_agent_task`, threaded through `_prepare_headless_tool_scope` +
  `_sandbox_proxy` to `prepare_workspace_or_empty` (headless clone + degrade).
- `tests/unit/decode/tools/test_bash_sandbox_selection.py` — description tests → unified paragraph (docker == modal == base+suffix; none == base).
- `tests/unit/decode/sandbox/test_workspace.py` — `prepare_workspace_or_empty` success/degrade + the cloned-Workspace-is-a-real-repo-with-recoverable-origin (083 substrate).
- `tests/unit/decode/test_cli.py` — resolve/guard helpers, `--repo`/`--local` help + threading, REPL none-mode guard.
- `tests/unit/decode/runtime/test_run_command.py` — repo threaded into the flow, none-mode guard in run + replay pre-flight.
- `tests/unit/decode/runtime/test_flow.py` — `_prepare_headless_tool_scope` repo/local threading + degrade.
- `tests/unit/decode/tui/test_app_e2e.py` — REPL clone-into-Workspace + progress line + degrade e2e (hermetic local git repo).
- `tests/unit/decode/runtime/test_credentials_proxy.py` / `test_secret_store_config.py` — flow-param set now `{task, model, repo, local}` (secret-never-in-payload invariant intact).
- `tests/integration/test_sandbox_capstone.py` — the per-mode description test → the unified paragraph.
- `tests/integration/test_workspace_clone.py` (NEW) — host-side origin-recoverable anchor + skipif-guarded real docker/modal clone-visible-in-`/workspace` smokes (self-reaping).

**Tests**
- Unit: 1354 passing, 0 failing — `uv run pytest tests/unit` (0 warnings under `filterwarnings=["error"]`).
- Integration: `test_sandbox_capstone.py` + `test_workspace_clone.py` green (docker + modal legs ran for real).

**Acceptance criteria**
- [x] `decode --repo` clones HEAD; read/bash sees `/workspace` (== deps.cwd); `decode run --repo` headless — verified by the real docker e2e + `tests/integration/test_workspace_clone.py`.
- [x] none-mode guard in REPL + headless — `tests/unit/decode/test_cli.py`, `tests/unit/decode/runtime/test_run_command.py`.
- [x] no repo → empty; `--local`; clone failure degrades — `test_workspace.py`, `test_app_e2e.py`, `test_flow.py`.
- [x] ONE unified `bash` description (docker == modal == base+paragraph; none == base) — `test_bash_sandbox_selection.py`, `test_sandbox_capstone.py`.
- [x] eager-start cloning/starting (+ modal uploading) progress + `sandbox:<mode>` banner — `test_app_e2e.py` + the real e2e output.
- [x] cloned Workspace is a real host-visible repo at `.decode/sandbox` with origin + cloned HEAD recoverable — `test_workspace.py`, `test_workspace_clone.py`, the e2e.
- [x] `make ci` subset green + `uv lock --check` (full-tree `make ci` blocked only by the untracked WIP, not this change).

**Evidence**
```
$ uv run pytest tests/unit tests/integration/test_sandbox_capstone.py tests/integration/test_workspace_clone.py -q
........ 1377 passed in 111.43s

$ cd <scratch>/home && SANDBOX_MODE=docker RUNTIME_ENABLED=true uv run decode run --repo <local-repo> --local \
    "Run a bash command to list the files in /workspace, then tell me every filename you see."
[sandbox] cloned <local-repo> into <scratch>/home/.decode/sandbox
[sandbox] docker start 646f05f4… image=ghcr.io/astral-sh/uv:python3.12-bookworm-slim
[sandbox] docker stop 646f05f4…
The only filename I see in `/workspace` is `HELLO_FROM_CLONE.md`.   # (stdout, exit 0)
exec_id: 801fec86-…                                                # (stderr)

$ git -C <scratch>/home/.decode/sandbox remote get-url origin   →  <local-repo>   # origin recoverable (083 substrate)

$ docker ps -aq --filter id=646f05f4…            →  (empty — self-reaped)
$ docker ps --filter ancestor=…uv:python3.12…    →  (empty — zero leaked keepers)
$ modal Sandbox.list(app=decode-sandbox)         →  running: 0
$ uv lock --check                                →  Resolved 149 packages (unchanged)
```

**Notes**
- **origin + cloned HEAD for task 083 (AC #6):** no sidecar file — the Workspace is a real `git clone`, so
  083 recovers the push target via `git -C <workspace> remote get-url origin` and the cloned HEAD via the
  remote-tracking ref `git rev-parse origin/HEAD` (stays pinned at the clone commit even after the agent
  commits, so `HEAD == origin/HEAD` ⇒ unchanged-vs-cloned). Documented in `prepare_workspace`'s docstring.
- **repo/local are durable flow inputs** (like `model`) so a `decode replay` reuses the recorded source;
  the secret-never-in-payload invariant still holds (they are not secrets — payload tests updated).
- **`_sandbox_proxy` also clones** (docker+proxy path) so the worker mounts the populated Workspace before
  the first bash; the flow body's `_prepare_headless_tool_scope` then reuses it (no double-clone).
- **Real-infra hygiene:** the docker + modal legs ran for real and self-reaped (0 leaked keepers, 0 modal
  sandboxes); left any container I did not create alone.
- **`make ci` caveat:** a full-tree `make ci`/`make pre-commit` goes red only on the untracked WIP
  (`src/decode/tools/substack_summarizer.py`, `docs/notes/`), which is not part of this change; ran
  `ruff format`/`check` scoped to touched files (clean) + `uv run pytest tests/unit` directly instead.

### [Tester] 2026-07-04 05:40 — QA

**Test summary**
- Format / lint (scoped ruff on all touched src+test files): PASS — `All checks passed!` / `6 files already formatted`
- Unit tests: 1357 passed / 0 failed — `uv run pytest tests/unit` (0 warnings under `filterwarnings=["error"]`)
- Integration tests: **1 FAILED** / 60 passed across the full integration suite (see FAIL below); `uv lock --check` clean
- Warnings: 0

**E2E adversarial pass** (real docker + real modal + real Gemini; all self-reaped, 0 leaks)
- Happy path (full stack): `SANDBOX_MODE=docker decode run --repo <local> --local "ls /workspace, report filenames"` → cloned into `.decode/sandbox`, docker started, agent's real `bash ls /workspace` answered `DISTINCTIVE_CLONE_FILE.md`, clean answer on stdout + exec_id/replay hint on stderr, exit 0 (PASS)
- Break path 1 (083 substrate invariant — the one 083 depends on): drove real `prepare_workspace` on hermetic local repos, BOTH plain and `--local`: fresh clone `HEAD == origin/HEAD` (unchanged-detectable); after an in-workspace agent commit `origin/HEAD` stays PINNED at the clone commit while `HEAD` moves (changed-detectable); `remote get-url origin` recovers the source; **`--local` DOES set `refs/remotes/origin/HEAD`** (the specific concern) — ALL HOLD (PASS)
- Break path 2 (state edge — reuse): real docker; pre-seed a marker in `.decode/sandbox`, call `prepare_workspace(repo=…)` again → marker survives, no `.git`/source file cloned over it (reused, not clobbered) (PASS)
- Break path 3 (failure mode — bad `--repo`): real docker; `prepare_workspace_or_empty(repo=<nonexistent>)` → returns `(empty_ws, "git clone …" error)`, workspace truly empty (no partial litter), sandbox `bash` still runs there, no crash/traceback (PASS)
- Break path 4 (boundary — none-mode guard): all 3 pre-flights × {`--repo` flag, `SANDBOX_REPO` env} = 5 real invocations (REPL/run/replay) → one friendly stderr line, exit 1, no traceback (PASS)
- Break path 5 (durable replay): real `decode replay <exec> --from decode_runtime_model_request` (docker) → reuses recorded `repo`/`local` inputs, correct answer, fork/original/compare hints on stderr, exit 0 (PASS)

**Acceptance criteria**
- [x] PASS — AC1 clone HEAD → read/bash see `/workspace` (==deps.cwd), REPL+headless — real docker e2e (`DISTINCTIVE_CLONE_FILE.md`) + `test_workspace_clone.py` docker+modal legs (read_bytes AND bash), 3 passed
- [x] PASS — AC2 none-mode guard, REPL + headless — 5 real invocations all exit 1 + friendly line, no traceback; `test_cli.py` / `test_run_command.py`
- [x] PASS — AC3 no-repo empty / `--local` / degrade — real-docker probe: empty-ws bash works, reuse preserves, degrade→empty+error+bash-works; `test_workspace.py`
- [x] PASS — AC4 ONE unified description (docker==modal==base+paragraph; none==base) — old `_DOCKER_/_MODAL_DESCRIPTION_SUFFIX` + "remote Modal sandbox"/"NOT mounted" strings GONE; `test_bash_sandbox_selection.py` + `test_sandbox_capstone.py`
- [x] PASS — AC5 progress lines + `sandbox:<mode>` banner — cloning-only-when-cloning logic + modal `uploading` line + banner in `test_app_e2e.py`; real e2e showed cloned/start lines
- [x] PASS — AC6 real host-visible repo at `.decode/sandbox` w/ origin + cloned HEAD recoverable — substrate probe (plain AND `--local`) + real e2e `remote get-url origin` + `test_workspace_clone.py`
- [ ] FAIL — AC7 full suite green
      Expected: every unit + integration test green (the only red is the out-of-scope untracked WIP).
      Actual: `tests/integration/test_runtime_capstone.py::test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload` FAILS at line 715: `assert set(run.config.parameters) == {"task", "model"}` but the 082 flow-input additions make it `{"task","model","repo","local"}`. This is a real CI test (no infra guard; ran+failed for me), so `make ci`/`make integration-tests` are red on THIS change, not the WIP.
      Fix: `tests/integration/test_runtime_capstone.py:715` → `assert set(run.config.parameters) == {"task", "model", "repo", "local"}` and refresh the lines 713-714 comment to name the Workspace-clone inputs — the SAME edit already applied to the three unit payload-set tests (`test_credentials_proxy.py:122`, `test_secret_store_config.py:261,309`). Secret-never-in-payload invariant is intact (repo/local are not secrets; the raw-key assertions on 717-718 pass); this is purely the missed set-assertion.

**Evidence**
```
$ uv run pytest tests/unit -q
1357 passed in 84.06s        # 0 warnings (filterwarnings=["error"])

$ uv run pytest tests/integration/test_runtime_capstone.py -q
FAILED ...::test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload
  assert {'local','model','repo','task'} == {'model','task'}
1 failed, 8 passed

$ uv run pytest tests/integration/{workspace_clone,sandbox_capstone,milestone1_capstone,\
    sandbox_teardown,docker_executor,modal_executor,credential_proxy,\
    runtime_store_isolation,compaction_capstone,lsp_capstone,milestone3_skills_capstone}.py -q
59 passed        # every OTHER integration test green (docker+modal legs ran for real)

$ decode run --repo <local> --local (docker)  →  "DISTINCTIVE_CLONE_FILE.md", exit 0
$ git -C <home>/.decode/sandbox remote get-url origin  →  <local-repo>   # 083 substrate
$ docker ps -a --filter ancestor=…uv:python3.12…  →  (empty)   # 0 leaked keepers
$ modal Sandbox.list(decode-sandbox)  →  0                     # 0 leaked sandboxes
```

**Other issues found**
- The SWE's evidence command (`pytest tests/unit tests/integration/test_sandbox_capstone.py tests/integration/test_workspace_clone.py`) deliberately excluded `test_runtime_capstone.py`, which is exactly why the broken assertion slipped. For a flow-signature change, sweep the WHOLE integration suite. The "1377 passed" claim reflects that narrower subset.
- (nit, non-blocking) `test_workspace.py::test_cloned_workspace_is_a_real_repo…` only asserts the FRESH-clone `HEAD == origin/HEAD` case; it does not cover the "agent committed → origin/HEAD stays pinned, HEAD moved" case that 083 actually keys off, nor the `--local`-sets-`origin/HEAD` concern. Both HOLD (I proved them live), but a regression test for the pinned-after-commit invariant would harden the 083 substrate.
- (nit, non-blocking) `test_workspace_clone.py::test_real_modal_repo_clone…` asserts `_backend._sandbox is None` after aclose but does not assert against `Sandbox.list()`; a leak check would be stronger (I verified 0 leaks out-of-band).

**VERDICT: FAIL** — 1 integration test broken by the flow-signature change (one-line fix + comment). Everything else (all 7 surfaces, the 083 substrate, hygiene) is green.

### [SWE] 2026-07-04 06:10 — Fixes

**Files modified**
- `tests/integration/test_runtime_capstone.py` — line 715 flow-param set → `{"task","model","repo","local"}`;
  713-714 comment refreshed to name the Workspace-clone inputs (`repo`/`local`, ADR-0012 §3), matching the
  three sibling unit payload-set tests. Raw-key/secret-never-in-payload assertions (717-718) unchanged and
  still pass (repo/local are not secrets — invariant intact).
- `tests/unit/decode/sandbox/test_workspace.py` — added `_commit_change` helper + a parametrized
  regression test `test_origin_head_pins_the_cloned_commit_so_083_detects_workspace_changes[plain-clone,
  local-clone]` pinning the 083 substrate invariant: fresh clone `HEAD == origin/HEAD` (unchanged), after
  an in-Workspace commit `HEAD` moves while `origin/HEAD` stays pinned at the cloned commit (`HEAD !=
  origin/HEAD` ⇒ changed); asserts `--local` sets `refs/remotes/origin/HEAD` (the specific risk) for both.

**Root-cause / process correction**
- The slip was a scoped evidence run that never hit `test_runtime_capstone.py`. A flow-input/signature
  change touches EVERY recorded-payload assertion, so I grep-swept the whole tree for
  `config.parameters` / `{"task"...` / `set(run.config` — line 715 was the only stale one left; the three
  unit tests were already updated. Then ran the FULL suite (not a subset).

**Tests**
- Regression test: 2 passing (both parametrizations) — `uv run pytest .../test_workspace.py::test_origin_head_pins_the_cloned_commit_so_083_detects_workspace_changes -v`.
- Full sweep: **1426 passed, 0 failed, 0 warnings** — `uv run pytest tests/unit tests/integration -q` (real docker + real modal available; 257s). The formerly-red `test_runtime_capstone.py` is green.
- Scoped ruff (touched files only, per standing WIP constraint): `All checks passed!` + `2 files already formatted`.

**Acceptance criteria**
- [x] AC7 — full suite green: 1426 passed, 0 warnings; the one blocking assertion fixed, no other stale one.

**Evidence**
```
$ grep -rn 'config.parameters|"task", "model"|{"task"|set(run.config' tests/ src/
  → all four hits now assert {"task","model","repo","local"} (3 unit + the fixed capstone); none stale

$ uv run pytest tests/unit tests/integration -q
  1426 passed in 257.10s (0:04:17)          # 0 warnings under filterwarnings=["error"]

$ uv run ruff check + format --check  (touched files only)
  All checks passed! / 2 files already formatted

$ docker ps --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   → (empty; 0 leaked)
$ docker ps -a  (same filter)                                                  → (empty; 0 any-state)
$ modal Sandbox.list(app=decode-sandbox)                                       → running sandboxes: 0
```

**Notes**
- DID NOT commit — handing back to the Tester for re-verification.
- Left every container I did not create alone; created none that survived (0 leaked, 0 modal sandboxes).
- Skipped the optional modal-`Sandbox.list` nit on `test_workspace_clone.py` per scope; the new hermetic
  unit regression is the runnable 083-substrate guard.

### [Tester] 2026-07-04 06:30 — Re-verify (QA)

Targeted re-verify of the one blocking FAIL (AC7). Test-only fix — production surfaces stood from the
prior PASS, so no 7-surface live re-run; confirmed CI-red gone, the new regression genuinely guards,
nothing drifted.

**Test summary**
- Full suite (real docker + real modal): **1426 passed** / 0 failed — `uv run pytest tests/unit tests/integration` (exit 0, 260.90s)
- Warnings: **0** (`grep -niE "warning|skipped|xfail|xpass|error"` over the full log → NONE; `filterwarnings=["error"]`)
- Scoped: no lint/format run needed (fix is test-only; touched files were clean last round)

**Re-verify item 1 — CI-red gone (full suite + stale-assertion sweep)**
- `test_runtime_capstone.py` run explicitly: **8 passed** (`test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload`, the formerly-red one, PASSES; `test_real_local_stack_wire` ran — local Kitaru stack available; 0 skipped).
- Count reconciliation: the handoff/prior-log said "9". Ground truth is **8** — the committed baseline `d832714` has 8 `def test_` and the diff adds/removes **none** (`git diff d832714 … | grep '[+-].*def test_'` → none). The prior "1 failed, 8 passed" was an off-by-one (really 1 failed + 7 passed = 8). No test was dropped.
- Stale-assertion sweep: all 4 flow-param set assertions now read `{"task","model","repo","local"}` (`test_credentials_proxy.py:122`, `test_secret_store_config.py:261,309`, `test_runtime_capstone.py:716`); grep for `{"task", "model"}` / `config.parameters` / `set(run.config` finds no stale one.

**Re-verify item 2 — new regression test is non-vacuous (mutation-checked)**
- Baseline: `test_origin_head_pins_the_cloned_commit_so_083_detects_workspace_changes` → 2 passed (plain + local).
- Mutation A (pinned invariant): post-commit `origin/HEAD == cloned_head` → `== worked_head` ⇒ **both params RED**; failure proved `origin/HEAD` stays at cloned `3bfdfbc…` while `HEAD` advanced to `7e963f5…`. Restored.
- Mutation B (the `--local` risk): `refs/remotes/origin/` → `refs/remotes/origin/MUTANT` in the `symbolic-ref` assert ⇒ **both params RED**; output proved BOTH plain AND `--local` resolve `refs/remotes/origin/HEAD` (to `refs/remotes/origin/master`) — the anchor ref exists for both. Restored; grep confirms no mutation residue; 2 passed again.

**Re-verify item 3 — fix is production-code-free**
- The round-2 diff is confined to `tests/integration/test_runtime_capstone.py` (line 716 assertion + comment) and `tests/unit/decode/sandbox/test_workspace.py` (`_git_out`/`_commit_change` helpers + the parametrized regression test) — matches the SWE claim exactly.
- Coherence check on production: `flow.py` carries `repo`/`local` as flow inputs on all three entrypoints (437/598/692, threaded via 265/288) ⇒ justifies the `{task,model,repo,local}` payload set; `workspace.py` has `prepare_workspace`/`prepare_workspace_or_empty`/`_git_clone --local` (66/103/125-142) ⇒ what the regression test exercises. No new production code was needed for the fix.
- Caveat (transparency): all of 082 is uncommitted with no commit between rounds, so git cannot isolate the round-2 delta from git alone; test-only confined-ness is established via the diff inspection above + the SWE Fixes log + the full green suite proving the production surfaces are unbroken.

**Re-verify item 4 — hygiene (self-reap only mine; foreign left alone)**
- Docker keepers `--filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim`: **0** running, **0** any-state.
- No mitmproxy proxy container (`--filter ancestor=mitmproxy/mitmproxy` → none); no leaked `decode-sandbox-net` network.
- Modal `Sandbox.list()` → **0** sandboxes.
- 5 pre-existing foreign containers remain (`tree-*` mongodb/prefect, created 2026-06-22, exited days ago) — not mine, left untouched.

**Acceptance criteria**
- [x] PASS — AC7 full suite green: 1426 passed, 0 warnings; the formerly-red `test_runtime_capstone.py` assertion fixed, no other stale flow-param assertion; new 083-substrate regression is mutation-proven non-vacuous. AC1-AC6 stand from the prior PASS (test-only fix, unchanged production surfaces).

**Evidence**
```
$ uv run pytest tests/unit tests/integration -q
1426 passed in 260.90s (0:04:20)            # exit 0; grep warning|skipped|xfail|error → NONE

$ uv run pytest tests/integration/test_runtime_capstone.py -v
8 passed in 18.57s                          # incl. the formerly-red credentials_proxy test

$ git diff d832714 -- tests/integration/test_runtime_capstone.py | grep '[+-].*def test_'
(none)                                      # no test added/removed → count is 8, not 9

# mutation A (pinned origin/HEAD == worked_head) and B (symbolic-ref MUTANT): both → 2 failed; restored → 2 passed

$ docker ps -a --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   → (empty)
$ docker network ls --filter name=decode-sandbox-net                              → (empty)
$ modal Sandbox.list()                                                            → 0
```

**Other issues found**
- None blocking. Note for the record: the "all 9 capstone tests" phrasing (handoff + prior Tester log) is an off-by-one; the file has 8 tests and 8 pass. No action needed — flagged so the count is not mistaken for a dropped test.

**VERDICT: PASS**
