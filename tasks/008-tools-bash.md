---
id: 008-tools-bash
feature: m1-vanilla-agent
status: done
---

# Tools: bash execution

## Scope
Gated shell execution with a local-executor seam (M8 swaps it for a sandbox).

## Acceptance criteria
- [x] `tools/exec.py` defines the executor seam; `tools/bash.py` runs via `asyncio` subprocess.
- [x] `settings.bash_timeout_s` enforced; output truncated via `tools/truncate.py` (2000 lines / 50 KB) with overflow spilled to a temp-file path in the result. (Timeout path now returns the documented partial output — fixed in the SWE Fixes log entry, re-verified by the Tester 2026-06-19 21:40.)
- [x] Gated (no dangerous-command classifier in v1 — human approves every call).

## Out of scope
- Background jobs; OS sandbox / dangerous-command classifier (M8).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Executor seam is the M8 sandbox insertion point.

### [SWE] 2026-06-19 19:30 — Implementation

**Files modified**
- `src/decode/tools/exec.py` — NEW: the executor seam. `CommandExecutor` Protocol
  (`async run(command, *, cwd, timeout_s) -> ExecResult`) + `LocalExecutor`
  (`asyncio.create_subprocess_shell`, `start_new_session=True`). On timeout it SIGTERMs the
  whole process **group** (`os.killpg`), escalates to SIGKILL after a 2s grace, marks
  `timed_out=True`, returns partial output. Infra-agnostic — **this is the M8 sandbox
  insertion point** (swap the `CommandExecutor` impl; `bash` is untouched).
- `src/decode/tools/bash.py` — NEW: the gated `bash(command, timeout?)` tool. Runs via the
  module-level `LocalExecutor` under `ctx.deps.cwd`; gates (`raise ApprovalRequired` when
  `not ctx.tool_call_approved`); `timeout` defaults to / is clamped to `settings.bash_timeout_s`;
  truncates each stream via `tools/truncate.py` (2000 lines / 50 KB, overflow → temp-file path);
  reports exit code + timed-out status to the model. No dangerous-command classifier (the
  human gate is the safety boundary).
- `src/decode/tools/registry.py` — registered `bash` in `TOOL_SPECS` (`read_only=False`).
- `tests/unit/decode/tools/test_exec.py` — NEW: executor contract (stdout/stderr/exit capture,
  cwd honored, shell features, timeout returns `timed_out`, **timeout kills a spawned child**
  with no orphan, undecodable-bytes decode).
- `tests/unit/decode/tools/test_bash.py` — NEW: gating (ApprovalRequired, command not run),
  stdout/stderr/non-zero-exit reporting, cwd honored, timeout + child-kill, default/clamped/
  rejected timeout, truncation + temp-file spill, and a run **through a real agent**
  (`TestModel(call_tools=["bash"])` forcing the gated call, approving resolver, bash result fed
  back to the model).
- `tests/unit/decode/tools/test_registry.py` — added `bash` to the expected tools / read-only
  assertions.

**Tests**
- Unit: 214 passing, 0 failing (`make pre-commit`). New: 22 in test_exec.py + test_bash.py.
- Integration: N/A — no infra changes (the executor uses a local subprocess; no new services).

**Acceptance criteria**
- [x] `tools/exec.py` defines the executor seam; `tools/bash.py` runs via `asyncio` subprocess —
  verified by `tests/.../test_exec.py` (all) and `tests/.../test_bash.py::test_bash_reports_stdout_and_exit_code`.
- [x] `settings.bash_timeout_s` enforced; output truncated via `tools/truncate.py` with temp-file
  overflow — verified by `test_bash.py::test_bash_defaults_timeout_to_settings`,
  `::test_bash_clamps_a_too_large_timeout_to_the_configured_max`,
  `::test_bash_times_out_and_tells_the_model`,
  `::test_bash_truncates_long_output_and_spills_to_a_temp_file`.
- [x] Gated (no classifier; human approves every call) — verified by
  `test_bash.py::test_bash_requires_approval_when_not_approved` and
  `::test_bash_runs_through_the_agent_when_approved`.

