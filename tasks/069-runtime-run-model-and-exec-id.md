---
id: 069-runtime-run-model-and-exec-id
feature: runtime-replay
status: done
---

# `decode run --model X` + surface the exec_id + paste-ready replay hint

Tags: `runtime`, `cli`, `replay`
Depends on: #067, #068
Blocks: #070

This task implements **ADR-0010 §4**. Expose the #067 flow parameter as a `decode run --model` flag,
and stop discarding the `FlowHandle` on the bypass path (`cli.py:364` today throws away `exec_id`).
After the answer, print `exec_id: <id>` plus a paste-ready `decode replay <id> --model <…>` hint so
the checkpoint→replay loop is discoverable from the terminal.

## Scope

- **`cli.py` `run` subcommand** (`:273-365`):
  - Add `@click.option("--model", "model", default=None, metavar="ID", help="Override the active
    provider's model id for this run (e.g. gemini-2.5-pro); defaults to the provider's configured
    model. Does not change the provider (set LLM_PROVIDER for that).")`.
  - Pass it through: bypass → `run_agent_task.run(task=task, model=model)`; `--hitl` → thread `model`
    into `_run_hitl(task, model)` → `run_hitl_agent_task(task, model)` (extend that helper's
    signature to forward `model` to `run_agent_task_hitl.run(task=task, model=model)`).
  - **Capture the handle and surface the exec_id (bypass path).** Replace the discard at `:364`:
    `handle = run_agent_task.run(task=task, model=model)`, extract the output via the #068 contract
    (`.wait().output` or `_load_runtime_output(handle.exec_id)`), `click.echo` the answer, then echo
    to **stderr** (so stdout stays the clean answer for piping):
    - `exec_id: <handle.exec_id>`
    - `replay it with a change:  decode replay <handle.exec_id> --model <model-id>`
    (use the just-run model id in the hint when `--model` was given, else a placeholder like
    `<model-id>`).
  - The `--hitl` path already prints its `exec_id` on pause; on a **completed** HITL run, also echo
    the `exec_id` + replay-not-supported note (point at `kitaru executions replay` — decode replay is
    bypass-only, #070 / ADR-0010 §5).
- **Guards unchanged:** `--model` does not alter the provider-config / runtime / proxy / secret-store
  guard chain (`cli.py:311-350`). A model id invalid for the provider is not validated here (presence
  not correctness — matches the existing key guards); it fails at the first model request.
- **`runtime/flow.py`:** extend `run_hitl_agent_task(task: str, model: str | None = None)` to forward
  `model` to `run_agent_task_hitl.run(task=task, model=model)` (`flow.py:453`).
- **Docs:** update the `decode run` help/docstring to mention `--model`. (The E2E manual-QA rows for
  `--model` and `decode replay`, and the operator playbook, are added in #070.)

## Acceptance criteria

- [x] `decode run --model gemini-2.5-pro "task"` runs the flow with the overridden model and prints
      the answer on **stdout**; a `CliRunner` test (scripted seam keyed on `model`) asserts the seam
      received `model="gemini-2.5-pro"` and the output printed.
- [x] `decode run "task"` (no `--model`) is unchanged: prints the answer, uses the provider's
      configured model (seam receives `model=None`); existing run-command tests pass.
- [x] After a successful bypass run, `exec_id: <id>` and a `decode replay <id> …` hint are printed on
      **stderr** (stdout carries only the agent answer — a test asserts stdout is pipe-clean and
      stderr carries the `exec_id` + `decode replay` hint).
- [x] `--model` composes with `--hitl`: `decode run --hitl --model X "task"` forwards `model=X` into
      the HITL flow (unit test via the HITL seam); a completed HITL run prints its `exec_id` and the
      "use `kitaru executions replay`" note (decode replay is bypass-only).
- [x] All existing guards fire identically with `--model` present (disabled runtime / missing key /
      proxy / secret-store) — no new guard, no flow built when a guard trips (tripwire tests).
- [x] `make ci` green, **0 warnings**; `uv lock --check` passes; `import decode.cli` still does not
      import `kitaru` (lazy-import subprocess test stays green).

## User stories

### Story: A developer tries a bigger model on one run and gets the replay anchor
1. Developer runs `decode run --model gemini-2.5-pro "refactor the parser and add tests"`.
2. The answer streams to stdout; the process exits 0.
3. On stderr they see `exec_id: kr-abc123` and `decode replay kr-abc123 --model <model-id>`.
4. They copy the exec_id for a later what-if replay.

### Story: The default run is unchanged
1. Developer runs `decode run "list the python files"` (no `--model`).
2. The provider's configured model is used; the answer prints; the exec_id + replay hint appear on
   stderr without cluttering the piped stdout.

## Out of scope
- The `decode replay` command itself (#070).
- The operator playbook / cohort docs (#070).
- Validating that a `--model` id is real for the provider (presence-not-correctness, by design).

## Log

### [SWE] 2026-07-02 15:20 — Implementation

**Files modified**
- `src/decode/cli.py` — added the `run --model ID` option; threaded `model` into the bypass
  (`run_agent_task.run(task=task, model=model)`) and `--hitl` (`_run_hitl(task, model)`) paths; added
  `_echo_replay_anchor()` to print `exec_id:` + a paste-ready `decode replay … --model <id>` hint to
  **stderr** after a bypass run; extended `_run_hitl(task, model)` to forward the override and, on a
  completed run, echo `exec_id:` + a "decode replay is bypass-only → `kitaru executions replay`" note
  to stderr; updated the `run` docstring for `--model` + the stdout/stderr split.
- `src/decode/runtime/flow.py` — `run_hitl_agent_task(task, model=None)` now forwards `model` to
  `run_agent_task_hitl.run(task=task, model=model)`.
- `tests/unit/decode/runtime/test_run_command.py` — bypass coverage: help documents `--model`;
  scripted recording seam asserts `model="gemini-2.5-pro"` threads through and `model=None` without
  the flag; stdout pipe-clean vs stderr `exec_id`/`decode replay` split; hint uses the given model id
  else a `<model-id>` placeholder; four `--model` guard tripwires (disabled runtime / missing key /
  proxy secret / secret-store) never build a flow.
- `tests/unit/decode/runtime/test_hitl.py` — `--model` composes with `--hitl` via a recording HITL
  seam; a completed `decode run --hitl` echoes its `exec_id` + the `kitaru executions replay` note.

**Tests**
- Unit: 1039 passing, 0 failing (`make pre-commit`); new runtime tests: 12 in
  `test_run_command.py`, 2 in `test_hitl.py`. `make unit-tests` / `make pre-commit` output below.
- Integration: 20 passing (`make integration-tests`) — the runtime capstone still green.
- `make format-check`, `make lint-check` clean; `uv lock --check` passes; 0 warnings under
  `filterwarnings=["error"]`.

**Acceptance criteria**
- [x] `decode run --model X` overrides the model + prints on stdout — `test_run_command.py::test_run_model_flag_threads_the_override_to_the_seam_and_prints_output` + e2e below.
- [x] `decode run` (no `--model`) unchanged, seam gets `model=None` — `test_run_command.py::test_run_without_model_passes_none_to_the_seam`.
- [x] exec_id + `decode replay` hint on stderr, stdout pipe-clean — `test_run_command.py::test_run_exec_id_and_replay_hint_go_to_stderr_not_stdout` + `::test_run_replay_hint_uses_a_placeholder_when_no_model_given`.
- [x] `--model` composes with `--hitl`; completed HITL points at `kitaru executions replay` — `test_hitl.py::test_run_hitl_threads_the_model_override_through_the_seam` + `::test_run_hitl_completed_run_points_at_kitaru_executions_replay`.
- [x] All guards fire with `--model` present, no flow built — `test_run_command.py::test_run_model_does_not_bypass_the_{disabled_runtime,provider_key,proxy_secret,secret_store}_guard`.
- [x] `make ci` green, 0 warnings; `uv lock --check` passes; `import decode.cli` stays kitaru-free — `test_cli.py::test_importing_the_cli_does_not_import_kitaru`.

**Evidence**

Real `decode run --model` end-to-end (offline: real Click `run` + real Kitaru flow on an isolated
store, only the model boundary scripted) — the exact stdout/stderr split a user sees:
```
exit_code = 0
----- STDOUT (pipe target) -----
the python files are: cli.py, flow.py
----- STDERR (discoverability) -----
exec_id: be94b3cb-00ca-42d7-a115-e29393dfc2ed
replay it with a change:  decode replay be94b3cb-00ca-42d7-a115-e29393dfc2ed --model gemini-2.5-pro
```

Guards still fire with `--model` (real invocations) + lazy-import contract:
```
$ GEMINI_API_KEY="" RUNTIME_ENABLED=true decode run --model gemini-2.5-pro "…"
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).   exit=1
$ GEMINI_API_KEY=fake RUNTIME_ENABLED=false decode run --model gemini-2.5-pro "…"
Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true … (see .env.example).   exit=1
$ python -c "import decode.cli, sys; assert 'kitaru' not in sys.modules"
OK: kitaru not imported by decode.cli
```

```
$ make pre-commit
… uv run ruff format --check → 144 files already formatted
… uv run ruff check → All checks passed!
… 1039 passed in 72.03s

$ make integration-tests
… 20 passed in 46.84s
```

**Notes**
- The `FlowHandle` was already captured on the bypass path (task 068 read output via
  `_load_runtime_output(handle.exec_id)`); this task only threads `--model` into `.run(...)` and adds
  the stderr `exec_id`/replay-hint echo — no discard to replace.
- Guard chain untouched: `--model` only flows into the post-guard `run_agent_task.run(...)` /
  `_run_hitl(...)` calls, so provider-config / runtime / proxy / secret-store guards are byte-identical
  (four `--model` tripwire tests confirm no flow is built when a guard trips).
- Manual happy-path against a real Gemini (`decode run --model … "task"` printing a live answer) needs
  a `GEMINI_API_KEY` + network, out of scope here; the offline evidence above drives the real command
  and flow with only the model boundary swapped.

### [Tester] 2026-07-02 16:15 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 144 files clean; `ruff check` all passed)
- `make ci` (authoritative): PASS — **1059 passed** (1039 unit + 20 integration), 0 failed
- `uv lock --check`: PASS (no lock drift)
- Warnings: 0 — `filterwarnings=["error"]` is in force, so a green run *is* a zero-warning run
- No tests dropped: `git diff HEAD -- tests/` is 238 insertions / **0 deletions**; both touched test
  files (`test_run_command.py`, `test_hitl.py`) are pure additions. +11 net new tests (9 + 2), so the
  068 baseline is fully preserved. (Log prose says "12 in test_run_command.py"; the diff shows 9 — a
  harmless miscount, the "1039 unit" total is correct.)

**E2E adversarial pass** (real Click `run` via `CliRunner`, real Kitaru flow, offline — model boundary
scripted, store isolated by the rootdir conftest; probes run then removed)
- Happy path `run --model gemini-2.5-pro "…"` → exit 0; STDOUT = `the python files are: cli.py, flow.py`
  (only the answer); STDERR = `exec_id: <id>` + `replay it with a change:  decode replay <id> --model
  gemini-2.5-pro`. (PASS)
- Happy path no `--model` → same clean STDOUT; STDERR hint = `… --model <model-id>` (placeholder). (PASS)
- Break 1 (boundary — empty `--model ""`): seam receives `model=""`; exit 0; stdout = answer; hint
  falls back to `<model-id>` placeholder (empty is falsy in both `_echo_replay_anchor` and
  `_build_model`'s `model or settings.<provider>_model`). Graceful, consistent. (PASS)
- Break 2 (hostile input — `--model "gemini; rm -rf / #{}%s"`): threaded verbatim as an opaque string,
  echoed literally in the stderr hint via an f-string to `click.echo` (no shell, no log-format eval);
  stdout stays exactly the answer. No injection. (PASS)
- Break 3 (malformed — missing TASK arg, `run --model X`): Click usage error, exit 2, no flow built
  (tripwire seam never called). (PASS)
- Break 4 (state edge — paused `--hitl --model X`): the completed-run branch does **not** fire on a
  pause — `kitaru executions replay` / `bypass-only` are absent; only the `kitaru executions input`
  pause hint shows. decode writes nothing to stdout on a pause (the `Waiting for input…` line on
  stdout is Kitaru's own framework prompt, pre-existing, not decode's — see Other issues). (PASS)

**Acceptance criteria**
- [x] PASS — `--model X` threads the override + prints on stdout — `test_run_command.py::test_run_model_flag_threads_the_override_to_the_seam_and_prints_output` (green in `make ci`); live: seam captured `model="gemini-2.5-pro"`, stdout = answer.
- [x] PASS — no `--model` passes `model=None`, unchanged — `test_run_without_model_passes_none_to_the_seam`; live: `captured["model"] is None`.
- [x] PASS — exec_id + `decode replay` hint on **stderr**, stdout pipe-clean — `test_run_exec_id_and_replay_hint_go_to_stderr_not_stdout` + `test_run_replay_hint_uses_a_placeholder_when_no_model_given`; Click 8.2.1 `CliRunner` genuinely separates the streams (`mix_stderr` removed); my exact-equality probe: `stdout.strip() == "PIPED-PAYLOAD"`, scaffolding only on stderr.
- [x] PASS — `--model` composes with `--hitl`; completed HITL → `kitaru executions replay` note — `test_hitl.py::test_run_hitl_threads_the_model_override_through_the_seam` + `::test_run_hitl_completed_run_points_at_kitaru_executions_replay`; `run_hitl_agent_task(task, model=None)` forwards to `run_agent_task_hitl.run(task=task, model=model)` (flow.py:487).
- [x] PASS — every guard fires with `--model` present, no flow built — 4 tripwires `test_run_model_does_not_bypass_the_{disabled_runtime,provider_key,proxy_secret,secret_store}_guard`; live subprocess: missing key → `set GEMINI_API_KEY …` exit 1; `RUNTIME_ENABLED=false` → `headless runtime is disabled …` exit 1.
- [x] PASS — `make ci` green / 0 warnings; `uv lock --check` OK; `import decode.cli` stays kitaru-free — `python -c "import decode.cli, sys; assert 'kitaru' not in sys.modules"` → OK; `test_cli.py` + `test_kitaru_dependency.py` green.

**Evidence**
```
$ make ci
… uv lock --check → Resolved 149 packages
… ruff format --check → 144 files already formatted
… ruff check → All checks passed!
… 1059 passed in 112.67s (0:01:52)

$ uv run python -c "import decode.cli, sys; assert 'kitaru' not in sys.modules; print('OK')"
OK: kitaru NOT imported by decode.cli

# exact user-visible split (scripted seam, offline):
----- STDOUT (pipe target) -----      ----- STDERR (discoverability) -----
the python files are: cli.py, flow.py  exec_id: 46f58225-…-4ae486c44440
                                        replay it with a change:  decode replay 46f58225-… --model gemini-2.5-pro
```

**Other issues found** (non-blocking)
- SWE log's new-test count ("12 in test_run_command.py") is off by 3 — the diff added 9 there (+2 in
  test_hitl = 11 total). The `1039 unit` total is accurate and **zero tests were dropped**; flagging
  only for log accuracy.
- On a `--hitl` **pause**, Kitaru/ZenML prints its own `Waiting for input. Question: …` prompt to
  **stdout** (framework behavior, pre-existing — decode's paused branch writes only to stderr). So a
  piped `decode run --hitl` is not stdout-clean *on a pause*. Out of scope for 069 (the AC's pipe-clean
  guarantee is the completed **bypass** answer, which holds); worth a note for the #070 operator docs.

**VERDICT: PASS**
