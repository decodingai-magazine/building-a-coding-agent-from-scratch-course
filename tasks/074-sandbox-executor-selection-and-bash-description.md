---
id: 074-sandbox-executor-selection-and-bash-description
feature: sandboxing
status: pending
---

# Executor selection seam + mode-specific bash tool description + teardown wiring

Tags: `sandbox`, `agent`, `tools`
Depends on: #072, #073
Blocks: #075, #076, #077

This task implements ADR-0011 §4 — it makes `SANDBOX_MODE` **live**. The `bash` seam selects
`LocalExecutor` / `DockerExecutor` / `ModalExecutor` by mode (lazily), the `bash` tool **description
adapts per mode** so the model is never surprised by the semantics, and executor **teardown** is wired
into the app-exit path + the headless flow. `none` mode stays **byte-identical** to today.

## Scope

- **Selection seam** (`tools/bash.py` + `sandbox/__init__.py`):
  - Add `select_executor(mode) -> CommandExecutor` to `decode.sandbox` — `none` → `LocalExecutor()`
    (from `tools/exec.py`, importing **no** sandbox impl); `docker` → `DockerExecutor(...)`; `modal` →
    `ModalExecutor(...)` — each concrete sandbox impl **lazy-imported inside** the matching branch so
    `none` never imports docker/modal code.
  - In `bash.py`, replace the eager `_EXECUTOR = LocalExecutor()` with a cached lazy getter
    `_get_executor()` that populates `_EXECUTOR` from `select_executor(settings.sandbox_mode)` on first
    use; `bash()` calls `_get_executor().run(...)`. Keep `_EXECUTOR` **patchable** and add
    `reset_executor()` for tests (so a test can force a mode or inject a fake).
- **Mode-specific `bash` description** (via the registry's existing per-tool `prepare=` callback, which
  returns a `ToolDefinition`):
  - `none`: description **unchanged** (byte-identical — assert this).
  - `docker`: append a paragraph — commands run in a **persistent shell** inside a local container over
    the repo mounted at `/workspace`; `cd`/`export`/installs **persist across calls**; a timeout resets
    the shell (cwd/env cleared).
  - `modal`: append a paragraph — commands run in a **remote scratch sandbox** where the **local tree is
    NOT present** (git clone/fetch/generate to work with code); filesystem persists across calls, shell
    cwd/env reset per call.
  - **Verify-first:** confirm mutating `ToolDefinition.description` inside `prepare` takes effect on the
    installed pydantic-ai (`pydantic-ai-slim` 1.95); record the finding. If mutation-in-`prepare` is not
    supported, fall back to composing the description at registration keyed on `settings.sandbox_mode`.
- **Teardown wiring:** add `close_executor()` (best-effort `aclose()` on the cached executor, then reset)
  and call it:
  - in the `run_app` exit path next to `shutdown_lsp_servers()` + the memory write-back
    (`tui/app.py:943-952`), non-fatal;
  - at headless flow completion (bypass + HITL) in `runtime/flow.py`, so a `decode run` reaps its
    container/sandbox. `--rm` (docker) / modal `timeout` remain the crash backstops.

## Acceptance criteria

- [ ] `SANDBOX_MODE=none` (default): `bash` uses `LocalExecutor`, the tool description is **byte-identical**
  to today, and importing `decode.tools.bash` / building the agent imports **no** docker/modal sandbox
  module — a test asserts `decode.sandbox.docker_executor` / `modal_executor` are absent from
  `sys.modules` on the `none` path.
- [ ] `SANDBOX_MODE=docker`: `_get_executor()` returns a `DockerExecutor` (patched/faked — no real
  daemon needed) and the `bash` description contains the persistent-shell/`/workspace` paragraph; a test
  asserts both.
- [ ] `SANDBOX_MODE=modal`: `_get_executor()` returns a `ModalExecutor` (faked) and the `bash`
  description contains the remote-scratch/no-local-tree paragraph; a test asserts both.
- [ ] End-to-end (hermetic, faked executor): a `bash` call in docker/modal mode routes through the
  selected executor's `run` (a fake records the call) and returns its `ExecResult` rendering — proving
  the seam swap, no real infra.
- [ ] `close_executor()` calls the executor's `aclose()` once and resets the seam; it is a safe no-op in
  `none` mode and when nothing was constructed; wired into the `run_app` exit path and the headless flow
  (asserted by a spy that `aclose` is called on exit).
- [ ] Verify-first: the log records whether `ToolDefinition.description` mutation in `prepare` works on
  the installed pydantic-ai (and which approach shipped).
- [ ] Existing `bash` / registry / factory tests pass unchanged (the `none` path is byte-identical);
  `make ci` green, 0 warnings; `uv lock --check` passes.

## Out of scope

- The Credential Proxy + the headless replay-safety checkpoint config (075).
- Any change to docker/modal executor internals (072/073 own those).

## Log