**Evidence**
```
$ make pre-commit
... 214 passed in 3.93s ...

$ uv lock --check
Resolved 163 packages in 3ms

# End-to-end (real subprocess, /tmp):
$ bash(echo 'hello from decode')          -> "Exit code: 0.\n\nstdout:\nhello from decode"
$ bash(ls /no/such/dir)                    -> "Exit code: 1.\n\nstderr:\nls: /no/such/dir: No such file or directory"
$ bash(rm -rf /, approved=False)           -> ApprovalRequired (never executed)
$ bash(sleep 30, timeout=0.3)              -> "Command timed out after 0.3s and was terminated (exit code -15). Partial output below." (returned in 0.31s)
$ bash(printf 'a\nb\nc\n' | grep b && echo matched) -> "Exit code: 0.\n\nstdout:\nb\nmatched"

# Timeout kills a spawned child, no orphan:
timed_out: True ; sentinel frozen (6 bytes before AND 0.5s after kill)
$ ps -ax | grep -E "child|sleep 30"        -> none (good)
```

**Notes**
- Executor seam = the **M8 sandbox insertion point** (ADR-0002 §7): swap the `CommandExecutor`
  implementation (Docker/Modal) and `bash` is unaffected. `LocalExecutor` keeps zero
  bash-specific logic.
- Process-group kill (`start_new_session=True` + `os.killpg`) is what stops a backgrounded
  child from outliving the timed-out command (proven by test + e2e).
- `TestModel` synthesises the `command` arg itself, so the through-the-agent test asserts the
  gated-then-executed contract (PermissionRequested fired + `Exit code:` tool-return reached the
  model) rather than a specific command's side effect.
- `.env.example` unchanged: `bash_timeout_s` already exists in `settings` with a default and is
  not env-overridden, matching the existing `max_output_lines` / `web_fetch_timeout_s`
  convention.

### [Tester] 2026-06-19 20:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` all exit 0)
- Unit tests: 214 passed / 0 failed (27 in the bash/exec/registry surface)
- Integration tests: N/A — `tests/integration/` empty; no new infra (local subprocess only). Correct.
- `uv lock --check`: PASS (Resolved 163 packages)
- Warnings: 0 (`filterwarnings=error` would have failed the run otherwise)
- `code-review` plugin enabled (advisory) — folded into the manual checklist below; no extra defects beyond the one found by the adversarial pass.

**E2E adversarial pass** (real LocalExecutor + real `bash` tool through actual subprocesses, 13 probes)
- Happy path: `bash("echo to-out; echo to-err 1>&2; exit 3")` → `Exit code: 3.` + stdout `to-out` + stderr `to-err` (PASS)
- (b) cwd honored: `pwd`/`ls` in a nested tmp tree → resolved to the passed cwd; `uniquefile.txt` listed (PASS)
- (c) timeout kills spawned child (no orphan/zombie): backgrounded child writing its PID every 0.1s, 0.3s timeout → `timed_out=True`, returned in 0.31s, sentinel frozen 18→18B after kill, `os.kill(pid,0)` on the child PID → ProcessLookupError (gone); `ps` shows no leaked processes (PASS)
- (d) truncation + complete spill: 5000-line emitter under the real 2000-line cap → truncated notice + spill file containing both `line-0` and `line-4999` (PASS)
- (e) gating no side effect: unapproved `bash("touch should-not-exist.txt")` → `ApprovalRequired` raised, file absent (PASS)
- (f) timeout clamp + reject: `timeout=10×bash_timeout_s` on a fast command runs fine (clamped); `timeout=0` and `timeout=-5` → `ModelRetry` (PASS)
- (g.1) huge stderr (200k lines): no deadlock (returned in 0.15s, pipe buffer did not fill the executor), truncated + spilled (PASS)
- (g.2) command killed by signal: self-`SIGKILL` → `Exit code: -9`, NOT reported as a timeout (PASS)
- boundary: empty / whitespace-only command → `ModelRetry` (PASS)
- boundary: unicode (`café — 日本語 🐙`) round-trips; raw `\377\376` bytes → replacement char, exit 0, no crash (PASS)
- state: 8 concurrent `bash` calls → each output correct, zero cross-talk/corruption (PASS)
- failure: nonexistent cwd → `FileNotFoundError` (clean raise, no silent wrong-dir run) (PASS)
- **timeout partial output: child flushes `early-line`/`EARLY-OUT` BEFORE the deadline, then sleeps → result has NO stdout/stderr section; partial output is empty (FAIL)** — see AC2 + Other issues.

