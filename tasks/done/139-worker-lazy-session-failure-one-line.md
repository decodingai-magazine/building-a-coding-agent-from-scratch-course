---
id: 139
feature: kitaru-replay-runtime
status: done
---

# Worker-mode lazy Kitaru Session creation must honor the one-friendly-line contract

Tags: `runtime`, `cli`, `bug`
Depends on: None (follow-up to 134/136/137 — all done)
Blocks: —

Post-acceptance follow-up to the kitaru-replay-runtime feature (PA-groomed at acceptance
review; NOT part of that feature's accepted gate). This task implements ADR-0019 §3's
existing contract for a failure window the Recording Seam's probe cannot cover — no new ADR.

**The gap (reproduced twice, deliberately unfixed in 136/137):** `kitaru-pydantic-ai`
creates the Kitaru Session LAZILY inside `agent.run` (its own `capability.wrap_run`) — AFTER
`wrap_for_recording`'s wrap-time reachability probe. In worker mode (`KITARU_TASK_ID`
present), a session-creation failure therefore escapes as a 90+-line raw traceback on stderr
instead of the ONE `Decode:`-prefixed friendly line every other recording failure gets.
Reproduced with a malformed task id (`ValueError: badly formed hexadecimal UUID string`) and
a well-formed but unregistered one (`ValidationError: 422: Session names no agent and no
task to infer one from`). Exit code is already non-zero either way, so a Kitaru Worker still
reads the run as failed — only the friendly-line contract is broken. It never fired on the
live replays in 137. Full history: `tasks/done/136-worker-task-input-entry.md` (SWE Notes
"Adjacent finding") and `tasks/done/137-agent-version-2-replay-context.md` (carried finding).

## Scope

- Surface a worker-mode Kitaru session-creation failure that escapes `agent.run` as the same
  one-line contract the seam's probe failures already get: ONE `Decode:`-prefixed stderr line
  naming the cause, full traceback in `.decode/logs/decode.log` only, exit non-zero.
- **Suggested approach (SWE decides specifics):** extend `src/decode/cli.py::run()`'s
  existing `except RecordingUnavailableError` guard to also catch the kitaru client's
  exception family when `is_worker_task()` is true — same
  `logger.warning(..., exc_info=True)` + `click.echo(f"Decode: ...", err=True)` +
  `Exit(1)` idiom the 134 fix round established. The catch must be worker-gated: on a
  user-launched recorded run, an exception escaping `agent.run` is an agent failure and must
  keep propagating untouched.
