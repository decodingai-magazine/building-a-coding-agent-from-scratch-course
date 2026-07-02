---
id: 069-runtime-run-model-and-exec-id
feature: runtime-replay
status: pending
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

- [ ] `decode run --model gemini-2.5-pro "task"` runs the flow with the overridden model and prints
      the answer on **stdout**; a `CliRunner` test (scripted seam keyed on `model`) asserts the seam
      received `model="gemini-2.5-pro"` and the output printed.
- [ ] `decode run "task"` (no `--model`) is unchanged: prints the answer, uses the provider's
      configured model (seam receives `model=None`); existing run-command tests pass.
- [ ] After a successful bypass run, `exec_id: <id>` and a `decode replay <id> …` hint are printed on
      **stderr** (stdout carries only the agent answer — a test asserts stdout is pipe-clean and
      stderr carries the `exec_id` + `decode replay` hint).
- [ ] `--model` composes with `--hitl`: `decode run --hitl --model X "task"` forwards `model=X` into
      the HITL flow (unit test via the HITL seam); a completed HITL run prints its `exec_id` and the
      "use `kitaru executions replay`" note (decode replay is bypass-only).
- [ ] All existing guards fire identically with `--model` present (disabled runtime / missing key /
      proxy / secret-store) — no new guard, no flow built when a guard trips (tripwire tests).
- [ ] `make ci` green, **0 warnings**; `uv lock --check` passes; `import decode.cli` still does not
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