**Acceptance criteria**
- [x] PASS — `tools/exec.py` defines the executor seam; `tools/bash.py` runs via `asyncio` subprocess.
      Evidence: `CommandExecutor` Protocol + `LocalExecutor` (`asyncio.create_subprocess_shell`) in `src/decode/tools/exec.py:52,65`; `bash` runs through the seam at `src/decode/tools/bash.py:77`; `test_exec.py` (8) + `test_bash.py::test_bash_reports_stdout_and_exit_code` pass; adversarial probe (a) confirms stdout+stderr+exit captured end-to-end.
- [ ] FAIL — `settings.bash_timeout_s` enforced; output truncated via `tools/truncate.py` (2000 lines / 50 KB) with overflow spilled to a temp-file path.
      The enforcement, clamping, default, truncation, and temp-file spill all work (probes d, f; `test_bash_truncates_long_output_and_spills_to_a_temp_file`, `::_defaults_timeout_to_settings`, `::_clamps_a_too_large_timeout`). **But the timeout path's documented "returns partial output" contract is broken — partial output is always empty.**
      Expected: a command that flushes output then exceeds the deadline returns that partial output (per `exec.py:15,43,80,117` "returns the partial output captured before the kill" and the `bash._render` header "Partial output below.").
      Actual: stdout/stderr are always `''` on timeout. Verified at executor level: a child that does `sys.stdout.write('EARLY-OUT\n'); flush(); sleep(30)` with `timeout_s=0.4` → `stdout=''`. The `bash` header still says "Partial output below." but shows nothing.
      Root cause: `LocalExecutor.run` lets `asyncio.wait_for(process.communicate(), ...)` CANCEL `communicate()` on timeout; `_terminate` then calls `process.communicate()` a SECOND time (`exec.py:90,121,124`). The cancelled first call discards its internal stream-reader buffers, so the re-invoked `communicate()` returns empty bytes (reproduced standalone). Draining the readers afterward also returns empty — the buffered bytes are gone.
      Fix: don't cancel `communicate()`. Run it as a task and `await asyncio.wait({task}, timeout=timeout_s)`; on timeout signal the group (SIGTERM→SIGKILL) and `await` the SAME task so it finishes draining the partial output. Validated: this pattern recovers `EARLY-OUT\n` on a 0.4s timeout. Add a regression test asserting partial output is non-empty on timeout (current `test_run_times_out_and_returns_timed_out` / `test_bash_times_out_and_tells_the_model` only assert `timed_out` / the header, which is why the gap slipped through).
- [x] PASS — Gated (no dangerous-command classifier in v1 — human approves every call).
      Evidence: `bash` raises `ApprovalRequired` when `not ctx.tool_call_approved` BEFORE running anything (`src/decode/tools/bash.py:69`); `test_bash_requires_approval_when_not_approved` + `test_bash_runs_through_the_agent_when_approved` pass; adversarial probe (e) confirms an unapproved call creates no file; `registry.py:57` registers `read_only=False`.

**Evidence**
```
$ make pre-commit
... 214 passed in 3.88s ...   (0 warnings; filterwarnings=error)
$ uv lock --check
Resolved 163 packages in 3ms

# Adversarial probe (real subprocesses): 12/13 PASS
[PASS] a/b/c/d/e/f/g.1/g.2 + boundary/unicode/concurrent/bad-cwd
[FAIL] timeout: partial output captured before kill is returned
        'Command timed out after 0.4s and was terminated (exit code -15). Partial output below.'
        (no stdout/stderr section — partial output is empty)

# Executor-level reproduction (child flushes BEFORE the deadline):
Case1 (flushed stdout then sleep, timeout_s=0.4): timed_out=True exit=-15 stdout='' stderr=''
# Normal (non-timeout) exit captures it fine: stdout='EARLY-OUT\n' timed_out=False
# Validated fix pattern (single non-cancelled communicate): stdout=b'EARLY-OUT\n' on timeout
```

