---
id: 058-runtime-durable-flow-and-decode-run
feature: kitaru-runtime
status: pending
---

# Durable headless flow + `decode run "<task>"` (first value, no HITL)

Tags: `runtime`, `cli`, `agent`
Depends on: #057
Blocks: #059, #060, #061, #062

This task implements ADR-0008 §1-2, §7-8: the **second entry path**. A new `src/decode/runtime/`
houses a Kitaru `@flow` that runs the **same** `build_agent()` (`agent/factory.py:68`) autonomously
via `KitaruAgent(...).run_sync(task)`, launched by a new `decode run "<task>"` subcommand. It
**bypasses** the interactive `Runner`/`agent/loop.py` (no mid-turn steering/abort — that is
interactive-only; headless gets Kitaru replay instead). The live TUI is untouched. This is the
first-value slice: checkpoints + replay work, **no HITL yet** (approvals run under `bypass`).

## Scope

- **`runtime/flow.py`** — a Kitaru durable flow (verify exact import paths/signatures against the
  installed SDK + context7 `/kitaru/adapters/pydantic-ai.md`, since pre-1.0):
  - `@flow def run_agent_task(task: str) -> str:` — **sync** (Kitaru flows are sync; the adapter
    bridges the async pydantic-ai agent internally, so **no manual asyncio/event loop** here).
  - Inside: build the agent through a **patchable runtime seam** (a module-level helper, e.g.
    `_build_runtime_agent()`, mirroring bash `_EXECUTOR` / lsp `_spawn_process` — so 062 can inject a
    scripted agent). The seam calls `build_agent()` and wraps it:
    `KitaruAgent(agent, checkpoint_strategy=settings.runtime_checkpoint_strategy)`.
  - Construct **headless** `AgentDeps`: `cwd=Path.cwd()`, a no-op/logging `emit` sink (no TUI), a
    `PermissionGate` in **`bypass` mode** (ADR-0003: every tool auto-allowed, no prompt — the
    simplest no-human posture so the autonomous run can actually do work; 059 layers durable
    approvals on top), and the existing headless decision resolvers
    (`deny_user_question_resolver` for `resolve_user_question`; a deny `resolve_permission`) so
    `ask_user`/`exit_plan_mode` raise→`ModelRetry` and the agent proceeds without a human. Under
    `bypass` no tool raises `ApprovalRequired`, so `run_sync` returns a clean `str` (no
    `DeferredToolRequests` to drive — that is 059's concern).
  - Call `KitaruAgent(...).run_sync(task)` and return the final text output.
- **`decode run "<task>"` subcommand** in `cli.py`:
  - Convert the `cli` entrypoint from `@click.command()` to a `@click.group(invoke_without_command=True)`
    so a **bare** `decode …` still launches the REPL with **all** existing flags (`--resume`,
    `--agent`, `--mode`) and startup guards unchanged (the M1 startup tests must still pass — assert
    this). When `ctx.invoked_subcommand is None`, run the existing REPL path verbatim.
  - Add a `run` subcommand taking one `TASK` argument that launches the flow:
    `result = run_agent_task.run(task=task).wait()` then `click.echo(result)`. Import `runtime/`
    **lazily inside the subcommand** (not at module load) so the REPL path never imports kitaru.
  - Guards: `decode run` reuses the provider-config startup guard (`_provider_config_error` — it
    builds a model) and adds a `runtime_enabled` guard (False → one friendly line on stderr, exit
    non-zero, no flow built).
- **Operator setup:** `decode run` requires a Kitaru project root (`.kitaru/`, created by
  `kitaru init`) and runs on the **local** stack (offline, no server needed for a local run).
  Document this in the README section + a friendly error if `.kitaru/` is absent (best-effort —
  surface Kitaru's own message rather than a raw traceback).
- **Docs:** add a short README "Headless runtime (`decode run`)" section; update AGENTS.md Project
  Structure (`runtime/` is now a concrete entry, like 055 did for `services/lsp/`), add a Kitaru
  **Tech Stack** row, and add a `decode run` row to the **Testing E2E** manual-QA table (type
  `decode run "list the python files"` → the agent tool-loops headlessly and prints a result; a
  re-run replays finished checkpoints from cache).

## Acceptance criteria

- [ ] `src/decode/runtime/flow.py` defines a sync `@flow run_agent_task(task: str) -> str` that wraps
      `build_agent()` in `KitaruAgent(checkpoint_strategy=settings.runtime_checkpoint_strategy)` and
      returns the agent's final text; a patchable `_build_runtime_agent` seam exists for tests.
- [ ] `decode run "<task>"` launches the flow (`run_agent_task.run(task=…).wait()`) and `click.echo`s
      the result; a CLI test (Click `CliRunner` + the runtime seam injecting a `TestModel`/`FunctionModel`
      agent, no network, no server) asserts the printed output equals the agent's output.
- [ ] **Backward compat:** a bare `decode` (no subcommand) still launches the REPL with `--resume` /
      `--agent` / `--mode` and every startup guard intact; the existing cli/startup unit tests still
      pass unchanged.
- [ ] `RUNTIME_ENABLED=false` → `decode run "x"` prints one friendly line on stderr and exits non-zero
      without building a flow; unit-tested.
- [ ] The provider-config guard fires for `decode run` too (e.g. missing `GEMINI_API_KEY` → the same
      friendly line, no traceback); unit-tested.
- [ ] A hermetic test proves a task round-trips through the **real** `@flow` + `KitaruAgent` with a
      scripted model on the **local** stack — **no network, no Kitaru server** (mirrors the LSP
      "patch the seam" posture; `kitaru init` / a `tmp_path` `.kitaru/` is set up by the test). The
      flow runs the agent loop and returns the scripted final text.
- [ ] **De-risk early:** an explicit check (a test or a recorded probe in the SWE log) confirms the
      async-pydantic-ai-agent ⇄ sync-`run_sync` bridge works against the installed adapter (ADR-0008
      §Consequences "Honest risk (a)" — now resolved); document the confirmed `KitaruAgent(...,
      checkpoint_strategy=…)` signature used.
- [ ] The interactive TUI path is byte-unchanged (no behavior diff in `agent/loop.py` / `tui/`).
- [ ] `make ci` green, 0 warnings; `uv lock --check` passes.

## User stories

### Story: A developer runs one autonomous task headlessly
1. Developer runs `kitaru init` once (creates `.kitaru/`).
2. Developer runs `decode run "summarize what the cli module does"`.
3. The agent tool-loops to completion with no prompts (bypass mode) and the final answer is printed
   to stdout; the process exits 0.

### Story: A crash mid-task does not re-bill finished turns
1. A multi-turn `decode run` task completes turn 1 (a checkpoint is written) then the process is
   killed before turn 2.
2. The developer re-runs the same task.
3. Turn 1 returns from the Kitaru cache (no model call) and only the unfinished work runs again —
   replay picks up near the crash, not from the top.

### Story: The REPL is unaffected by the new subcommand
1. A developer runs bare `decode --agent plan` exactly as before.
2. The TUI launches in plan mode; `--resume`, steering/abort, and every guard behave identically to
   the pre-runtime build.

### Story: The runtime is disabled
1. A developer sets `RUNTIME_ENABLED=false` and runs `decode run "x"`.
2. They see one friendly line ("the headless runtime is disabled — set RUNTIME_ENABLED=true …") on
   stderr and a non-zero exit; no flow is built.

## Out of scope
- HITL / durable waits for approvals or `ask_user` (task 059) — 058 runs under `bypass`, no pausing.
- `sleep` as a durable timer (task 060) — `sleep` stays `asyncio.sleep` here.
- Credentials proxy (task 061) — model keys still read from settings.
- The full capstone e2e (task 062).
- A deployable entrypoint / cron (deferred to step 12, ADR-0008 §6). **Note:** `KitaruAgent` message
  history is in-memory, so cross-process resume of *conversation* is limited — acceptable for a single
  autonomous task; do not attempt multi-turn conversation recovery here.

## Log
