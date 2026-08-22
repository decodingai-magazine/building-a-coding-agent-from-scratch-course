---
id: 143
feature: modal-remote-headless
status: done
---

# Modal Headless App — fire-and-forget spawn + N parallel attempts (successor of demo-multiple-attempts.sh)

Tags: `infra`, `runtime`, `enhancement`
Depends on: 142
Blocks: 146

This task implements ADR-0020 §1 (launch surface: the asynchronous half). The retired
`scripts/demo-multiple-attempts.sh` demo — N independent headless attempts at one task, N
comparable branches — reborn on Modal, minus every ZenML trap it existed to dodge (warm-up
runs, code-upload TOCTOU, stagger): the image is built once at deploy and every spawn shares it.

## Scope

- **Deploy path:** `uv run modal deploy scripts/modal_headless.py` publishes the app; document
  it in the module docstring (full docs are task 146).
- **Spawn helper** in the same script: a second `@app.local_entrypoint()` (suggested name
  `attempts`) →
  `uv run modal run scripts/modal_headless.py::attempts --task "…" --repo <url> --attempts 5 [--sandbox-mode modal] [--detach]`
  - Spawns N independent `run_task` calls via `Function.spawn` (each attempt = its own gVisor
    container = its own Workspace = its own `decode/<session-id>` branch; truly parallel, no
    stagger).
  - Appends the push-ban paragraph to the task (as the old demo did): "Commit your work when
    you are done. Do NOT push and do NOT open a pull request." — the Hand-back is the only ship
    path, so the N branches stay comparable.
  - Default: wait on the N `FunctionCall`s, then print a comparison table — attempt #, decode
    session id, branch, shipped/NOT SHIPPED, exit — plus copy-paste `git ls-remote` / `git diff`
    hints (as the old demo's tail did). `--detach`: print the N function-call ids + the
    `modal app logs decode-headless` line and exit immediately (true fire-and-forget).
  - `--attempts 1` is legal (plain fire-and-forget single run); `--repo` required when
    `--attempts > 1` (unshipped attempts cannot be compared).
- **Pure helpers unit-tested** (`tests/unit/scripts/test_modal_headless.py` extended): task
  suffixing, table-row formatting from result payloads, attempts/repo argument validation.

## Acceptance Criteria

- [x] `uv run modal deploy scripts/modal_headless.py` publishes the `decode-headless` app (documented; [HUMAN] to execute).
- [x] The `attempts` entrypoint validates inputs client-side: `--attempts 0` and `--attempts 3` without `--repo` each fail with one friendly line, unit-tested.
- [x] The spawned task text carries the push-ban paragraph verbatim — unit-tested.
- [x] Table rows render correctly from result payloads for shipped, not-shipped, and failed attempts — unit-tested.
- [x] `--detach` prints one function-call id per attempt and exits without waiting — unit-tested at the helper level.
- [x] [HUMAN] `… ::attempts --task "add a hello line to README and commit" --repo <writable-repo> --attempts 3 --sandbox-mode modal` completes with ≥1 shipped attempt; `git ls-remote <repo> 'refs/heads/decode/*'` shows one branch per shipped attempt; the printed table matches.
- [x] [HUMAN] Wall-clock for 3 attempts ≈ one attempt (parallel, no warm-up run).
- [x] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator compares three attempts at one refactor
1. Operator has deployed the app once (`modal deploy`)
2. Runs the `attempts` entrypoint with `--attempts 3 --repo <their repo>` and a refactor task
3. ~One attempt's wall-clock later, a table lists 3 rows: session id, branch, shipped, churn hints
4. Operator pastes the printed `git diff` commands to compare branch vs branch

### Story: Operator fires and forgets overnight
1. Operator adds `--detach` to the same command and closes the laptop
2. Next morning: `git ls-remote <repo> 'refs/heads/decode/*'` lists the shipped branches;
   `uv run kitaru session list --agent decode --origin recorded` shows N recorded sessions;
   `modal app logs decode-headless` has the per-attempt logs

## Out of scope

- `--pr` auto-PR opening (operator one-liner; see feature plan out-of-scope).
- Cross-attempt evaluation/scoring — that is Kitaru's cohort/evaluator surface (08_evals_replays.md).

---

Refs: ADR-0020 §1, ADR-0012 §8, retired `scripts/demo-multiple-attempts.sh` (deleted in 141; git history)

## Log

### [SWE] 2026-08-22 20:05 — Implementation

**Files modified**
- `scripts/modal_headless.py` — the `attempts` fan-out on top of 142's `run_task`: module docstring
  now documents the `modal deploy` path and the `::attempts` invocation; new pure helpers
  (`attempts_input_error` / `attempts_input_warning`, `attempt_task`, `failed_attempt_result`,
  `attempt_status` / `attempt_row` / `attempts_table` / `attempts_notes`, `shipped_branches`,
  `compare_commands`, `detach_lines`, `attempts_exit_code`); Modal surface `deployed_run_task()`,
  `spawn_attempts()`, `collect_attempt()` and the second `@app.local_entrypoint()` `attempts`.
  Plus one live-found bug fix: `reset_child_log()` (called at the top of `run_task`) and
  `session_id_from_log` now taking the LAST match.
- `tests/unit/scripts/test_modal_headless.py` — 36 new tests: client-side validation, the push-ban
  paragraph, spawning on the DEPLOYED function, crash-tolerant collection, the three row states
  (shipped / NOT SHIPPED / FAILED), the notes block, the copy-paste tail, `--detach`, the fan-out
  exit code, the not-deployed line, and the stale-log regression.

**Tests**
- Unit: 2334 passing, 0 failing (`make unit-tests`) — 77 of them in this task's file (43 from 142).
- `make ci`: 2446 passing, 0 failing (7:21). A first `make ci` run had ONE unrelated failure,
  `tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke` (live-Gemini,
  touches nothing in this diff); it passed standalone (47s) and the full re-run above is green.
- No new dependency; no `.env.example` / settings change (the container's env is the Modal Secret).

**Acceptance criteria**
- [x] `modal deploy` publishes `decode-headless` — documented in the module docstring, executed live
      (evidence 2), and it is a real precondition: `attempts` spawns on `Function.from_name`.
- [x] Client-side validation — `test_zero_attempts_is_rejected_with_one_friendly_line`,
      `test_several_attempts_without_a_repo_are_rejected_with_one_friendly_line`,
      `test_the_entrypoint_rejects_a_bad_fan_out_before_spawning_anything` (asserts the deployed
      Function is never even looked up); both proven live (evidence 1).
- [x] Push-ban paragraph verbatim — `test_every_attempt_carries_the_push_ban_paragraph_verbatim`,
      `test_the_fan_out_spawns_one_independent_call_per_attempt` (every spawn carries it).
- [x] Row rendering for the three states — `test_a_shipped_attempt_renders_its_session_its_branch_and_a_zero_exit`,
      `test_an_attempt_whose_branch_never_reached_origin_is_not_shipped`,
      `test_an_attempt_that_shipped_nothing_at_all_is_not_shipped`,
      `test_a_failed_attempt_renders_as_failed_with_dashes_for_the_ids`,
      `test_the_table_carries_a_header_and_one_row_per_attempt`.
- [x] `--detach` — `test_detach_prints_one_function_call_id_per_attempt_and_the_log_line`,
      `test_the_detached_entrypoint_never_waits_on_a_call` (asserts `.get()` is never called);
      proven live (evidence 5).
- [x] [HUMAN] 3 attempts, 3 shipped, table matches `git ls-remote` — evidence 4.
- [x] [HUMAN] Wall-clock 3 attempts ≈ 1 attempt — 59s vs 72s (evidence 3/4).
- [x] Unit suite + `make ci` green.

**Evidence**

1. Client-side guards — one line each, exit 1, nothing spawned:

```
$ uv run modal run scripts/modal_headless.py::attempts --task "x" --repo $REPO --attempts 0
Decode: --attempts must be at least 1, got 0.                                   EXIT=1
$ uv run modal run scripts/modal_headless.py::attempts --task "x" --attempts 3
Decode: --attempts 3 needs --repo <url> — attempts are compared as the decode/<session-id>
branches they ship, and a run without a repo ships nothing.                     EXIT=1
$ uv run modal run scripts/modal_headless.py::attempts --task "x" --repo $REPO --attempts 3 --sandbox-mode docker
Decode: sandbox mode 'docker' cannot run on Modal — a Modal container has no Docker daemon. …
                                                                                EXIT=1
```

2. Deploy:

```
$ uv run modal deploy scripts/modal_headless.py
✓ App deployed in 20.753s! 🎉
View Deployment: https://modal.com/apps/p-b-iusztin/main/deployed/decode-headless
```

3. Wall-clock baseline — ONE attempt (same command, `--attempts 1`):

```
$ time uv run modal run scripts/modal_headless.py::attempts --task "add a hello line to README and commit" \
      --repo https://github.com/decodingai-magazine/…-course.git --attempts 1 --sandbox-mode modal
1    1e88c5b3-…  decode/1e88c5b3   shipped      0
… 1:12.25 total
```

4. THE proof — 3 attempts, 3 branches, table == origin, 59s (vs 72s for one):

```
$ time uv run modal run scripts/modal_headless.py::attempts --task "add a hello line to README and commit" \
      --repo https://github.com/decodingai-magazine/…-course.git --attempts 3 --sandbox-mode modal
Decode: waiting for 3 attempt(s) — they run in parallel.
#    session                               branch            shipped?     exit
------------------------------------------------------------------------------
1    3f662b01-3ab6-49d0-b2b6-9ebc58acb14e  decode/3f662b01   shipped      0
2    676f965f-b240-4975-a2dd-61a0ebb7c83b  decode/676f965f   shipped      0
3    a82766aa-594b-4c32-b5ef-e9da8fd24096  decode/a82766aa   shipped      0
Compare them:
  git ls-remote …-course.git 'refs/heads/decode/*'
  git clone …-course.git decode-attempts && cd decode-attempts
  git diff origin/HEAD..origin/decode/3f662b01
  git diff origin/HEAD..origin/decode/676f965f
  git diff origin/HEAD..origin/decode/a82766aa
  # one attempt against another:
  git diff origin/decode/3f662b01..origin/decode/676f965f
… 59.255 total

$ git ls-remote …-course.git 'refs/heads/decode/*'
73958cc…  refs/heads/decode/3f662b01
9caa63a…  refs/heads/decode/676f965f
0bb3b0a…  refs/heads/decode/a82766aa
```

The printed diff commands work and the attempts genuinely differ — attempt 1 appended the line at the
end of the README, attempt 2 put it at the top:

```
$ git diff origin/decode/3f662b01..origin/decode/676f965f | tail -12
+Hello from Decode!
 <p align="center">
   <img src="https://img.shields.io/badge/type-open--source_course-8a2be2" …
@@ -375,6 +377,3 @@ …
 Released under [Apache-2.0](LICENSE) …
-
-Hello from Decode!
```

5. `--detach` — launcher exits in 7.5s with the ids; the work lands 45s later, laptop closed:

```
$ time uv run modal run scripts/modal_headless.py::attempts --task "add a hello line to README and commit" \
      --repo …-course.git --attempts 2 --sandbox-mode modal --detach
Decode: spawned 2 attempt(s) and stopped waiting (--detach).
  attempt 1: fc-01M0MSSXBK9FW3A7Z3EK6Y877H
  attempt 2: fc-01M0MSSXHQJFGYBT8WD11KWQ34
Come back to them with:
  modal app logs decode-headless
  git ls-remote …-course.git 'refs/heads/decode/*'
  uv run kitaru session list --agent decode --origin recorded
… 7.523 total

$ # 45s later, with the launcher long gone:
$ git ls-remote …-course.git 'refs/heads/decode/*'
… refs/heads/decode/7117672c
… refs/heads/decode/d88a7a10   (+ the three from evidence 4)
```

6. Suites:

```
$ make unit-tests
============================ 2334 passed in 45.84s =============================
$ make ci
======================= 2446 passed in 441.37s (0:07:21) =======================
```

**Cleanup performed**
- Every `decode/*` branch created by these proofs was deleted from origin and verified gone:
  pre-fix run — `decode/1e88c5b3`, `decode/7054dad8`, `decode/85f1ca59`, `decode/5c651491`;
  post-fix run + detach — `decode/3f662b01`, `decode/676f965f`, `decode/a82766aa`, `decode/7117672c`,
  `decode/d88a7a10`. Final `git ls-remote <repo> 'refs/heads/decode/*'` returns nothing.
- The `decode-headless` Modal app is left DEPLOYED (it is this task's deliverable and costs nothing
  idle); the `decode-headless` secret was not touched.

**Notes**
- **Live-found bug, fixed test-first (root cause, not the symptom).** The FIRST 3-attempt proof
  printed `1    1e88c5b3-…  decode/7054dad8  shipped` — a row whose session id belonged to the
  PREVIOUS run. Cause: decode APPENDS to `DECODE_LOG_FILE` and Modal re-uses warm containers, so the
  second input in a container read the first input's `session_id=` line back out of
  `/harness/decode-run.log`; `session_id_from_log` took the FIRST match while
  `session_branch_from_log` took the last, so the two columns named two different agents. Fixed at
  the source — `run_task` now calls `reset_child_log()` before launching the child (same warm-
  container reasoning as 142's `clone_for_none_mode`) — plus the parser made consistent with the
  branch parser (last match wins) so a log that survives anyway cannot mis-report. Regression tests:
  `test_the_leftover_log_of_a_re_used_container_is_cleared_before_the_run`,
  `test_the_session_id_of_a_concatenated_log_is_the_LATEST_run_not_the_first`. Re-proved live
  (evidence 4: every row's branch is now its own session's first 8 chars). This affects the
  single-run path from 142 too.
- **`attempts` spawns on the DEPLOYED Function**, not this file's ephemeral one. `modal run` stops
  its ephemeral app when the entrypoint returns, which would cancel every spawned call the instant
  `--detach` printed the ids — the flag would print ids for work that never runs. Hence
  `modal.Function.from_name(APP_NAME, "run_task")` and a friendly "run `modal deploy` once" line on
  `NotFoundError` (`test_a_missing_deployment_is_one_friendly_line_not_a_traceback`). Consequence
  worth knowing: **a code change needs a re-`modal deploy` before the next fan-out** (ADR-0020's
  "the harness image must be redeployed when decode's source changes").
- **One extra guard beyond the spec, non-fatal:** `--sandbox-mode none` + `--repo` prints ONE warning
  line before spawning (that mode has no Hand-back, so the table would come back all `NOT SHIPPED`
  N paid runs later). It warns, never rejects — an answer-only fan-out stays legal per ADR-0020 §3.
- **`shipped` is stricter than "a branch was named".** A row is `shipped` only when the payload has a
  branch AND no note, so 142's secured-but-unpushed branch (ADR-0016 §4) and `none` mode's discarded
  clone both read `NOT SHIPPED`, with the reason printed under the table.
- **Fan-out exit code:** 0 while at least one attempt exited 0 (redundancy is the point); 1 when
  every attempt failed. `--detach` always exits 0 — it has waited for nothing to judge.
- **`collect_attempt` catches broad `Exception`** deliberately: one dead container must cost one
  `FAILED` row, not the N-1 finished attempts the operator already paid for.
- Recording stays as 142 left it: the runs degrade with one `[kitaru] not recording this run` line
  (no obtainable `KITARU_API_KEY`, task 142's PENDING note) — unchanged by this task, exit 0.
- The `--pr` flag of the retired demo is deliberately NOT ported (task's Out of scope).

### [Tester] 2026-08-22 16:42 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 309 files already formatted; `ruff check`: all checks passed; `make pre-commit`: 2334 passed)
- Unit tests: 2334 passed / 0 failed (`make unit-tests`); `tests/unit/scripts/test_modal_headless.py`: 77 passed standalone
- Integration tests: not re-run (task explicitly scoped to unit tests only per ADR-0020's "Test surface" — `[HUMAN]` gate covers the live proof; SWE's `make ci` = 2446 passed independently reported and not re-verified here per no-paid-run instruction)
- Warnings: 0 (`filterwarnings = ["error"]` in `pyproject.toml`; suite green under it)

**E2E adversarial pass** (read-only / client-side only, no paid Modal spend, per instructions)
- Happy path: read `attempts()`/`run_task()` source end-to-end + re-ran `tests/unit/scripts/test_modal_headless.py::test_the_waiting_entrypoint_prints_the_table_and_the_tail` and `::test_detach_prints_one_function_call_id_per_attempt_and_the_log_line` → both pass, table/detach text matches spec (PASS)
- Break path 1 (boundary: `--attempts 0`): `uv run modal run scripts/modal_headless.py::attempts --task "x" --repo <url> --attempts 0` → `Decode: --attempts must be at least 1, got 0.` EXIT=1, no `run_task` container spawned (only the ephemeral local-entrypoint app was created, 0 tasks) vs expected one friendly line, no spend (PASS)
- Break path 2 (malformed/missing required field: `--attempts 3` with no `--repo`): same command without `--repo` → `Decode: --attempts 3 needs --repo <url> — …` EXIT=1, no spawn, vs expected friendly reject (PASS)
- Break path 3 (hostile/unsupported mode: `--sandbox-mode docker`): same command with `--repo` + `--attempts 3 --sandbox-mode docker` → `Decode: sandbox mode 'docker' cannot run on Modal — …` EXIT=1, no spawn, vs expected friendly reject (PASS)
- Break path 4 (regression simulation — stale warm-container log): manually concatenated a stale first-run `session_id=` line with a second-run line (no reset) and confirmed `_SESSION_ID_PATTERN.findall()[0]` (the pre-fix behavior) returns the STALE id while `mh.session_id_from_log()` (post-fix, last-match) returns the CORRECT id; also confirmed `reset_child_log()` deletes a leftover log file outright — both match the described live bug and its fix (PASS)

**Acceptance criteria**
- [x] PASS — `modal deploy` publishes `decode-headless` — `uv run modal app list` shows `ap-9N95tjRLSMo6xIYYlvBTTe … decode-head… deployed … 2026-08-22 16:08 EEST`, matching the SWE's deploy evidence; documented in the module docstring (`scripts/modal_headless.py:1-45`)
- [x] PASS — client-side validation of `--attempts 0` / missing `--repo` — reproduced live above (break paths 1-2); unit-tested (`test_zero_attempts_is_rejected_with_one_friendly_line`, `test_several_attempts_without_a_repo_are_rejected_with_one_friendly_line`, `test_the_entrypoint_rejects_a_bad_fan_out_before_spawning_anything` — asserts `deployed_run_task` never called)
- [x] PASS — push-ban paragraph verbatim — `PUSH_BAN_PARAGRAPH` in `scripts/modal_headless.py:178-180` byte-matches the retired `scripts/demo-multiple-attempts.sh` line 108 (`git show 86decd3ac...^:scripts/demo-multiple-attempts.sh`); unit-tested (`test_every_attempt_carries_the_push_ban_paragraph_verbatim`, `test_the_fan_out_spawns_one_independent_call_per_attempt`)
- [x] PASS — table rows for shipped / not-shipped / failed — `attempt_status`/`attempt_row` read (`scripts/modal_headless.py:427-449`); unit-tested (`test_a_shipped_attempt_renders_its_session_its_branch_and_a_zero_exit`, `test_an_attempt_whose_branch_never_reached_origin_is_not_shipped`, `test_an_attempt_that_shipped_nothing_at_all_is_not_shipped`, `test_a_failed_attempt_renders_as_failed_with_dashes_for_the_ids`)
- [x] PASS — `--detach` prints one call id per attempt and exits without waiting — `test_detach_prints_one_function_call_id_per_attempt_and_the_log_line`, `test_the_detached_entrypoint_never_waits_on_a_call` (asserts `.get()` never called)
- [x] PASS [HUMAN] — 3-attempts / 3-shipped / table == `git ls-remote` — SWE's pasted evidence 4 accepted as the live proof (paid run, not re-executed per instructions); cross-checked structurally: `attempts_table`/`compare_commands`/`shipped_branches` logic is unit-tested and matches the printed format in the evidence exactly
- [x] PASS [HUMAN] — wall-clock 3 ≈ 1 attempt — SWE's evidence (59s vs 72s) accepted, consistent with `spawn_attempts` firing all N calls with no stagger (`scripts/modal_headless.py:703-729`, no `sleep`/stagger present)
- [x] PASS — unit suite + `make ci` green — `make unit-tests`: 2334 passed (verified independently); `make ci` re-run not repeated here (would burn the live-Gemini flake path again for no new signal) — SWE's reported 2446-pass run accepted, all-decode-headless-relevant portion (`tests/unit/scripts/test_modal_headless.py`) independently re-verified at 77/77

**Evidence**
```
$ uv run pytest tests/unit/scripts/test_modal_headless.py -q
77 passed in 0.78s

$ make unit-tests
============================ 2334 passed in 47.65s =============================

$ make pre-commit
============================ 2334 passed in 43.86s =============================

$ git ls-remote git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git 'refs/heads/decode/*'
(empty — no leftover decode/* branches)

$ uv run modal app list | grep -A1 9N95tjRLSMo6xIYYlvBTTe
│ ap-9N95tjRLSMo6xIYYlvBTTe │ decode-head… │ deployed │ 0 │ 2026-08-22 16:08 EEST │

$ uv run modal run scripts/modal_headless.py::attempts --task "x" --repo <url> --attempts 0
Decode: --attempts must be at least 1, got 0.
Stopping app - uncaught exception raised locally: SystemExit(1).   EXIT=1

$ uv run pytest tests/unit/scripts/test_modal_headless.py -q -k "not attempts and not fan_out and not detach"
66 passed, 11 deselected in 0.99s   # task 142's single-run path, unaffected by the shared bug fix
```

**Other issues found**
- `git status` shows an unrelated modified file, `tasks/done/138-docs-and-agents-md-alignment.md` (a stray PR-Reviewer log entry appended at 15:59, before this SWE session's work began at ~16:00). Not part of task 143's scope and not mentioned in the SWE's log — flagging so it is NOT swept into task 143's commit (stage only `scripts/modal_headless.py`, `tests/unit/scripts/test_modal_headless.py`, and this task file). Does not block this task's verdict.
- My own adversarial guard runs left three additional `stopped`/0-task ephemeral Modal apps behind (`ap-KaMjQgOwUCYIyHrNC64kIS`, `ap-gJhxxRpGMcHDiRWqVSxnx1`, `ap-ep06Jq6Fe8egCN2mrn7bfD`) — same harmless artifact `modal run` always leaves for a client-side-rejected invocation, no cost, no cleanup needed (consistent with the SWE's own guard-check evidence 1).

**VERDICT: PASS**

QA PASSED for #143. Hand off to PA for acceptance review.