**Other issues found**
- (the partial-output gap above is the only defect — promoted to the AC2 FAIL since the contract is documented in the shipped docstrings and the header lies to the model.)
- Process-group kill is solid: no orphans/zombies across the child-kill, huge-output, and signal probes (`ps` clean before and after). Good.
- Minor (non-blocking, PASS-with-note): on timeout the header reports `exit code -15` (SIGTERM) even though the group may be SIGKILLed after the grace; cosmetic, the model only needs "timed out". No action required.

**VERDICT: FAIL** — 1 issue: the timeout path discards partial output despite the documented "Partial output below." contract (AC2). Fix `LocalExecutor.run`/`_terminate` to not cancel `communicate()`, and add a regression test asserting non-empty partial output on timeout. All other ACs and 12/13 adversarial probes pass; full suite green, 0 warnings.

### [SWE] 2026-06-19 21:05 — Fixes (QA defect: timeout discards partial output)

**Defect fixed**
- AC2 FAIL: `LocalExecutor.run` wrapped `process.communicate()` in `asyncio.wait_for(..., timeout_s)`.
  On timeout, `wait_for` *cancelled* `communicate()`, discarding the stream-reader buffers;
  `_terminate` then re-invoked `process.communicate()` a second time and got empty bytes. A child
  that flushed output *before* the deadline then hung returned `stdout=''`/`stderr=''` while the
  `bash` header still claimed "Partial output below." (the header lied to the model).

**Files modified**
- `src/decode/tools/exec.py` — `LocalExecutor.run` no longer cancels `communicate()`. It runs
  `communicate()` as a task (`asyncio.ensure_future`), waits via `asyncio.wait({comm}, timeout=timeout_s)`
  (which does NOT cancel on expiry), and on timeout calls `_terminate(process, comm)` which signals
  the process **group** (SIGTERM → SIGKILL after `_KILL_GRACE_S`) and then `await`s the **same**
  `comm` task so the partial output the child already buffered is drained and returned. `_terminate`
  now takes the running task instead of re-invoking `communicate()`. `timed_out` flag and the
  process-group kill (`os.killpg`, `start_new_session=True`) are preserved unchanged.
- `tests/unit/decode/tools/test_exec.py` — NEW
  `test_run_returns_partial_output_captured_before_timeout`: a child flushes a sentinel to BOTH
  streams then sleeps past a 0.4s deadline; asserts `timed_out is True` AND the sentinels survive
  into `result.stdout` / `result.stderr` (was empty before the fix).
- `tests/unit/decode/tools/test_bash.py` — NEW
  `test_bash_timeout_returns_partial_output_to_the_model`: same scenario through the `bash` tool;
  asserts the reply flags the timeout AND carries a `stdout:` section with the sentinel (the header
  no longer lies).

**Tests**
- Unit: 216 passing, 0 failing (`make pre-commit`). +2 regression tests over the prior 214.
  Both new tests were confirmed RED before the fix (`assert 'EARLY-OUT' in ''`), GREEN after.
- Timeout tests stay fast (0.4s deadlines). 0 warnings (`filterwarnings=error`).
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] AC2 now PASS — partial output is returned on timeout. Verified by
  `test_exec.py::test_run_returns_partial_output_captured_before_timeout` and
  `test_bash.py::test_bash_timeout_returns_partial_output_to_the_model`. Existing
  enforcement/clamp/truncation/spill tests remain green.

**Evidence**
```
$ make pre-commit
... 216 passed in 4.78s ...   (0 warnings)
$ uv lock --check
Resolved 163 packages in 3ms

# E2E (real subprocess, child flushes BEFORE a 0.4s deadline then sleeps 30s):
[timeout-partial]  timed_out=True exit=-15 elapsed=0.41s
                   stdout='EARLY-OUT\n' stderr='EARLY-ERR\n'
[normal]           timed_out=False exit=0 stdout='hi\n' stderr='err\n'

# Through the bash tool — header no longer lies:
Command timed out after 0.4s and was terminated (exit code -15). Partial output below.

stdout:
EARLY-OUT

# Orphan check after the timeout kill:
$ ps -ax | grep -E "EARLY-OUT|time.sleep(30)"  -> none (good)
```

