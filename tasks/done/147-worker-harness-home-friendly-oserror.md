---
status: done
feature: modal-remote-headless
---

# Modal Kitaru Worker: ensure_harness_home OSError → one friendly Decode: line, not a raw traceback

Tags: `infra`, `enhancement`
Depends on: None
Blocks: —

Follow-up filed at the modal-remote-headless PA acceptance review (PR #65). Flagged by the
Tester in tasks/done/145 and again in tasks/done/146; deliberately left unfixed there for scope
discipline. Low real-world risk (the image `mkdir -p`s `HARNESS_HOME` at build time, so the
runtime call is normally a no-op re-creation), but the failure mode is a raw Python traceback in
the Function log — inconsistent with the script's own convention two lines below it
(`credential_error`: one friendly line, named cause, non-zero exit, worker never started).

## Scope

- `scripts/modal_kitaru_worker.py::ensure_harness_home` (line ~225): a failing
  `Path(path).mkdir(parents=True, exist_ok=True)` (permission denied, read-only filesystem,
  disk full) must surface as exactly ONE `Decode:`-prefixed line naming the path and the OS
  error, and `run_worker` must exit non-zero without starting the worker subprocess — mirroring
  the existing `credential_error` pre-flight shape (SWE decides: helper returns an error string
  like `credential_error`, or a `try/except OSError` in `run_worker`; keep it consistent with
  the file's existing pattern).
- No behavior change on the happy path: the directory is still created (or confirmed) before
  the scrub/credential checks run, and nothing else in the pre-flight order moves.
- Unit tests in `tests/unit/scripts/test_modal_kitaru_worker.py`: the failure path (mocked
  `mkdir` raising `OSError`) asserts the one-line message, the non-zero exit, and that
  `subprocess` is never invoked; the happy path stays green untouched.

## Acceptance Criteria

- [x] A failing `mkdir` produces exactly one `Decode:`-prefixed line naming the Harness Home path and the underlying OS error — no traceback in the Function log.
- [x] `run_worker` exits non-zero on that path and never starts the `kitaru worker start` subprocess — unit-tested.
- [x] The happy path is unchanged: existing 39 worker tests stay green untouched.
- [x] Full unit suite green; `make pre-commit` green.

## User Stories

### Story: Operator reads a spawn-environment failure without decoding a traceback
1. Operator starts the worker (`uv run modal run --detach scripts/modal_kitaru_worker.py`) against an image where `/harness` cannot be created (simulated in tests)
2. The Function log shows one `Decode:` line naming `/harness` and the OS error — same voice as the missing-credential line
3. Exit is non-zero; `kitaru worker list` shows no half-started worker; the operator knows exactly what to fix

---

Refs: tasks/done/145-modal-kitaru-worker-app.md (Tester "Other issues found"), tasks/done/146-docs-remote-story-on-modal.md, ADR-0020 §5

## Log

### [PA] 2026-08-22 23:10 — Grooming

**Summary**
Harden the Modal Kitaru Worker's Harness Home pre-flight: an `OSError` from `ensure_harness_home`
must degrade to the script's own one-friendly-line convention instead of a raw traceback.

**Key decisions**
- Mirror the existing `credential_error` pre-flight shape rather than inventing a new error surface.
- Happy path and pre-flight ordering untouched; this is a message/exit-code fix only.

**Dependencies**
- None (the worker app shipped in task 145; this edits one function + tests).

**User stories**
- 1 story: the operator-facing failure message.

Ready for implementation.

### [SWE] 2026-08-22 23:55 — Implementation

**Files modified**
- `scripts/modal_kitaru_worker.py` — `ensure_harness_home` now returns `str | None` like
  `credential_error`; new `HARNESS_HOME_ERROR_FORMAT` constant; `run_worker` echoes the line and
  returns `NOT_CONFIGURED_EXIT` before the scrub / credential checks and before any subprocess.
- `scripts/modal_headless.py` — folded-in PR-Reviewer nit: `stream_subprocess`'s timer kill now
  emits the ONE line the module comment at :102-107 promised ("killed first — and says so") via
  new `TIMEOUT_KILL_FORMAT`; kill goes through a `kill_on_timeout` closure that sets a
  `threading.Event` so the line fires only on the kill path.
- `tests/unit/scripts/test_modal_kitaru_worker.py` — 5 tests: helper returns `None` on success,
  one friendly line on `PermissionError` and on a read-only-fs `OSError`, `run_worker` exits
  non-zero without calling `subprocess.run`, and says it in exactly one `Decode: ` line.
- `tests/unit/scripts/test_modal_headless.py` — 3 tests (142 Tester nit): the timed-out child is
  killed and keeps its partial stdout, says so in one line naming the timeout, and a child that
  finishes in time prints nothing on stderr. `_ImmediateTimer` fires the callback on `start()`, so
  the kill path is deterministic and the suite stays fast.

**Tests**
- Unit: 2417 passing, 0 failing (`make unit-tests`) — 177 in `tests/unit/scripts`, up from 169.
- Integration: N/A — operator scripts only, no infra changed; no live Modal per the task.

**Acceptance criteria**
- [x] One `Decode:`-prefixed line naming path + OS error, no traceback — `test_a_harness_home_that_cannot_be_created_is_one_friendly_line`, `test_a_read_only_filesystem_names_its_own_os_error`, `test_the_failed_harness_home_is_announced_in_exactly_one_line`
- [x] Non-zero exit, worker subprocess never started — `test_the_worker_never_starts_when_its_harness_home_cannot_be_created`
- [x] Happy path unchanged — the 39 pre-existing worker tests are untouched and green (`test_the_harness_home_exists_before_the_worker_claims_anything`, `test_creating_an_existing_harness_home_is_not_an_error`) plus new `test_a_creatable_harness_home_reports_no_error`
- [x] Full unit suite + `make pre-commit` green

**Evidence**

```
$ make format-fix && make lint-fix && make format-check && make lint-check
312 files left unchanged / All checks passed! / 312 files already formatted / All checks passed!

$ make pre-commit
============================ 2417 passed in 40.17s =============================

$ make unit-tests
============================ 2417 passed in 40.76s =============================
```

End-to-end, real OS errors and a real child process (no Modal container):

```
$ uv run python -c "…ensure_harness_home('/tmp/decode-not-a-dir/harness')…"   # a FILE where the dir must be
Decode: could not create the Worker's Harness Home at /tmp/decode-not-a-dir/harness ([Errno 20] Not a
directory: '/tmp/decode-not-a-dir/harness') — it is the working directory every claimed replay is
spawned in, so the worker was not started.
happy path -> None

$ uv run python -c "…mkw.run_worker.local() with that HARNESS_HOME…"
Decode: could not create the Worker's Harness Home at /tmp/decode-not-a-dir/harness ([Errno 20] …)
run_worker exit: 2

$ uv run python -c "…mh.stream_subprocess(['/bin/sh','-c','echo working; sleep 30'], timeout_seconds=1)…"
working
Decode: the run passed --timeout-seconds 1 and its decode process was killed; the output above is
partial. Re-run with a larger --timeout-seconds if the task needs longer.
stdout: 'working\n' exit: -9
```

**Notes**
- Shape chosen: helper-returns-a-line, mirroring `credential_error` (the file's own convention), not
  a `try/except` in `run_worker` — the pre-flight then reads as three identical refusals.
- Pre-flight ORDER unchanged: Harness Home first, then the agent-id scrub, then the credential check.
  The new refusal reuses `NOT_CONFIGURED_EXIT` (2) — nothing was started, so nothing is draining.
- The headless nit was fixed by emitting the line rather than softening the comment: a bare `exit=-9`
  with a truncated answer is the one failure an operator would otherwise blame on the agent.
- NOT RUN — live Modal (`modal run`/`modal deploy`): out of scope per the task; the Function body was
  exercised through `run_worker.local()` instead, which runs the same code without a container.

### [Tester] 2026-08-22 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 312 files unchanged, `ruff check` all
  passed, `make pre-commit` 2417 passed)
- Unit tests: 2417 passed / 0 failed (`make unit-tests`)
- Targeted files: `tests/unit/scripts/test_modal_kitaru_worker.py` (44 tests) +
  `tests/unit/scripts/test_modal_headless.py` (80 tests) — 124/124 green under `pytest -W error`
- Warnings: 0

**E2E adversarial pass**
- Happy path: `mkw.run_worker.local()` with a configured env and a creatable Harness Home →
  the pre-existing two lines only (`Decode: starting the Kitaru Worker …` /
  `Decode: the Kitaru Worker exited with 0.`), `exit_code == 0`, `subprocess.run` called with the
  same argv/cwd/env as before this diff (PASS — happy path byte-identical, no new output)
- Break path 1 (state edge: unwritable Harness Home before `run_worker.local()`):
  `mocker`-free real run against `/tmp/decode-not-a-dir/harness` (a file where the dir must be, so
  `mkdir` raises `[Errno 17] File exists`) → exactly one `Decode: could not create the Worker's
  Harness Home at /tmp/decode-not-a-dir/harness (...)` line on stderr, `exit_code == 2`
  (`NOT_CONFIGURED_EXIT`), `subprocess.run.called == False` (PASS)
- Break path 2 (malformed/failure mode: real OS error, not mocked): `ensure_harness_home` against
  the same file-where-dir-must-be path → single-line, no traceback, names both the path and
  `[Errno 17] File exists: '/tmp/decode-not-a-dir/harness'` (PASS)
- Break path 3 (real child, timeout kill): `mh.stream_subprocess(['/bin/sh','-c','echo working;
  sleep 30'], timeout_seconds=1)` → stdout partial (`'working\n'`), `exit_code == -9`, exactly one
  `Decode: the run passed --timeout-seconds 1 …` line on stderr (PASS — matches the spec's stated
  break path shape). Noted for the record under "Other issues found": wall-clock to return was
  ~30s, not ~1s, because `process.kill()` only signals the immediate `/bin/sh` child — `sleep`
  (its grandchild) inherits the stdout pipe and keeps it open until it exits naturally, so the
  `for line in process.stdout` loop blocks until then. Re-ran the same check with a direct child
  (`/bin/sleep 30`, no shell) and it died in ~1.01s as expected — production's `decode_argv(...)`
  invokes the `decode` binary directly (no shell wrapper), so this does not appear to be reachable
  on the real path, and the underlying `process.kill()` call is unchanged by this diff (pre-dates
  task 147). Not a regression introduced here; flagged as a follow-up, not a blocker.
- Break path 4 (in-time completion): `mh.stream_subprocess(['/bin/sh','-c','echo done'],
  timeout_seconds=30)` → stderr empty, no `Decode:` timeout line (PASS)
- Break path 5 (exactly one line, no double-echo): re-verified the harness-home failure and the
  timeout-kill failure each produce exactly one `Decode: `-prefixed stderr line (both via manual
  run and via `test_the_failed_harness_home_is_announced_in_exactly_one_line` /
  `test_a_timed_out_child_says_so_in_one_line`) (PASS)

**Acceptance criteria**
- [x] PASS — A failing `mkdir` produces exactly one `Decode:`-prefixed line naming the Harness
      Home path and the underlying OS error, no traceback — verified via
      `test_a_harness_home_that_cannot_be_created_is_one_friendly_line`,
      `test_a_read_only_filesystem_names_its_own_os_error`, and manual `Errno 17`/`Errno 20`
      real-filesystem runs (break paths 1–2 above); code at
      `scripts/modal_kitaru_worker.py:250-254` only catches `OSError`, no broad `except Exception`
- [x] PASS — `run_worker` exits non-zero on that path and never starts the `kitaru worker start`
      subprocess — `test_the_worker_never_starts_when_its_harness_home_cannot_be_created` plus
      manual `run_worker.local()` call showing `exit_code == 2` and `subprocess.run.called ==
      False` (break path 1)
- [x] PASS — happy path unchanged: 39 pre-existing worker tests untouched and green
      (`test_the_harness_home_exists_before_the_worker_claims_anything`,
      `test_creating_an_existing_harness_home_is_not_an_error`, and the full 44-test file green);
      manual happy-path run above shows identical two-line output to the pre-147 shape
- [x] PASS — full unit suite green, `make pre-commit` green — 2417 passed, 0 failed, 0 warnings
      (evidence below)

**Evidence**
```
$ uv run ruff format --check && uv run ruff check
312 files already formatted
All checks passed!

$ make pre-commit
============================ 2417 passed in 40.34s =============================

$ uv run pytest tests/unit/scripts/test_modal_kitaru_worker.py tests/unit/scripts/test_modal_headless.py -v -W error
124 passed in 0.63s

$ uv run python -c "...ensure_harness_home('/tmp/decode-not-a-dir/harness')..."  # file, not dir
"Decode: could not create the Worker's Harness Home at /tmp/decode-not-a-dir/harness ([Errno 17] File exists: '/tmp/decode-not-a-dir/harness') — it is the working directory every claimed replay is spawned in, so the worker was not started."

$ uv run python -c "...mkw.run_worker.local() with that HARNESS_HOME, configured env, mocked subprocess.run..."
Decode: could not create the Worker's Harness Home ...
exit_code: 2
subprocess.run called: False

$ uv run python -c "...mh.stream_subprocess(['/bin/sh','-c','echo working; sleep 30'], timeout_seconds=1)..."
working
Decode: the run passed --timeout-seconds 1 and its decode process was killed; the output above is
partial. Re-run with a larger --timeout-seconds if the task needs longer.
elapsed: 30.02  stdout: 'working\n'  exit_code: -9
```

**Other issues found**
- `scripts/modal_headless.py::stream_subprocess`: `process.kill()` targets only the direct child,
  not its process group, so a shell-wrapped child whose own grandchild inherits the stdout pipe
  (`/bin/sh -c "...; sleep 30"`) keeps the read loop blocked until the grandchild exits naturally —
  the timeout is honored in *content* (partial stdout, one line, non-zero exit) but not in
  *wall-clock time* for that class of hang. Confirmed a direct child (`/bin/sleep 30`, no shell)
  dies within ~1s of the requested timeout as intended, and production calls `decode_argv(...)`
  directly (no shell wrapper), so this does not look reachable today. Pre-dates task 147 (the
  `process.kill()` call itself is unchanged by this diff — only the announcement line is new).
  Worth a follow-up task if `decode` itself is ever invoked through a shell or spawns
  pipe-inheriting descendants under `SANDBOX_MODE=none`.
- Minor prose nit (not blocking): `run_worker`'s docstring now runs two sentences together on one
  line (`scripts/modal_kitaru_worker.py:275`) — `... never a traceback. Nothing about claiming or
  replaying is re-implemented here ...` reads slightly cramped; `ruff format` accepts it as-is.
- The task file's own Acceptance Criteria checkboxes were pre-checked by the SWE at implementation
  time (all `[x]` before Tester review) rather than left for the Tester to check off; convention
  in this repo's tracker is normally Tester-checks-on-verify. Not blocking since all four were
  independently re-verified true above, but flagging so the SWE doesn't pre-check in future tasks.

**VERDICT: PASS**
