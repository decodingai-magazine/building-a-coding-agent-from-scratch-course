---
id: 134
feature: kitaru-replay-runtime
status: done
---

# Recording Seam: presence-based KitaruAgent wrap for headless `decode run`

Tags: `runtime`, `enhancement`
Depends on: 133
Blocks: 135, 136, 137

This task implements ADR-0019 (§ Recording Seam). One seam decides, for any built agent,
whether it runs wrapped in `kitaru_pydantic_ai.KitaruAgent` (recorded) or bare.

## Scope

- New settings field `kitaru_agent_id` (env `KITARU_AGENT_ID`, default empty) + `.env.example`
  entry. Presence-based opt-in: recording is configured iff `kitaru_agent_id` is set AND the
  adapter's own connection env (`KITARU_API_URL`; `KITARU_API_KEY` per the client's
  conventions) is present — decode passes the agent id through and lets the adapter client
  resolve its env itself; decode adds NO url/key settings of its own.
- New module (suggested `src/decode/runtime/recording.py`): one function that takes the built
  `Agent` (+ optional `session_name`) and returns either `KitaruAgent(agent,
  agent_id=settings.kitaru_agent_id, session_name=...)` or the bare agent. Constructor facts
  (verified from the adapter docs): `KitaruAgent(agent, agent_id=None, agent_version_id=None,
  session_name=None, batch_size=20)`; one Kitaru session per `run()` call; async `run` +
  `iter` supported; under a worker task the agent id is inferred.
- **Import invariant (tightened):** `kitaru_pydantic_ai` / `kitaru` are imported ONLY inside
  the seam, ONLY when recording is configured. At `DECODE_ENV=local` with no kitaru config,
  decode imports no kitaru module — extend the existing no-import test.
- **Graceful degrade (user-launched):** the adapter fast-fails at session creation when the
  server is unreachable. The seam catches that failure and falls back to the UNWRAPPED agent
  with exactly ONE stderr/log warning line (naming the server, not dumping a traceback); the
  run itself proceeds and succeeds. Design note for the SWE: "catch at wrap seam" — whether
  that's an eager reachability probe at wrap time or catching the first run()'s
  session-creation error and re-running unwrapped is an implementation choice; the observable
  contract is: ONE warning, run completes, exit code unaffected.
- **Hard fail (worker-spawned):** if `KITARU_TASK_ID` is present in the env, degrade is
  FORBIDDEN — a recording/session failure propagates and the process exits non-zero. A silent
  unrecorded replay would be a lying experiment.
- Wire the seam into the plain headless runner from 131 (`decode run`): wrap when configured;
  `session_name` may carry the run's session id.
- Unit tests fake the adapter/server boundary (no network): wrap-when-configured,
  bare-when-not, degrade-once-with-one-line, hard-fail-under-task-id, no-import invariant.

## Acceptance Criteria

- [x] With `KITARU_AGENT_ID` unset, `decode run` builds and runs the bare agent and imports no kitaru module (invariant test).
- [x] With recording configured and a reachable (faked) server, the runner executes through `KitaruAgent` with `agent_id=settings.kitaru_agent_id`.
- [x] With recording configured and an unreachable server, a user-launched `decode run` prints exactly ONE warning line, completes on the bare agent, and exits 0.
- [x] With `KITARU_TASK_ID` set and the server unreachable, the process exits non-zero with a clear error naming the recording failure.
- [x] `.env.example` documents `KITARU_AGENT_ID` (+ pointers to `KITARU_API_URL`/`KITARU_API_KEY` as adapter-owned env); settings drift test green.
- [ ] [HUMAN] Live proof (feature gate "(b)"): with the managed workspace configured, `decode run "<small task>"` records a session visible via `kitaru session list --agent decode --origin recorded`.

## Out of scope

- REPL wiring (135); `KITARU_TASK_INPUTS` handling (136); agent-version registration (137).
- Any Opik/logfire change — the adapter composes with OTel per its docs; tracing stays as-is.

## Log

### [SWE] 2026-08-22 02:46 — Implementation

**Files modified**
- `src/decode/runtime/recording.py` (new) — the Recording Seam: `wrap_for_recording()` +
  `recording_is_configured()` / `is_worker_task()` + `RecordingUnavailableError`; both kitaru
  imports (`kitaru.client`, `kitaru_pydantic_ai`) live inside the configured branch.
- `src/decode/runtime/headless.py` — `_run_task` now runs
  `await wrap_for_recording(_build_headless_agent(model), session_name=session_id)`; the run's
  session id names the Kitaru Session, the Opik thread and the Session Branch alike.
- `src/decode/runtime/__init__.py` — docstring names the seam module.
- `src/decode/config/settings.py` — new `kitaru_agent_id: str = ""` (env `KITARU_AGENT_ID`).
- `.env.example` — `# KITARU_AGENT_ID=` + prose for the adapter-owned `KITARU_API_URL` /
  `KITARU_API_KEY` (prose, not `KEY=` lines: they are read from `os.environ`, never `.env`, so a
  `KEY=` line would fail the drift guard AND lie — `.env` values land in `Settings` only).