**Notes**
- Process-group kill behaviour (no orphans/zombies) preserved — re-verified at the executor and
  bash level and via `ps`. The existing child-kill tests still pass.
- NOT committed — handing back to the Tester for re-review per workflow.

### [Tester] 2026-06-19 21:40 — Re-QA (verifying the timeout partial-output fix)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` exit 0; 57 files formatted, all checks pass)
- Unit tests: 216 passed / 0 failed (`make unit-tests`, 4.96s — timeout tests fast). +2 over prior 214.
- Integration tests: N/A — `tests/integration/` empty; no new infra (local subprocess only). Correct.
- `uv lock --check`: PASS (Resolved 163 packages)
- Warnings: 0 (`filterwarnings=error` would have failed the run otherwise)
- `code-review` plugin enabled (advisory) — folded into the manual review of the focused diff; no defects beyond the cosmetic header note (g) below.

**Independent verification of the fix (did NOT trust the SWE tests alone)**
Read the fix: `LocalExecutor.run` runs `communicate()` as a task (`asyncio.ensure_future`),
`await asyncio.wait({comm}, timeout=timeout_s)` (no cancellation on expiry); on timeout
`_terminate` signals the process group (SIGTERM → SIGKILL after `_KILL_GRACE_S`) and `await`s
the SAME `comm` task so buffered partial output is drained (`exec.py:99-137`). The fix matches
the FAIL-log prescription exactly.