- **Never mask a genuine agent failure.** A model/provider error inside a worker replay
  (e.g. the `ModelHTTPError` 503 in 137's replay 1) must still surface as an agent-level
  failure — do NOT rewrite it into a recording line. Only kitaru-client/session-creation
  exception types qualify.
- **Import invariant holds.** `kitaru` exception types may only be imported inside the
  worker branch (or matched lazily, e.g. by exception module name) — the no-kitaru-import
  invariant for user-launched paths must stay green.
- **Escalation clause:** if distinguishing a session-creation failure from an agent failure
  inside one `agent.run` turns out to require adapter changes or a recording-architecture
  change, STOP and escalate to the PA (architectural fork) — do not fork the adapter.
- Unit tests at the CLI boundary (faked adapter raising the kitaru exception shapes):
  worker-mode one-liner, user-launched propagation, agent-failure passthrough.

## Acceptance Criteria

- [x] `KITARU_TASK_ID=<well-formed synthetic uuid> KITARU_TASK_INPUTS='{"task":"say hi"}' decode run` against the real workspace exits non-zero with stderr = exactly ONE `Decode:`-prefixed line naming the session-creation failure (no `Traceback` on stderr); the full traceback is in `.decode/logs/decode.log`.
- [x] A malformed `KITARU_TASK_ID` (uuid parse failure inside the adapter) produces the same one-line contract.
- [x] A faked agent-level failure (e.g. a model HTTP 503) under `KITARU_TASK_ID` is NOT rewritten into a recording line — it propagates as an agent failure exactly as today.
- [x] User-launched recorded runs are byte-identical to today on every path (degrade line, happy path, agent failure) — pinned by existing 134/135 tests staying green untouched.
- [x] No kitaru import on any path where recording is unconfigured (existing fresh-interpreter invariant tests stay green).
- [x] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator debugs a Worker locally with a synthetic task id and gets one line
1. Operator exports `KITARU_TASK_ID=00000000-0000-4000-8000-000000000139` and `KITARU_TASK_INPUTS='{"task":"say hi"}'` in a shell authenticated to the workspace
2. Operator runs `decode run`
3. The workspace rejects session creation (422 — no such task)
4. Operator sees exit 1 and exactly one stderr line: `Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: ...` naming the 422 cause
5. `tail .decode/logs/decode.log` shows the full traceback for post-hoc debugging

### Story: A real replay fails at the model and the Worker log stays honest
1. A Kitaru Worker claims a replay and spawns `decode run`; the Kitaru Session is created fine
2. The provider returns 503 on the first model request
3. `kitaru replay get <id>` shows `status: failed` with the agent-level `ModelHTTPError` — no recording-failure wording anywhere (matches 137's replay 1 evidence shape)

---

Refs: `tasks/done/136-worker-task-input-entry.md`, `tasks/done/137-agent-version-2-replay-context.md`, ADR-0019 §3

## Log

### [PA] 2026-08-22 — Grooming

**Summary**
Close the lazy-session-creation window in the Recording Seam's one-friendly-line contract for
worker-spawned runs. Design decision taken at grooming: catch the kitaru client exception
family at the CLI boundary, worker-gated — no adapter fork, no new ADR (this implements
ADR-0019 §3 as amended, it does not change it).

**Key decisions**
- Bias-to-least: reuse the existing `except RecordingUnavailableError` guard idiom in
  `cli.py::run()` rather than building failure-classification machinery inside the seam.
- Worker-gated catch only; user-launched paths untouched by construction.
- Explicit escalation clause if the distinction can't be made at the CLI boundary.

**Dependencies**
- None — 134/136/137 are done; this hardens their shipped surface.

**User stories**
- 2 stories covering: the misconfigured-worker debug path (the reproduced gap) and the
  agent-failure passthrough (the must-not-regress case).

Ready for implementation.

### [SWE] 2026-08-22 23:10 — Implementation

**Files modified**
- `src/decode/runtime/recording.py` — `is_recording_failure()` (kitaru-owned exception classifier, by
  module name, no import), `worker_session_failure()` / `_unavailable()` (the ONE Worker-Task line,
  now shared by the probe and the lazy-session path), `_worker_task_id()` (parse `KITARU_TASK_ID` at
  wrap time so a typo is a setup failure), `_agent_id_trap_hint()` (the 403 → `KITARU_AGENT_ID` hint)
- `src/decode/cli.py` — `run()`: second, worker-gated `except Exception` that turns an escaping
  kitaru session-creation failure into the same friendly line + `Exit(1)`, and re-raises everything else
- `tests/support/kitaru_recording.py` — `kitaru_api_error()`: an error shaped like the real
  `kitaru.client.exceptions.APIError` (kitaru module, `status_code`, `"<code>: <detail>"`)
- `tests/unit/decode/runtime/test_run_command.py` — 5 CLI-boundary tests (worker one-liner, traceback
  in the log only, 403 hint, agent-failure passthrough, user-launched passthrough)
- `tests/unit/decode/runtime/test_recording.py` — 10 seam tests (malformed task id, 403 hint present /
  absent, the classifier on kitaru vs. `ModelHTTPError` vs. builtins, the session-failure line)
- `tests/unit/decode/test_kitaru_dependency.py` — pins the REAL kitaru exception family's module +
  `status_code` (the two facts the classifier and the fake stand on)
- `running_the_code/03_runtime.md`, `running_the_code/08_evals_replays.md` — the lazy-session half of
  the Worker hard-fail contract; §7.3's `Tracked: tasks/139` retired

**Design (as groomed — no ADR, no adapter fork, no escalation)**
- Worker-gated **and** type-gated catch at the CLI boundary. The gate is
  `is_worker_task() and is_recording_failure(exc)`; anything else re-raises untouched, so a provider
  `ModelHTTPError` inside a replay stays an agent failure.
- Classification is by the exception class's own module (`kitaru` / `kitaru_pydantic_ai`, MRO walked),
  matched by NAME — so the guard itself imports no kitaru and the invariant tests stay green.
- **Deliberate non-catch:** an `httpx` transport error raised by the kitaru client mid-run is NOT
  classified (an `httpx.ConnectError` to the workspace and one to the model provider are
  indistinguishable by type, and masking the second would lie the other way). In practice the seam's
  wrap-time probe already owns "workspace unreachable"; the residue is a workspace that dies in the
  seconds between the probe and session creation → raw traceback, exit non-zero. Upgrade path if it
  ever bites: classify by the traceback's innermost frame module instead.
- **AC2 fixed at the seam, not by guessing:** a malformed `KITARU_TASK_ID` raises a bare `ValueError`
  from `uuid.UUID` inside the adapter, which no type-based rule can safely claim. decode now parses
  the id at wrap time (the adapter re-reads the same env var), so the typo takes the existing
  hard-fail exit with a line that names the variable. No bare-`ValueError` catching anywhere.

**Tests**
- Unit: 2409 passing, 0 failing (`make unit-tests`) — 15 new
- Integration: 112 passing, no infra surface touched (ran as part of `make ci`)
- `make ci`: green (lockfile + format-check + lint-check + 2521 tests, 8m11s)

**Acceptance criteria**
- [x] Real-workspace 422 → ONE line, exit 1, traceback in the log — verified live (Evidence, run 2) +
  `tests/unit/decode/runtime/test_run_command.py::test_a_worker_session_creation_failure_is_one_friendly_line`
  and `::test_a_worker_session_creation_failure_keeps_the_traceback_in_the_log`
- [x] Malformed `KITARU_TASK_ID` → same contract — verified live (Evidence, run 1) +
  `tests/unit/decode/runtime/test_recording.py::test_a_malformed_worker_task_id_hard_fails_at_the_seam`
- [x] Agent-level failure under `KITARU_TASK_ID` is not rewritten —
  `test_run_command.py::test_a_worker_agent_failure_is_never_rewritten_as_a_recording_line` (real
  `pydantic_ai.exceptions.ModelHTTPError`) + `test_recording.py::test_an_agent_failure_does_not_read_as_a_recording_failure`;
  live proof for a non-kitaru worker-mode failure in Evidence, run 5
- [x] User-launched paths byte-identical — every 134/135 test untouched and green;
  `test_run_command.py::test_a_user_launched_kitaru_failure_still_propagates` pins the gate; live happy
  path + live agent failure in Evidence, runs 3-4
- [x] No kitaru import when unconfigured — `test_recording.py::test_the_unconfigured_seam_imports_no_kitaru_module_in_a_fresh_interpreter`
  and the `test_worker_task_inputs.py` twin, both green (the classifier imports nothing)
- [x] Full unit suite + `make ci` green

**Evidence**

```
$ make unit-tests
============================ 2409 passed in 40.50s =============================

$ make ci
======================= 2521 passed in 491.25s (0:08:11) =======================
[exited with code 0]

--- run 1: malformed task id, offline (AC2) ---
$ env -u KITARU_API_URL -u KITARU_AGENT_ID KITARU_TASK_ID=abc123 \
    KITARU_TASK_INPUTS='{"task":"say hi"}' LLM_PROVIDER=gemini SANDBOX_MODE=none \
    RUNTIME_ENABLED=true uv run decode run
exit=1 ; stdout empty ; stderr (1 line, no Traceback):
Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: recording could not be set up
against the Kitaru workspace configured by your kitaru login (ValueError: KITARU_TASK_ID='abc123' is
not a Kitaru task id: badly formed hexadecimal UUID string). Failing the run rather than producing an
unrecorded — and therefore untrustworthy — replay.

--- run 2: the reproduced defect, against the REAL workspace, read-only (AC1) ---
$ set -a && . .env && set +a && env -u KITARU_AGENT_ID \
    KITARU_TASK_ID=00000000-0000-4000-8000-000000000139 KITARU_TASK_INPUTS='{"task":"say hi"}' \
    LLM_PROVIDER=gemini SANDBOX_MODE=none RUNTIME_ENABLED=true uv run decode run
exit=1 ; stdout empty ; stderr = 1 line, 0 occurrences of "Traceback":
Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: the Kitaru Session could not be
created on https://f5ee9622-kitaru.cloudinfra.zenml.io (ValidationError: 422: Session names no agent
and no task to infer one from). Failing the run rather than producing an unrecorded — and therefore
untrustworthy — replay.
$ grep -n "Kitaru Session creation failed for a Worker Task" -A 3 .decode/logs/decode.log
10283:... WARNING decode.cli: Kitaru Session creation failed for a Worker Task; failing
10284-Traceback (most recent call last):
10285-  File ".../src/decode/cli.py", line 524, in run
10286-    output = run_headless_task(task, model=model, repo=resolved_repo, local=local)
(7 kitaru/client frames in the logged traceback; none on stderr)

--- run 3: user-launched happy path, unchanged (AC4) ---
$ ... -u KITARU_TASK_ID ... uv run decode run "reply with exactly the single word: HAPPYPATH"
exit=0 ; stdout "HAPPYPATH" ; stderr empty

--- run 4: user-launched agent failure, unchanged (AC4) ---
$ ... uv run decode run --model gemini-no-such-model "say hi"
exit=1 ; raw 74-frame traceback ending in
pydantic_ai.exceptions.ModelHTTPError: status_code: 404, model_name: gemini-no-such-model ...

--- run 5: worker mode, NON-kitaru failure → not rewritten (AC3, offline) ---
$ ... KITARU_TASK_ID=00000000-0000-4000-8000-000000000139 SANDBOX_MODE=docker \
    uv run decode run --repo /no/such/repo.git
exit=1 ; stderr = raw traceback, 0 occurrences of "kitaru":
RuntimeError: could not clone '/no/such/repo.git' into the Workspace ...
```

**Notes**
- No live workspace writes: run 2's 422 is a rejected `sessions.create` (nothing created), and the
  model is never reached — session creation precedes the first LLM call in the adapter's `wrap_run`.
- The worker probe line's wording changed from "<workspace> could not be reached" to "recording could
  not be set up against <workspace>": the same branch now also reports a malformed task id, which is
  not a reachability problem. User-launched wording (the degrade line) is untouched.
- `_worker_task_id()` only *validates*; `is_worker_task()` stays presence-based, so `task_inputs`'
  contract and the tests that use `KITARU_TASK_ID=abc123` there are unaffected.
- NOT RUN — a real Kitaru Worker replay whose Session is created and whose MODEL then fails (Story 2):
  it needs a live Worker + a provider outage; covered by unit tests over the real `ModelHTTPError`
  type, by run 5's live non-kitaru worker failure, and historically by task 137's replay 1 evidence.

### [Tester] 2026-08-22 23:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 312 files; `ruff check` clean; `make
  pre-commit` re-runs format-check + lint-check + unit-tests, all green)
- Unit tests: 2409 passed / 0 failed (`make unit-tests`, 41.5s)
- Integration tests: 112 passed / 0 failed (`make integration-tests`, 419.6s — no kitaru/network
  surface touched, matches SWE's report)
- New-test subset re-run in isolation with `-W error`: 69 passed, 0 warnings
  (`test_recording.py` + `test_run_command.py` + `test_kitaru_dependency.py`)
- Warnings: 0

**E2E adversarial pass** (fakes at the CLI/seam boundary — no live workspace writes)
- Happy path: re-verified the SWE's live evidence is internally consistent (run 2, real 422 against
  the real workspace) → ONE `Decode: [kitaru] …` line, exit 1, 0 "Traceback" occurrences on stderr,
  full traceback confirmed present in `.decode/logs/decode.log` per the SWE's grep output (PASS)
- Break path 1 (fake kitaru-module exception under `KITARU_TASK_ID`): faked
  `kitaru_api_error(422, ...)` from the runner under a worker env → exit 1, stderr = exactly 1 line,
  0 "Traceback" occurrences (PASS)
- Break path 2 (same exception WITHOUT task id): same 422 error, no `KITARU_TASK_ID` → `is_worker_task()`
  is False, `except Exception` re-raises untouched (`result.exception is error`), degrade path
  unaffected — byte-identical to pre-139 behavior (PASS)
- Break path 3 (non-kitaru exception under task id): `ModelHTTPError(503)` raised under
  `KITARU_TASK_ID` → `is_recording_failure()` is False, exception re-raised untouched
  (`result.exception is error`), no `[kitaru]` text anywhere on stderr — never masks an agent failure
  (PASS)
- Break path 4 (403-shaped vs 422-shaped): 403 → `KITARU_AGENT_ID` hint present in the one line; 422 →
  hint absent (PASS both)
- Break path 5 (malformed `KITARU_TASK_ID`): `KITARU_TASK_ID=not-a-uuid-at-all` under
  `wrap_for_recording` → `RecordingUnavailableError`, single-line message, names the env var and the
  underlying `ValueError` as `__cause__` (PASS)
- Extra boundary: kitaru-module exception with NO `status_code` attribute → `getattr(..., None)`
  degrades gracefully, no crash, no spurious hint, still exactly one line (PASS)
- Extra boundary: `KITARU_TASK_ID=""` → `is_worker_task()` correctly reads `False` (presence-based,
  `bool("")` is falsy) — the guard is not fooled by an exported-but-empty var (PASS)
- httpx judgment call (SWE's "deliberate non-catch"): confirmed live that
  `httpx.ConnectError.__module__ == "httpx"`, outside `_KITARU_PACKAGES`, so
  `is_recording_failure()` returns `False` for it. Verified end to end: an `httpx.ConnectError`
  raised by the runner under a Worker Task propagates untouched (`result.exception is error`, no
  `[kitaru]` wording) — exit still non-zero, so the Worker still reads the run as failed; it simply
  does not get the friendly-line rewording. **RULING: ACCEPT.** Classifying by type here would risk
  misclassifying a genuine provider-side `httpx` failure as a "recording" failure — exactly the
  masking the task's headline invariant ("never mask a genuine agent failure") forbids — for a gap
  the SWE correctly scoped as narrow (workspace dying in the few-second window between the probe and
  session creation) and already degrades safely (non-zero exit, traceback in the log via the default
  exception path, no untrustworthy replay silently produced). No adapter fork or architecture change
  needed to do better without that risk, so this does not trip the task's escalation clause.

**Acceptance criteria**
- [x] PASS — real-workspace 422 → ONE `Decode:` line, exit 1, no stderr `Traceback`, full traceback
      in `.decode/logs/decode.log` — SWE's live Evidence run 2 (self-consistent: `_unavailable()` +
      `worker_session_failure()` build exactly this shape) + `test_run_command.py::test_a_worker_session_creation_failure_is_one_friendly_line`
      and `::test_a_worker_session_creation_failure_keeps_the_traceback_in_the_log`, both re-run and
      green
- [x] PASS — malformed `KITARU_TASK_ID` → same one-line contract —
      `test_recording.py::test_a_malformed_worker_task_id_hard_fails_at_the_seam` re-run green +
      reproduced independently (Break path 5 above) with a different malformed value
      (`not-a-uuid-at-all` vs. the SWE's `abc123`)
- [x] PASS — faked agent-level failure (`ModelHTTPError` 503) under `KITARU_TASK_ID` is not
      rewritten — `test_run_command.py::test_a_worker_agent_failure_is_never_rewritten_as_a_recording_line`
      re-run green + reproduced independently (Break path 3)
- [x] PASS — user-launched recorded runs byte-identical — every pre-existing 134/135 test in
      `test_run_command.py` / `test_recording.py` still green (2409/2409 unit pass), plus
      `test_a_user_launched_kitaru_failure_still_propagates` re-run green + reproduced independently
      (Break path 2)
- [x] PASS — no kitaru import when unconfigured —
      `test_recording.py::test_the_unconfigured_seam_imports_no_kitaru_module_in_a_fresh_interpreter`
      re-run green; independently re-verified with a fresh subprocess importing `decode.cli` (which
      now also imports `is_recording_failure`/`worker_session_failure`/`is_worker_task` at call time)
      with all kitaru env vars scrubbed: `sys.modules` has zero `kitaru*` entries
- [x] PASS — full unit suite + `make ci`-equivalent green — 2409 unit + 112 integration passed,
      format-check/lint-check clean, 0 warnings

**Evidence**
```
$ make unit-tests
============================ 2409 passed in 41.47s =============================

$ make integration-tests
======================= 112 passed in 419.63s (0:06:59) ========================

$ uv run pytest tests/unit/decode/runtime/test_recording.py tests/unit/decode/runtime/test_run_command.py tests/unit/decode/test_kitaru_dependency.py -v -W error
============================== 69 passed in 2.19s ==============================

$ env -u KITARU_API_URL -u KITARU_AGENT_ID -u KITARU_TASK_ID uv run python -c "
import sys, decode.cli
print(sorted(m for m in sys.modules if m.startswith('kitaru')))"
[]

$ uv run python -c "import httpx; print(httpx.ConnectError.__module__)"
httpx
```

**Other issues found**
- `recording.one_line()` (pre-existing, not new in this task) collapses newlines/tabs via
  `" ".join(text.split())` but does not strip ANSI escape sequences from an error's `str()`. A kitaru
  or agent exception whose message embeds raw escape codes would still render on one physical line
  but could carry stray terminal color codes. Not a task-139 regression (shared helper, pre-dates
  this diff) and out of scope for this AC set — flagging as a follow-up candidate only.
- The code-review plugin is enabled in `.claude/settings.json` but this Tester's toolset (Read /
  Edit / Write / Bash) has no invocation surface for it; the manual checklist above is the QA basis
  for this review. Noting per protocol since the plugin's absence-of-signal shouldn't be read as a
  clean bill from it.

**VERDICT: PASS**