- `tests/support/kitaru_recording.py` (new) — fake adapter + fake `KitaruAPIClient` installed via
  `sys.modules`, mirroring `support/kitaru_secrets.py`. No server, no credentials, no network.
- `tests/unit/decode/runtime/test_recording.py` (new, 20 tests) — the gate, the wrap, the probe,
  the degrade, the hard fail, and a fresh-interpreter no-kitaru-import check.
- `tests/unit/decode/runtime/test_headless.py` — 5 wiring tests (bare by default, session_name =
  the run's session id, recorded run executes through the wrapper, degrade completes, worker
  failure propagates).
- `tests/unit/decode/runtime/test_run_command.py` — `decode run` exits non-zero on
  `RecordingUnavailableError`.
- `tests/unit/decode/test_cli.py` — extended the existing no-kitaru-import invariant test: it now
  imports `decode.runtime` + `decode.runtime.recording` too and checks BOTH distributions
  (`kitaru`, `kitaru_pydantic_ai`).
- `tests/conftest.py` — new autouse hermeticity guard `_no_kitaru_recording`: scrubs
  `KITARU_AGENT_ID` / `KITARU_API_URL` / `KITARU_TASK_ID` and blanks the singleton field, so a
  developer with a live workspace in their env cannot make the suite record (or hard-fail).

**Design notes (for the Tester / reviewer)**
- **Probe, not catch-and-rerun.** The seam makes ONE workspace call before wrapping
  (`agents.get(<configured id>)` when decode knows the agent — that one call settles url,
  credentials and the id; `info.get()` under a Worker Task, where the id is inferred). Catching the
  first `run()`'s session-creation error and re-running bare was rejected: it cannot distinguish a
  recording failure from an agent failure, and a re-run repeats tool side effects. Cost: one extra
  HTTP round-trip per recorded run.