**E2E adversarial pass** (real subprocesses, ran my own probes — not the SWE's tests)
- Happy path (regression R2): `echo line1; echo line2; echo err1 1>&2; exit 7` → stdout `line1\nline2\n`, stderr `err1\n`, exit 7, timed_out False (PASS)
- Fix probe 1 (executor, BOTH streams): child flushes `OUT-SENTINEL-12345` to stdout AND `ERR-SENTINEL-67890` to stderr, then `sleep(30)`, `timeout_s=0.4` → `timed_out=True`, both sentinels present in `stdout`/`stderr`, returned in 0.41s (PASS)
- Fix probe 2 (bash tool, model-facing): same command via `bash(..., timeout=0.4)` → reply flags "timed out", carries `stdout:`+`OUT-SENTINEL-12345` AND `stderr:`+`ERR-SENTINEL-67890` (header no longer lies) (PASS)
- Fix probe 3 (no output before timeout): `sleep(30)`, `timeout_s=0.3` → empty partial (`stdout=''`/`stderr=''`), `timed_out=True`, bash reply has NO empty stream sections (PASS)
- Adversarial (SIGKILL escalation): child `SIG_IGN`s SIGTERM, flushes `IGNORES-SIGTERM-OUT`, loops forever; grace shortened to 0.4s, `timeout_s=0.3` → partial output preserved, escalated to SIGKILL (`exit=-9`), child gone (`os.kill(pid,0)`→ProcessLookupError, `ps` empty), returned in 0.71s — no hang (PASS)
- Adversarial (large partial): child flushes 5000 lines (`L0`..`L4999`) then sleeps, `timeout_s=0.5` → all 5000 lines drained on timeout, `timed_out=True`, 0.51s (PASS)

**Regression re-check (previously-green behaviour still green)**
- R1 process-group kill / no orphan/zombie: backgrounded child (writes PID + ticks every 50ms), 0.3s timeout → `timed_out=True`, sentinel frozen, child PID dead (`os.kill(pid,0)`→ProcessLookupError), `ps -p` empty (no zombie) (PASS)
- R3 truncation + complete spill: 2500-line emitter under a 5-line cap → truncated notice + spill file containing `0` (first) and `2499` (last) (PASS)
- R4 gating no side effect: unapproved `bash("touch …")` → `ApprovalRequired` raised, file absent (PASS)
- R5 clamp / non-positive: `timeout=0` and `timeout=-5` → `ModelRetry`; `timeout=999999` runs (clamped) (PASS)
- R6 concurrency: 10 concurrent `bash` calls → each reply correct, zero cross-talk (each carries exactly its own marker) (PASS)
- Post-run `ps` sweep: NO leftover processes from any probe (clean)

**Acceptance criteria**
- [x] PASS — `tools/exec.py` defines the executor seam; `tools/bash.py` runs via `asyncio` subprocess.
      Evidence: `CommandExecutor` Protocol + `LocalExecutor` (`asyncio.create_subprocess_shell`) at `src/decode/tools/exec.py:52,65`; `bash` runs through the seam at `src/decode/tools/bash.py:77`; `test_exec.py` (9) + `test_bash.py` (15) pass; happy-path probe confirms stdout+stderr+exit end-to-end.
- [x] PASS — `settings.bash_timeout_s` enforced; output truncated via `tools/truncate.py` (2000 lines / 50 KB) with overflow spilled to a temp-file path — **and the timeout path now returns the documented partial output.**
      Evidence: enforcement/clamp/default/reject — R5 + `test_bash_clamps_a_too_large_timeout_to_the_configured_max`, `::_defaults_timeout_to_settings`, `::_rejects_non_positive_timeout`. Truncation+spill — R3 + `test_bash_truncates_long_output_and_spills_to_a_temp_file`. **Partial output (the prior FAIL) — `test_exec.py::test_run_returns_partial_output_captured_before_timeout` + `test_bash.py::test_bash_timeout_returns_partial_output_to_the_model` (both confirmed red→green by SWE) AND my independent fix probes 1–3 + adversarial SIGKILL/large-partial probes: both streams' pre-deadline output reaches the model on timeout; the "Partial output below." header no longer lies; no-output case stays clean.**
- [x] PASS — Gated (no dangerous-command classifier in v1 — human approves every call).
      Evidence: `bash` raises `ApprovalRequired` when `not ctx.tool_call_approved` BEFORE running (`src/decode/tools/bash.py:69`); `test_bash_requires_approval_when_not_approved` + `test_bash_runs_through_the_agent_when_approved` pass; R4 confirms an unapproved call creates no file; `registry.py:57` registers `read_only=False`.

**Evidence**
```
$ make unit-tests
... 216 passed in 4.96s ...   (0 warnings; filterwarnings=error)
$ uv lock --check
Resolved 163 packages in 2ms

# Independent executor-level probe (child flushes BOTH streams before a 0.4s deadline):
[PROBE1 both-streams] elapsed=0.41s timed_out=True exit=-15
  stdout='OUT-SENTINEL-12345\n'  stderr='ERR-SENTINEL-67890\n'
[PROBE2 no-output]     elapsed=0.31s timed_out=True stdout='' stderr=''

# Through the bash tool — header backed by real output:
Command timed out after 0.4s and was terminated (exit code -15). Partial output below.
stdout:
OUT-SENTINEL-12345
stderr:
ERR-SENTINEL-67890

# SIGKILL-escalation path still delivers partial output AND kills the child:
[ADV1 ignores-SIGTERM] elapsed=0.71s timed_out=True exit=-9 stdout='IGNORES-SIGTERM-OUT\n'
  child alive_after_kill=False  ps='' (no orphan/zombie)

# Post-probe orphan sweep:
$ ps -ax | grep -E "SENTINEL|decode_qa|time.sleep(30)"  -> NO LEFTOVER PROCESSES (clean)
```

**Other issues found**
- (none blocking) The prior AC2 partial-output defect is fixed and pinned by two regression tests plus my independent probes.
- Minor (non-blocking, PASS-with-note, unchanged from prior QA): on a no-output timeout the header still says "Partial output below." even though there is nothing to show (probe 2/3). It no longer *lies* about discarded output — it just over-promises when the child wrote nothing before the deadline. Cosmetic; the model only needs "timed out". The cosmetic `exit code -15` (SIGTERM) vs `-9` (SIGKILL after grace) wording also remains; harmless. No action required.

**VERDICT: PASS** — the timeout partial-output blocker is fixed and independently confirmed at the `LocalExecutor` and `bash`-tool levels, including the SIGKILL-escalation and large-output paths; all previously-green behaviour (process-group kill / no orphan-zombie, full-output capture, truncation+spill, gating, clamp/reject, concurrency) re-verified green; full suite 216/216, 0 warnings, lock clean. Hand off for commit.