- **Worker Task ⇒ always recorded.** `recording_is_configured()` returns True whenever
  `KITARU_TASK_ID` is present, even with `KITARU_AGENT_ID` unset (the adapter infers the id from the
  task's agent version). Deliberately slightly wider than ADR-0019 §3's literal "agent id + env"
  gate: without it, a worker spawned without `KITARU_AGENT_ID` would run silently unrecorded — the
  exact lying experiment §3 forbids. Flagging it for the PA in case the ADR should read this way.
- **`agent_id` is parsed to `uuid.UUID`** (the adapter's declared type). A malformed id is treated
  like any other recording setup failure: degrade for a user-launched run, hard fail under a Worker.

**Tests**
- Unit: 2194 passing, 0 failing (`make pre-commit`, which also runs format-check + lint-check).
- Integration: 96 passing, 16 skipped (docker daemon not reachable on this host — pre-existing).

**Acceptance criteria**
- [x] AC1 bare + no kitaru import —
  `tests/unit/decode/runtime/test_recording.py::test_the_unconfigured_seam_imports_no_kitaru_module_in_a_fresh_interpreter`,
  `::test_an_unconfigured_run_touches_no_kitaru_module`,
  `tests/unit/decode/runtime/test_headless.py::test_an_unrecorded_run_drives_the_bare_agent`,
  `tests/unit/decode/test_cli.py::test_importing_the_cli_does_not_import_kitaru`.
- [x] AC2 wrapped with the configured agent id —
  `test_headless.py::test_a_recorded_run_executes_through_the_kitaru_wrapper`,
  `test_recording.py::test_the_wrap_carries_the_configured_agent_id_and_the_session_name`.
- [x] AC3 ONE warning, run completes, exit 0 —
  `test_recording.py::test_the_degrade_costs_exactly_one_warning_line_naming_the_workspace`,
  `test_headless.py::test_an_unreachable_workspace_still_completes_the_run`, plus the live
  `decode run` evidence below (exit 0, the run continued past the seam).
- [x] AC4 non-zero + clear error under `KITARU_TASK_ID` —
  `test_recording.py::test_a_worker_task_hard_fails_when_the_workspace_is_unreachable`,
  `test_run_command.py::test_run_exits_non_zero_when_a_worker_task_cannot_be_recorded`, plus the
  live `decode run` evidence below (exit 1, empty stdout).
- [x] AC5 `.env.example` + drift green —
  `tests/unit/decode/config/test_env_example_drift.py` (both directions).
- [ ] [HUMAN] AC6 live proof against the managed workspace — an operator gate, NOT attempted here:
  it needs real workspace credentials and a paid model call, and it is the one thing the fakes
  cannot stand in for. Everything up to the network boundary is proved above.

**Evidence**

```
$ make pre-commit
uv run ruff format --check
302 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
============================ 2194 passed in 36.33s =============================

$ make integration-tests
================== 96 passed, 16 skipped in 274.14s (0:04:34) ==================
```

End-to-end, the real cli against a REAL (refused) workspace — no fakes in the loop:

```
$ KITARU_AGENT_ID=6f1d…0f0f KITARU_API_URL=http://127.0.0.1:9 decode run "say hello"
exit=0 (the run proceeded past the seam to the model), and .decode/logs/decode.log holds
exactly one line:
2026-08-22 02:44:41,293 WARNING decode.runtime.recording: [kitaru] not recording this run:
http://127.0.0.1:9 is unavailable (ConnectError: All connection attempts failed);
continuing on the bare agent

$ KITARU_TASK_ID=0f9d…0001 KITARU_API_URL=http://127.0.0.1:9 decode run "replay this"
exit=1
stdout: (empty)
stderr: decode.runtime.recording.RecordingUnavailableError: [kitaru] recording is unavailable
for this Kitaru Worker Task: http://127.0.0.1:9 could not be reached (ConnectError: All
connection attempts failed). Failing the run rather than producing an unrecorded — and
therefore untrustworthy — replay.
```

**Notes**
- **Where the ONE warning line lands.** It is a `logger.warning` (AGENTS.md: library code never
  prints; the task's own wording is "ONE stderr/log warning line"). `init_logger()` sends INFO+ to
  `<harness home>/.decode/logs/decode.log`, never the terminal — so under the real `decode run` the
  operator sees it in the log file, not on stderr. If the PA wants it on the terminal, that is a
  logging-config decision (surface WARNING+ on stderr for the headless entrypoint), not a change
  the seam can make on its own without breaking the no-print rule — flagged, not silently decided.
- `KITARU_API_URL` must be **exported**, not written in `.env`: `Settings` never writes to
  `os.environ`, so the adapter's client cannot see a dotenv-only value. Documented in `.env.example`.
- NOT RUN — AC6 (the [HUMAN] live proof) and any docker-backed integration test (no daemon here).

### [Tester] 2026-08-22 03:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` — 302 files formatted; `ruff check` — all
  checks passed)
- Unit tests: 2194 passed / 0 failed (also reran the full suite with `KITARU_API_URL` /
  `KITARU_AGENT_ID` / `KITARU_TASK_ID` exported to simulate a developer's live-workspace env —
  still 2194 passed, confirming the `tests/conftest.py::_no_kitaru_recording` hermeticity guard
  actually scrubs)
- Integration tests: 96 passed / 0 failed, 16 skipped (docker daemon not reachable on this host —
  pre-existing, matches SWE's evidence)
- Warnings: 0 (`pytest -W error` clean)
- `code-review` plugin is enabled in `.claude/settings.json` but not invocable as a tool from this
  agent's toolset (no Task/Agent/slash-command tool available here); substituted a full manual
  diff read + targeted greps (no `print()` in the new module, every function signature typed, no
  unrelated files in the diff) in its place.

**E2E adversarial pass**
- Happy path: `LLM_PROVIDER=gemini decode run "reply with exactly the word: pong"` (no kitaru env
  set) → `pong` on stdout, exit 0, no kitaru import (verified separately below) — PASS.
- Break path 1 (refused workspace, user-launched, real CLI, real refused socket, real Gemini call):
  `LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d…0f0f KITARU_API_URL=http://127.0.0.1:9 decode run
  "reply with exactly the word: pong"` → exit 0, stdout `pong`, **stderr empty**, one
  `WARNING decode.runtime.recording` line appended to `.decode/logs/decode.log` only. Expected
  (AC3 + decode's own stated convention, see below): the run must complete + exit 0 (met), but the
  warning must be visible to the operator running the command, not silently filed in a log they
  are not tailing — FAIL on visibility.
- Break path 2 (refused workspace, worker-spawned): `LLM_PROVIDER=gemini
  KITARU_TASK_ID=0f9d…0001 KITARU_API_URL=http://127.0.0.1:9 decode run "reply with exactly the
  word: pong"` → exit 1, stdout empty, stderr ends with `decode.runtime.recording.
  RecordingUnavailableError: [kitaru] recording is unavailable for this Kitaru Worker Task: …`
  (wrapped in a full Python traceback, unhandled-exception style rather than the guard-chain's
  usual clean `click.echo(..., err=True)` line) — matches AC4's letter ("clear error naming the
  recording failure" — it does), PASS with note (see Other issues found).
- Break path 3 (malformed `KITARU_AGENT_ID`, both modes): user-launched
  `KITARU_AGENT_ID=not-a-uuid KITARU_API_URL=http://127.0.0.1:9 decode run "…"` → exit 0, stdout
  `pong`, one `WARNING …(ValueError: badly formed hexadecimal UUID string)…` line in the log
  (same stderr-visibility gap as break path 1); worker-spawned equivalent
  (`KITARU_TASK_ID=…&KITARU_AGENT_ID=not-a-uuid`) → exit 1, empty stdout, `RecordingUnavailableError`
  naming the cause on stderr. Both PASS on behavior (degrade vs hard-fail split correctly triggered
  by a malformed id, not just a network failure).
- Break path 4 (hermeticity guard under a simulated developer env): ran
  `KITARU_API_URL=http://127.0.0.1:9 KITARU_AGENT_ID=6f1d…0f0f KITARU_TASK_ID=deadbeef-…
  pytest tests/unit` (full suite) — 2194 passed, no test attempted a network call or hard-failed —
  PASS, the `_no_kitaru_recording` autouse fixture scrubs correctly.
- Fresh-interpreter no-import check (independently reproduced, not just re-running the SWE's test):
  a subprocess with `DECODE_ENV` / `KITARU_*` scrubbed imports `decode.runtime` +
  `decode.runtime.recording`, calls `wrap_for_recording` unconfigured, and asserts no `kitaru*`
  module in `sys.modules` afterward → `NO_KITARU_OK`, rc 0 — PASS.

**Ruling on the two flagged design decisions**

1. **Degrade warning visibility — REQUIRE stderr; this is the reason for the FAIL.** decode's own
   `run` command docstring says "diagnostics go to stderr" and "Guards (same
   friendly-line-on-stderr, non-zero-exit contract as the REPL)"; the established idiom for a
   non-blocking, run-proceeds-anyway notice already exists verbatim in `cli.py` —
   `_context_window_notice()` returns `str | None`, and `run()` does
   `click.echo(window_notice, err=True)` before the agent runs. AC3 says "prints exactly ONE
   warning line" — "prints" means visible to the operator invoking the command, not "appended to
   a log file at `<harness home>/.decode/logs/decode.log` that nobody is tailing during a headless
   run." I independently confirmed by running the real CLI (break path 1 above): stderr was
   byte-empty while the warning sat in the log. A `logger.warning` call is fine to KEEP (so the log
   file also carries it, useful for post-hoc debugging / a Kitaru Worker's log aggregation), but it
   is not sufficient alone — the seam (or `headless.py`/`cli.py`) must also surface the same one
   line to stderr for a user-launched run. Concrete fix options, both idiomatic for this codebase:
   (a) have `wrap_for_recording` return `(agent, warning_message | None)` (or stash the message
   somewhere `run_headless_task` can read) so `_run_task`/`run_headless_task` can echo it once
   `asyncio.run` returns, mirroring the `_context_window_notice` pattern; or (b) have
   `run_headless_task` (already the headless-only entrypoint, not a shared library boundary) import
   `click` directly and `click.echo(msg, err=True)` right where the log call happens. Either is
   fine; silently leaving the warning in the log file only is not. This is a FAIL, not a nit — it
   contradicts the AC's literal wording and the codebase's own already-established convention for
   this exact class of notice.
2. **Worker Task ⇒ configured even without `KITARU_AGENT_ID` — acceptable, not blocking.** This is
   the PA's call per the task brief, but for the record: I agree with the SWE's reasoning. ADR-0019
   §3's hard-fail clause ("Worker-spawned runs … HARD-FAIL — an unrecorded replay is a lying
   experiment") is unconditional on `KITARU_TASK_ID` presence, and the adapter infers the agent id
   from the task itself — so the literal AND-gate in the same paragraph is arguably underspecified
   for this case, not contradicted. The alternative (worker task + no agent id ⇒ "not configured"
   ⇒ silently unrecorded) is exactly the lying-experiment failure mode §3 exists to forbid. Verified
   this behavior directly: `tests/unit/decode/runtime/test_recording.py::
   test_a_worker_task_is_recorded_even_without_an_agent_id` and
   `::test_a_worker_task_probes_reachability_without_an_agent_id` both pass, and match the
   `is_worker_task()` / `recording_is_configured()` code at
   `src/decode/runtime/recording.py:65-72`. Flagging for the PA to consider whether ADR-0019 §3
   should be reworded to state this explicitly, not asking the SWE to change it.

**Acceptance criteria**
- [x] PASS — AC1 (bare + no kitaru import) — reproduced independently: fresh-interpreter subprocess
      (see above), `NO_KITARU_OK`, rc 0; `tests/unit/decode/test_cli.py::
      test_importing_the_cli_does_not_import_kitaru` passes; `tests/unit/decode/runtime/
      test_recording.py::test_the_unconfigured_seam_imports_no_kitaru_module_in_a_fresh_interpreter`
      passes.
- [x] PASS — AC2 (wrapped with configured agent id, reachable server) —
      `tests/unit/decode/runtime/test_headless.py::
      test_a_recorded_run_executes_through_the_kitaru_wrapper` and `tests/unit/decode/runtime/
      test_recording.py::test_the_wrap_carries_the_configured_agent_id_and_the_session_name` pass;
      code at `src/decode/runtime/recording.py:143` constructs `KitaruAgent(agent,
      agent_id=agent_id, session_name=session_name)` with `agent_id` parsed from
      `settings.kitaru_agent_id`.
- [ ] FAIL — AC3 (ONE warning line, completes, exits 0) — the "completes, exits 0" half is verified
      (unit: `test_headless.py::test_an_unreachable_workspace_still_completes_the_run`; live:
      `LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d…0f0f KITARU_API_URL=http://127.0.0.1:9 decode run
      "reply with exactly the word: pong"` → exit 0, stdout `pong`). The "prints" half is not: the
      warning line lands only in `.decode/logs/decode.log`, never on stderr — confirmed empty
      stderr on the live run above.
      Expected: the operator running `decode run` sees the one warning line on their terminal
      (stderr), per AC3's wording and the codebase's own `_context_window_notice` /
      friendly-line-on-stderr convention (`src/decode/cli.py:360-362`).
      Actual: `logger.warning(...)` only (`src/decode/runtime/recording.py:150-154`); nothing
      reaches stderr.
      Fix: surface the same one line to stderr from `run_headless_task` / `cli.py`'s `run()` — see
      ruling 1 above for two concrete options.
- [x] PASS — AC4 (non-zero + clear error under `KITARU_TASK_ID`) — reproduced live:
      `LLM_PROVIDER=gemini KITARU_TASK_ID=0f9d…0001 KITARU_API_URL=http://127.0.0.1:9 decode run
      "…"` → exit 1, empty stdout, `RecordingUnavailableError` naming the workspace and the cause
      on stderr; unit: `test_recording.py::test_a_worker_task_hard_fails_when_the_workspace_is_
      unreachable`, `test_run_command.py::test_run_exits_non_zero_when_a_worker_task_cannot_be_
      recorded`. Note: the live error surfaces as a full Python traceback rather than a clean
      guard-chain line — see Other issues found; not required by AC4's wording so not blocking.
- [x] PASS — AC5 (`.env.example` + drift green) — `tests/unit/decode/config/
      test_env_example_drift.py` both directions pass; `KITARU_AGENT_ID` documented as a `# KEY=`
      line (counts per the drift test's own regex), `KITARU_API_URL`/`KITARU_API_KEY` documented as
      prose (correctly NOT as `KEY=` lines, since they're process-env-only and a `KEY=` line would
      be a lie the drift test would also catch under `test_every_env_example_key_is_a_real_
      settings_field`).
- [ ] AWAITING HUMAN — AC6 ([HUMAN] live proof against the managed workspace) — correctly left
      unchecked; not attempted (needs real workspace credentials + a paid model call, per the SWE's
      own note).

**Evidence**
```
$ uv run pytest tests/unit -q -W error
2194 passed in 36.93s

$ KITARU_API_URL=http://127.0.0.1:9 KITARU_AGENT_ID=6f1d6b6a-... KITARU_TASK_ID=deadbeef-... \
    uv run pytest tests/unit -q -W error
2194 passed in 36.87s   # hermeticity guard holds under a simulated developer env

$ uv run pytest tests/integration -q
96 passed, 16 skipped in 271.77s (0:04:31)

$ LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d6b6a-6f6f-4c0a-9c9a-0f0f0f0f0f0f \
    KITARU_API_URL=http://127.0.0.1:9 decode run "reply with exactly the word: pong"
exit=0
stdout: pong
stderr: (empty)
.decode/logs/decode.log (new line): WARNING decode.runtime.recording: [kitaru] not recording
this run: http://127.0.0.1:9 is unavailable (ConnectError: All connection attempts failed);
continuing on the bare agent

$ LLM_PROVIDER=gemini KITARU_TASK_ID=0f9d1a3e-0000-4000-8000-000000000001 \
    KITARU_API_URL=http://127.0.0.1:9 decode run "reply with exactly the word: pong"
exit=1
stdout: (empty)
stderr (tail): decode.runtime.recording.RecordingUnavailableError: [kitaru] recording is
unavailable for this Kitaru Worker Task: http://127.0.0.1:9 could not be reached (ConnectError:
All connection attempts failed). Failing the run rather than producing an unrecorded — and
therefore untrustworthy — replay.
```

**Other issues found**
- AC4's live error surfaces as a full unhandled-exception traceback on stderr rather than the
  guard chain's usual one-line `click.echo(f"Decode: {msg}", err=True)` treatment used elsewhere in
  `cli.py` (e.g. `_sandbox_config_error`, unknown `--agent`). Not a blocker — AC4 only requires a
  non-zero exit and a clear error naming the failure, both true — but worth a follow-up polish pass
  so a Kitaru Worker's captured stderr is a one-liner rather than 40+ lines of framework frames.
- The live `KITARU_API_URL=http://127.0.0.1:9` run against the real Modal-backed default provider
  (`LLM_PROVIDER=modal` in this workspace's `.env`) hit a persistent `503` from the configured Modal
  endpoint (unrelated to this task — a cold/unavailable serving endpoint) — worked around by
  switching to `LLM_PROVIDER=gemini` for the live checks above; not a decode bug, flagging only so
  the next tester isn't surprised by the same 503 on this host.

**VERDICT: FAIL**

One blocking issue: AC3's "prints exactly ONE warning line" is not met on the terminal (stderr) —
only in the log file. Everything else (AC1, AC2, AC4, AC5, the import invariant, the hermeticity
guard, the presence gate, the malformed-id handling, and the full regression suite) verified clean.
Design decision 2 (Worker Task widens the configured gate) is accepted as-is, flagged for the PA
per the task brief.

### [SWE] 2026-08-22 03:20 — Fixes (Tester round 1)

**Blocking fix — AC3: the degrade warning now reaches the operator's stderr**

The seam stays print-free (it is shared library code; task 135 wires it into the REPL, where a stray
stderr write would fight prompt_toolkit for the terminal), so the caller owns the surface — the
`(value, notice)` shape `prepare_workspace_or_empty` already uses in this package.

- `src/decode/runtime/recording.py` — `wrap_for_recording` now returns
  `tuple[AbstractAgent, str | None]`: the agent to run, plus the ONE degrade line or `None`. The
  degrade path builds the message once, `logger.warning("%s", notice)`s it (log file unchanged) and
  hands the same string back. Hard-fail and happy paths return `(agent, None)`.
- `src/decode/runtime/headless.py` — `_run_task` unpacks the pair and
  `click.echo(recording_notice, err=True)` before the agent runs (so the operator sees it *before*
  the run burns tokens, not after), mirroring `cli.py::_context_window_notice`. stdout untouched.
- `src/decode/cli.py` — `run()` docstring documents the recording stderr contract.

**Non-blocking polish — the worker hard-fail is a one-liner, not a traceback**

- `src/decode/cli.py` — `run()` wraps `run_headless_task` in `except RecordingUnavailableError`:
  `logger.warning(..., exc_info=True)` (traceback stays in `.decode/logs/decode.log`) +
  `click.echo(f"Decode: {exc}", err=True)` + `Exit(1)` — the same guard-chain idiom as
  `_sandbox_config_error` / unknown `--agent`. The import sits inside the subcommand with the runner
  import, so the REPL path still loads no headless machinery and imports no kitaru.

**Tests**
- Unit: 2196 passing, 0 failing (was 2194; +2 net new).
- Integration: N/A — no infra change (recording is faked at the `sys.modules` boundary; the two live
  checks below use a refused socket).
- New / changed:
  - `test_headless.py::test_the_degrade_warning_reaches_the_operators_stderr` (new) — asserts
    `capsys` stderr == the logged line + `\n`, names the workspace, no `Traceback`, and stdout stays
    empty. This is the test that was missing and it fails on the pre-fix code (stderr was `""`).
  - `test_headless.py::test_a_recorded_run_executes_through_the_kitaru_wrapper` — now also asserts a
    successful recording prints NOTHING to stderr (only a loss is news).
  - `test_run_command.py::test_run_recording_hard_failure_is_a_friendly_line_not_a_traceback` (new) —
    stderr is exactly `Decode: [kitaru] recording is unavailable …\n`;
    `::test_run_exits_non_zero_when_a_worker_task_cannot_be_recorded` now asserts the message on
    stderr + empty stdout instead of an escaping exception.
  - `test_recording.py` — unpack the pair throughout; the degrade tests assert
    `notice == warnings[0].getMessage()` (one message, two sinks — they cannot drift) and that the
    returned copy is single-line; the fresh-interpreter no-import check asserts `== (agent, None)`.

**Acceptance criteria**
- [x] AC3 ONE warning line, completes, exit 0 — now on **stderr** as well as the log:
  `test_headless.py::test_the_degrade_warning_reaches_the_operators_stderr`,
  `test_recording.py::test_the_degrade_costs_exactly_one_warning_line_naming_the_workspace`, plus the
  live evidence below.
- [x] AC1, AC2, AC4, AC5 unchanged and still green (full suite below).
- [ ] [HUMAN] AC6 live proof against the managed workspace — still an operator gate, NOT attempted.

**Evidence**

```
$ make format-fix && make lint-fix && make format-check && make lint-check
uv run ruff format        → 302 files left unchanged
uv run ruff check --fix   → All checks passed!
uv run ruff format --check→ 302 files already formatted
uv run ruff check         → All checks passed!

$ make pre-commit
============================ 2196 passed in 36.71s =============================
```

End-to-end, the real cli against a REAL (refused) workspace and a real Gemini call — no fakes:

```
$ LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d…0f0f KITARU_API_URL=http://127.0.0.1:9 \
    decode run "reply with exactly the word: pong"
exit=0
stdout: pong
stderr: [kitaru] not recording this run: http://127.0.0.1:9 is unavailable (ConnectError: All
connection attempts failed); continuing on the bare agent          # ONE line, no traceback
.decode/logs/decode.log: the same line still filed there too

$ LLM_PROVIDER=gemini KITARU_TASK_ID=0f9d…0001 KITARU_API_URL=http://127.0.0.1:9 \
    decode run "reply with exactly the word: pong"
exit=1
stdout: (empty)
stderr (1 line): Decode: [kitaru] recording is unavailable for this Kitaru Worker Task:
http://127.0.0.1:9 could not be reached (ConnectError: All connection attempts failed). Failing the
run rather than producing an unrecorded — and therefore untrustworthy — replay.
.decode/logs/decode.log: WARNING decode.cli: recording unavailable for a Kitaru Worker Task;
failing  + the full traceback
```

**Notes**
- The stderr line is the message VERBATIM, no `Decode:` prefix, on the degrade path — one string,
  two sinks, so the log and the terminal can never disagree (a test pins the equality). The hard-fail
  path does carry the `Decode:` prefix, because there it is a guard line like every other one in
  `cli.py`.
- `wrap_for_recording`'s return type changed (`agent` → `(agent, notice)`). Task 135's REPL wiring
  inherits the same shape and should route the notice to the TUI's own output rather than stderr.
- NOT RUN — AC6 ([HUMAN] live proof) and the docker-backed integration tests (no daemon on this host,
  pre-existing).

### [Tester] 2026-08-22 03:35 — Re-QA (round 2)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` — 302 files formatted; `ruff check` —
  all checks passed; `make pre-commit` — 2196 passed)
- Unit tests: 2196 passed / 0 failed (matches SWE's claim; +2 net new vs round 1's 2194)
- Integration tests: spot-checked `tests/integration/test_opik_headless_trace.py` (3 passed, the
  closest-touching integration surface — the fix has no infra/docker footprint per the SWE's own
  note); not re-run in full since this fix round touches no sandbox/docker code path (round 1
  already proved 96 passed / 16 skipped and nothing in this diff changes that surface)
- Warnings: 0 (`pytest -q -W error` clean, both plain and full `make pre-commit`)

**Re-review of changed files (`git diff` since round 1)**
- `src/decode/runtime/recording.py` — `wrap_for_recording` now returns
  `tuple[AbstractAgent, str | None]`; degrade path builds ONE `notice` string, `logger.warning`s
  it, and returns the SAME string as the second element (one string, two sinks — read at
  `src/decode/runtime/recording.py:159-164`). Happy/hard-fail paths return `(agent, None)`. Seam
  itself still never touches stdio (no `click`/`print` import in this module) — consistent with
  the stated "REPL wiring in 135 must route to TUI, not stderr" constraint.
- `src/decode/runtime/headless.py` — `_run_task` unpacks the pair and
  `click.echo(recording_notice, err=True)` **before** `agent.run()` is awaited (line 178-179), so
  the operator sees the loss before the run spends tokens, matching the SWE's stated intent.
  stdout path (`click.echo(output)` in `cli.py`) is untouched.
- `src/decode/cli.py` — `run()` now wraps `run_headless_task` in
  `except RecordingUnavailableError`: `logger.warning(..., exc_info=True)` (traceback → log only)
  + `click.echo(f"Decode: {exc}", err=True)` + `raise click.exceptions.Exit(1)`, the same
  guard-chain idiom as `_sandbox_config_error`. Import is local to the subcommand, so the REPL
  path still loads no headless machinery.
- Read `tests/unit/decode/runtime/test_headless.py` and `test_run_command.py` diffs in full: the
  new `test_the_degrade_warning_reaches_the_operators_stderr` pins `capsys` stderr ==
  `f"{logged}\n"`, asserts `"Traceback" not in printed.err`, and `printed.out == ""` — this is
  exactly the shape that was empty under round 1's code (single-value `wrap_for_recording`, no
  `click.echo` anywhere in `_run_task`; confirmed via `git diff HEAD` — the `click.echo(...,
  err=True)` line and the `(agent, notice)` unpack are net-new hunks, not present before this
  fix), so the test genuinely pins the regression rather than trivially passing either way. The
  new `test_run_recording_hard_failure_is_a_friendly_line_not_a_traceback` pins
  `result.stderr == "Decode: [kitaru] recording is unavailable...\n"` verbatim.

**E2E adversarial pass — re-ran both round-1 break paths against the fixed code, real CLI, no fakes**
- Happy path (regression check, unchanged): `LLM_PROVIDER=gemini decode run "reply with exactly
  the word: pong"` (no kitaru env) → stdout `pong`, exit 0 — PASS (unit-verified via
  `test_an_unrecorded_run_drives_the_bare_agent`; not re-run live since round 1 already proved it
  live and nothing in this diff touches the unconfigured path).
- Break path 1 (refused workspace, user-launched, real CLI, real refused socket, real Gemini
  call): `LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d6b6a-6f6f-4c0a-9c9a-0f0f0f0f0f0f
  KITARU_API_URL=http://127.0.0.1:9 decode run "reply with exactly the word: pong"` → exit 0,
  stdout = `pong` exactly, **stderr = exactly 1 line** (`wc -l` = 1, 144 bytes, no `Decode:`
  prefix, no `Traceback`): `[kitaru] not recording this run: http://127.0.0.1:9 is unavailable
  (ConnectError: All connection attempts failed); continuing on the bare agent`. The same line is
  still filed in `.decode/logs/decode.log` (grepped, present). This is the exact scenario round
  1 FAILed on (stderr was byte-empty then) — now PASS.
- Break path 2 (refused workspace, worker-spawned, real CLI): `LLM_PROVIDER=gemini
  KITARU_TASK_ID=0f9d1a3e-0000-4000-8000-000000000001 KITARU_API_URL=http://127.0.0.1:9 decode
  run "reply with exactly the word: pong"` → exit 1, stdout empty, **stderr = exactly 1 line**
  (`wc -l` = 1): `Decode: [kitaru] recording is unavailable for this Kitaru Worker Task:
  http://127.0.0.1:9 could not be reached (ConnectError: All connection attempts failed).
  Failing the run rather than producing an unrecorded — and therefore untrustworthy — replay.`
  No traceback on stderr. `.decode/logs/decode.log` still carries the full Python traceback for
  post-hoc debugging (tailed, confirmed present) — PASS, and resolves round 1's "Other issues
  found" polish note as a side effect.
- Break path 3 (fresh-interpreter no-import invariant, independently reproduced): subprocess with
  `env -i` (fully scrubbed env) imports `decode.cli`, `decode.runtime`,
  `decode.runtime.recording` and asserts no `kitaru*` module lands in `sys.modules` → prints
  `NO_KITARU_OK`, rc 0 — PASS, confirms the fix didn't move a kitaru import to module level.

**Acceptance criteria**
- [x] PASS — AC1 (bare + no kitaru import) — spot-checked, unchanged and still green:
      `tests/unit/decode/test_cli.py::test_importing_the_cli_does_not_import_kitaru` and
      `tests/unit/decode/runtime/test_recording.py::
      test_the_unconfigured_seam_imports_no_kitaru_module_in_a_fresh_interpreter` pass; live
      `env -i` fresh-interpreter check above → `NO_KITARU_OK`.
- [x] PASS — AC2 (wrapped with configured agent id, reachable server) — spot-checked, unchanged
      and still green: `test_headless.py::test_a_recorded_run_executes_through_the_kitaru_wrapper`
      now additionally asserts a successful recording writes NOTHING to stderr; passes.
- [x] PASS — AC3 (ONE warning line, completes, exits 0) — **now fully met, including the
      "prints" half that round 1 FAILed on.** Live evidence: refused-socket run above → exit 0,
      stdout `pong`, stderr exactly 1 line (144 bytes) naming `http://127.0.0.1:9`, no traceback;
      same line also still in the log file. Unit:
      `test_headless.py::test_the_degrade_warning_reaches_the_operators_stderr` (pins stderr ==
      logged line + `\n`, single-line, no `Traceback`, stdout untouched),
      `test_recording.py::test_the_degrade_costs_exactly_one_warning_line_naming_the_workspace`.
- [x] PASS — AC4 (non-zero + clear error under `KITARU_TASK_ID`) — spot-checked, unchanged and
      still green; the round-1 "Other issues found" polish note (traceback on stderr instead of a
      one-liner) is now also fixed as a side effect of the AC3 fix: live evidence above → exit 1,
      empty stdout, stderr exactly 1 line with the `Decode:` guard-line prefix, no traceback.
      `test_run_command.py::test_run_recording_hard_failure_is_a_friendly_line_not_a_traceback`
      (new), `::test_run_exits_non_zero_when_a_worker_task_cannot_be_recorded` (updated).
- [x] PASS — AC5 (`.env.example` + drift green) — spot-checked, unchanged and still green:
      `tests/unit/decode/config/test_env_example_drift.py` both directions pass; `git diff` shows
      no changes to `.env.example` or `settings.py` in this fix round.
- [ ] AWAITING HUMAN — AC6 ([HUMAN] live proof against the managed workspace) — correctly left
      unchecked; not attempted (needs real workspace credentials + a paid model call).

**Evidence**
```
$ uv run pytest tests/unit -q -W error
2196 passed in 37.47s

$ make pre-commit
============================ 2196 passed in 37.03s =============================

$ uv run pytest tests/integration/test_opik_headless_trace.py -q
3 passed in 1.31s

$ LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d6b6a-6f6f-4c0a-9c9a-0f0f0f0f0f0f \
    KITARU_API_URL=http://127.0.0.1:9 uv run decode run "reply with exactly the word: pong" \
    1>stdout.txt 2>stderr.txt; echo "exit=$?"
exit=0
stdout.txt: pong
stderr.txt (wc -l = 1, wc -c = 144): [kitaru] not recording this run: http://127.0.0.1:9 is
unavailable (ConnectError: All connection attempts failed); continuing on the bare agent

$ LLM_PROVIDER=gemini KITARU_TASK_ID=0f9d1a3e-0000-4000-8000-000000000001 \
    KITARU_API_URL=http://127.0.0.1:9 uv run decode run "reply with exactly the word: pong" \
    1>stdout2.txt 2>stderr2.txt; echo "exit=$?"
exit=1
stdout2.txt: (empty)
stderr2.txt (wc -l = 1): Decode: [kitaru] recording is unavailable for this Kitaru Worker Task:
http://127.0.0.1:9 could not be reached (ConnectError: All connection attempts failed). Failing
the run rather than producing an unrecorded — and therefore untrustworthy — replay.
```

**Other issues found**
- None new. Round 1's "Other issues found" (hard-fail traceback on stderr) is resolved by this
  fix round, confirmed live above.
- Note for whoever picks up task 135 (REPL wiring): `wrap_for_recording`'s return shape is now
  `(agent, notice)`; the SWE's own note flags that the REPL must route `notice` to the TUI output,
  not stderr — worth keeping on the 135 checklist.

**VERDICT: PASS**

The single round-1 blocker (AC3's warning invisible on stderr) is fixed and independently
reproduced live against a real refused socket: stderr carries exactly one line in both the
degrade and hard-fail paths, stdout stays pure, the log file still carries the full detail for
post-hoc debugging, and the fix additionally resolves the "traceback on stderr" polish note from
round 1. Full unit suite green (2196/2196), 0 warnings, format/lint/pre-commit green, no
regressions in the spot-checked ACs (1, 2, 4, 5) or the touched integration surface. AC6 remains
correctly unchecked pending human live-workspace verification.

Handing off to PA for acceptance review.
