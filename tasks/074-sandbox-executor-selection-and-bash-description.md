---
id: 074-sandbox-executor-selection-and-bash-description
feature: sandboxing
status: done
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

- [x] `SANDBOX_MODE=none` (default): `bash` uses `LocalExecutor`, the tool description is **byte-identical**
  to today, and importing `decode.tools.bash` / building the agent imports **no** docker/modal sandbox
  module — a test asserts `decode.sandbox.docker_executor` / `modal_executor` are absent from
  `sys.modules` on the `none` path. — `test_bash_sandbox_selection.py::test_none_mode_agent_imports_no_sandbox_executor_module`, `::test_get_executor_none_keeps_the_eager_local_executor`, `::test_agent_bash_description_docker_is_none_plus_the_paragraph` (docker == none + suffix ⇒ none is byte-identical base).
- [x] `SANDBOX_MODE=docker`: `_get_executor()` returns a `DockerExecutor` (patched/faked — no real
  daemon needed) and the `bash` description contains the persistent-shell/`/workspace` paragraph; a test
  asserts both. — `::test_get_executor_docker_returns_a_real_docker_executor`, `::test_get_executor_docker_selects_via_the_seam_and_memoizes`, `::test_bash_description_docker_appends_the_persistent_shell_paragraph`, `::test_bash_prepare_docker_appends_the_paragraph`.
- [x] `SANDBOX_MODE=modal`: `_get_executor()` returns a `ModalExecutor` (faked) and the `bash`
  description contains the remote-scratch/no-local-tree paragraph; a test asserts both. — `::test_get_executor_modal_returns_a_real_modal_executor`, `test_select.py::test_select_modal_returns_an_inert_modal_executor`, `::test_bash_description_modal_appends_the_remote_scratch_paragraph`.
- [x] End-to-end (hermetic, faked executor): a `bash` call in docker/modal mode routes through the
  selected executor's `run` (a fake records the call) and returns its `ExecResult` rendering — proving
  the seam swap, no real infra. — `::test_bash_routes_through_the_selected_docker_executor`.
- [x] `close_executor()` calls the executor's `aclose()` once and resets the seam; it is a safe no-op in
  `none` mode and when nothing was constructed; wired into the `run_app` exit path and the headless flow
  (asserted by a spy that `aclose` is called on exit). — `::test_close_executor_awaits_aclose_once_and_resets`, `::test_close_executor_is_idempotent`, `::test_close_executor_is_a_safe_noop_in_none_mode`, `test_executor_teardown.py` (bypass + HITL + error path), `test_app_e2e.py::test_run_app_reaps_the_sandbox_executor_on_exit`. **[Tester 2026-07-03: FAIL — headless-flow reap broken cross-loop. FIXED round 2: `DockerExecutor.aclose()` now reaps LOOP-INDEPENDENTLY (loop-free SIGKILL of the shell's process group + `os.waitpid` reap + transport neutralize when the shell's loop is foreign/closed; the clean same-loop await stays for the REPL), and `docker rm -f` always runs. Modal was investigated and is NOT affected (synchronicity proxies `terminate.aio()` onto its own loop — verified via `Sandbox.list`). Now guarded by loop-BOUND regressions (`test_docker_executor.py::test_aclose_reaps_a_loop_bound_shell_from_a_fresh_closed_loop`, `test_executor_teardown.py::test_reap_runtime_executor_reaps_a_loop_bound_executor_cross_loop` — both FAIL on the buggy code, verified) + the real-docker `test_sandbox_teardown.py`. Real e2e: `SANDBOX_MODE=docker decode run` → `/workspace`, container reaped (`docker start`/`docker stop` same id), `docker ps` clean, stderr traceback-free.]**
- [x] Verify-first: the log records whether `ToolDefinition.description` mutation in `prepare` works on
  the installed pydantic-ai (and which approach shipped). — see the SWE log entry below (works; shipped `dataclasses.replace`).
- [x] Existing `bash` / registry / factory tests pass unchanged (the `none` path is byte-identical);
  `make ci` green, 0 warnings; `uv lock --check` passes. — `make ci` = 1191 passed, 0 warnings; `uv lock --check` clean.

## Out of scope

- The Credential Proxy + the headless replay-safety checkpoint config (075).
- Any change to docker/modal executor internals (072/073 own those).

## Log

### [SWE] 2026-07-03 12:20 — Implementation

**Verify-first finding (prepare / ToolDefinition.description)**
Mutating the description inside a per-tool `prepare=` callback **works** on the installed
`pydantic-ai-slim 1.95.1`. Confirmed by spike: `ToolDefinition` is a **non-frozen dataclass**; a
`prepare` that returns a modified definition (in-place `.description=` **or** `dataclasses.replace`)
takes effect on the model-facing schema (captured via `FunctionModel`'s `AgentInfo.function_tools`).
The `tool_def` handed to `prepare` is **rebuilt fresh per run** (distinct object ids across three
runs; no cross-run accumulation), so composing from `tool_def.description` is safe/idempotent.
**Shipped `dataclasses.replace`** (immutable style — never mutates the passed definition); `none`
mode returns the definition **untouched** (same object) so it is provably byte-identical.

**Where `select_executor` landed**
In `src/decode/sandbox/__init__.py` (per-branch lazy imports), and the package `__init__` no longer
eagerly imports the executor modules — a PEP-562 `__getattr__` resolves `DockerExecutor` /
`ModalExecutor` lazily, so `bash.py`'s `_get_executor()` (which imports `select_executor` only on the
docker/modal branch) keeps the `none` path free of every sandbox executor module in `sys.modules`.

**Seam design (why two module globals)**
`bash._EXECUTOR` stays a **live** `LocalExecutor` at all times + a `_executor_selected` memo guard.
This keeps the existing `mocker.patch("decode.tools.bash._EXECUTOR.run", …)` tests passing unchanged
(none mode returns the same eager instance, never re-selecting it) while docker/modal lazily swap in
the sandbox executor once. `reset_executor()` clears the memo (test hermeticity, wired autouse in the
rootdir conftest); `close_executor()` reaps + resets. Headless flows bridge the async
`close_executor` from their **sync** `@flow` body via a **dedicated** `new_event_loop`
(NOT `asyncio.run`, which orphans the loop `run_sync` leaves current → `ResourceWarning` under
`filterwarnings=error`; verified empirically).

**Files modified**
- `src/decode/sandbox/__init__.py` — `select_executor(mode)` (per-branch lazy imports) + lazy
  `__getattr__` re-export; dropped the eager executor imports.
- `src/decode/tools/bash.py` — lazy `_get_executor()` + `_executor_selected` memo, `reset_executor()`,
  async `close_executor()`, `bash_description()` + docker/modal description constants; `bash()` now
  calls `_get_executor().run(...)`; one INFO `[sandbox] mode=<mode>` at selection.
- `src/decode/tools/registry.py` — `_prepare_for(name)` composes the bash mode-specific description
  onto the active-agent restriction (via `dataclasses.replace`; none is a no-op); tightened the
  `prepare` return annotations to `Awaitable[ToolDefinition | None]`.
- `src/decode/tui/app.py` — `await close_executor()` on the `run_app` exit path (next to LSP shutdown
  + memory write-back), non-fatal.
- `src/decode/runtime/flow.py` — `_reap_runtime_executor()` in a `finally` around both flow bodies
  (bypass + HITL) so `decode run` reaps its container/sandbox on completion **and** error.
- `tests/conftest.py` — autouse `_reset_sandbox_executor` (rootdir, order-robust) for hermeticity.

**Files added**
- `tests/unit/decode/sandbox/test_select.py` — `select_executor` mapping + inert construction.
- `tests/unit/decode/tools/test_bash_sandbox_selection.py` — getter/memo, `reset`/`close`,
  `bash_description`, registry `prepare` per mode, model-surface description, e2e seam swap,
  subprocess `sys.modules` isolation (none imports no executor module; docker-mode imports no
  kitaru/modal).
- `tests/unit/decode/runtime/test_executor_teardown.py` — bypass + HITL reap (spy), plus the error path.
- `tests/unit/decode/tui/test_app_e2e.py` — added the REPL-exit reap test (spy through `run_app`).

**Tests**
- Unit: 1156 → passing (whole unit suite); the new files add 21 (bash selection) + 4 (select) +
  3 (flow teardown) + 1 (REPL exit) tests.
- Integration: unaffected — `make ci` ran the real docker (9) + modal (5) suites green (Docker up).
- `make ci` = **1191 passed, 0 warnings** (`filterwarnings=["error"]`); `uv lock --check` clean.

**Evidence**
```
$ make ci
... 1191 passed in 146.26s (0:02:26)

$ docker info >/dev/null 2>&1 && echo DOCKER: up
DOCKER: up

# Manual e2e smoke — real DockerExecutor through the task-074 seam (scratch script):
1) selected executor: DockerExecutor
2) description tail: "... stdout and stderr are merged into a single stream in the command's own order."
3) bash pwd/echo -> Exit code: 0.  stdout: /workspace  hello-from-container
4) persistent cwd -> Exit code: 0.  stdout: /workspace/sub      # cd persists across bash calls
5) container id: c54ab135d256...
6) after close_executor, memo selected = False; docker ps -a for that id -> ''   # container reaped
SMOKE OK
```

**Notes**
- Manual smoke drove `bash` through the **real** `DockerExecutor` via the selection seam (Docker was
  up): selection → real container (`pwd`=/workspace bind mount, persistent `cd`) → `close_executor`
  reaped the container + reset the memo. The `decode run` headless smoke needs a real `GEMINI_API_KEY`;
  the seam-level smoke above proves the same wiring without a model call.
- HITL flow reaps on completion/error via the same `finally`; on a durable **pause** the reap may run
  too (best-effort, idempotent, `--rm`/modal-`timeout` are crash backstops) — acceptable per ADR-0011
  §2 ("torn down on decode exit"). Not exercised with a real container in CI (hermetic fakes only).
- Did NOT touch the permission gate, Credential Proxy, or checkpoint configs (075's scope).

### [Tester] 2026-07-03 03:15 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 156 files clean; `ruff check` all pass)
- Unit tests: 1158 passed / 0 failed
- Integration tests: 35 passed / 0 failed (incl. real-docker 9, real-modal 5 — Docker up, modal creds present)
- Warnings: 0 (`filterwarnings=["error"]`); `uv lock --check` clean
- Test-order robustness: pytest-randomly is NOT installed; ran 3 adversarial explicit orderings
  (selection↔plain-bash↔exec↔teardown, forward/reversed/interleaved) — 53/53/20 passed, autouse
  `_reset_sandbox_executor` prevents memo/mode leakage.

**E2E adversarial pass** (real Gemini, `LLM_PROVIDER=gemini`)
- Happy path (none): `SANDBOX_MODE=none decode run "run the shell command pwd ..."` → exit 0, answer =
  the host repo cwd, `[sandbox] mode=none` logged. PASS.
- Happy path (docker): `SANDBOX_MODE=docker decode run "..."` → exit 0, answer = `/workspace`,
  `[sandbox] mode=docker` + `docker start <id>` logged. Selection + bind-mount + description all correct.
- **Break path 1 (teardown / real container reap — FAIL):** the same docker run leaves the container
  **running** (`docker ps` after = 1, no `docker stop`/`docker rm` log line) and prints
  `RuntimeError: Event loop is closed` to stderr. Reproduced twice (containers `44839a3e…`, `b594ecb1…`
  leaked; `--rm` is no backstop — PID 1 is `sleep infinity`, never exits). Root cause isolated with a
  same-loop-vs-cross-loop harness: SAME-LOOP aclose (REPL path) → `REAPED=YES`; CROSS-LOOP aclose (the
  headless `_reap_runtime_executor()` fresh loop) → `close_executor RAISED: RuntimeError: … got Future
  attached to a different loop`, `REAPED=NO — LEAKED`. `DockerExecutor`'s shell subprocess + pipe
  transports are bound to `run_sync`'s (now-closed) loop; closing them from a new loop fails.
- **Break path 2 (byte-identity of `none` description — PASS):** captured the model-facing `bash`
  description via a real agent + `FunctionModel` under `none` mode on THIS tree vs a git worktree at
  baseline `57dbbd9`. Identical SHA `ae63a1f3…`, 879 chars, `diff` empty. `none` is provably
  byte-identical.
- **Break path 3 (docker daemon down mid-session — FAIL, secondary):** with a dead `DOCKER_HOST`, a
  `bash()` call through the docker seam lets an **unhandled `RuntimeError`** escape the tool
  (`docker run failed (exit 1): Cannot connect to the Docker daemon…`) — not a `ModelRetry`, not a
  rendered `ExecResult`. The task-071 startup guard is the primary mitigation (fires if the daemon is
  down at startup); this is the residual mid-session path, which 074 is the first task to make reachable.
- **Break path 4 (modal-mode selection without creds — PASS):** `_get_executor()` in modal mode
  constructs a `ModalExecutor` inertly — no `modal` SDK import, no network, `_sandbox is None`, no kitaru.
- **Break path 5 (import laziness — PASS):** none-path build+select imports neither
  `decode.sandbox.docker_executor` nor `modal_executor`; docker-mode REPL build+select imports no
  `kitaru`/`modal` (subprocess-isolated tests + direct probe).

**Acceptance criteria**
- [x] PASS — AC1 `none`: LocalExecutor + byte-identical description + no sandbox import — baseline↔current
      SHA identical (`ae63a1f3…`); `test_none_mode_agent_imports_no_sandbox_executor_module` passes.
- [x] PASS — AC2 `docker`: `_get_executor()`→DockerExecutor + persistent-shell/`/workspace` paragraph —
      unit tests + real e2e (`pwd`=`/workspace`, `mode=docker`).
- [x] PASS — AC3 `modal`: `_get_executor()`→ModalExecutor + remote-scratch paragraph — unit tests +
      inert-construction probe.
- [x] PASS — AC4 hermetic seam swap: `test_bash_routes_through_the_selected_docker_executor`.
- [ ] FAIL — AC5 `close_executor()` wired into run_app exit AND the headless flow so a `decode run`
      reaps its container/sandbox.
      Expected: after `SANDBOX_MODE=docker decode run …`, the session container is removed and no
      traceback reaches stderr (ADR-0011 §4; CLAUDE.md `decode run` contract).
      Actual: container leaks (still `Up`), no `docker rm` runs, `RuntimeError: Event loop is closed`
      prints to stderr; `_reap_runtime_executor()` in `runtime/flow.py:210` raises `got Future attached
      to a different loop` (logged+swallowed, so exit is still 0 — masking the leak). REPL half is fine.
      Fix: reap without the cross-loop bridge. Simplest: make `DockerExecutor.aclose()` /
      `ModalExecutor.aclose()` reap loop-independently — `docker rm -f <id>` (and modal terminate) do not
      need `run_sync`'s loop; only pipe-transport teardown does, so shell out the container removal
      synchronously and swallow the transport-close error. Then add a regression test that exercises a
      *loop-bound* executor across the headless bridge (the current spy uses a loop-agnostic `AsyncMock`,
      so it can't catch this) — ideally a `skipif(docker)` integration test mirroring `test_docker_executor.py`.
- [x] PASS — AC6 verify-first: prepare/description mutation works — SWE log + independent capture confirm.
- [x] PASS — AC7 existing suites unchanged + `make` gates green + lock clean — see Test summary.

**Evidence**
```
$ SANDBOX_MODE=docker DECODE_LOG_FILE=… uv run decode run "run the shell command pwd ..."   # (2nd run)
… answer: The exact output of the `pwd` command is: `/workspace`   (exit 0)
$ docker ps --format '{{.ID}} {{.Image}} {{.Status}}'
b594ecb1daa3 python:3.12-slim Up 6 seconds          # container NOT reaped
$ grep -c "Event loop is closed" docker_stderr.txt  → 2
$ grep -c "docker stop" run_docker2.log             → 0    # never reaped

# same-loop vs cross-loop isolation
[SAME-LOOP  (REPL-like)]      pwd='/workspace' container_after=0  REAPED=YES
[CROSS-LOOP (headless bridge)] close_executor RAISED: RuntimeError: … got Future attached to a
                               different loop   →  REAPED=NO — LEAKED

# byte-identity of the none-mode description (baseline 57dbbd9 vs task-074)
ae63a1f3da719d649506f240f40362e33448f427  desc_baseline.txt
ae63a1f3da719d649506f240f40362e33448f427  desc_current.txt   (diff empty)
```

**Other issues found**
- Test-quality gap (root of the missed defect): `test_executor_teardown.py` + the app-exit reap test
  inject `SimpleNamespace(aclose=AsyncMock())`, a loop-agnostic fake, so they pass while the real
  headless teardown is broken. Any regression test must use a loop-bound executor (or a real container).
- ModalExecutor is very likely affected by the same cross-loop root cause (`sandbox.terminate.aio()` on
  a modal client bound to `run_sync`'s loop) — not run remotely (creds/cost), flagged for the fix.
- Daemon-down-mid-session (break path 3): an unhandled `RuntimeError` escapes `bash()` rather than a
  model-facing error. Consider catching executor-infra failures at the seam and returning a `ModelRetry`
  / rendered error. Softer than the leak (startup guard mitigates the common case); orchestrator to weigh.
- `code-review` plugin is enabled but not invokable as a slash command from the Tester subagent context;
  substituted a full manual diff review + the adversarial e2e above (which caught the defect).

**VERDICT: FAIL**

### [SWE] 2026-07-03 07:45 — Fixes (round 2, addressing the Tester FAIL)

**What changed since round 1 (the two Tester findings)**

1. **HEADLINE (AC5) — headless container/sandbox leak + `Event loop is closed` on the reap. FIXED.**
   Root cause confirmed exactly as the Tester diagnosed: the headless flow reaps on a *fresh* loop
   (`_reap_runtime_executor`) while `DockerExecutor`'s shell subprocess + pipe transports are bound to
   the kitaru per-call loop that has since **closed** — awaiting them there raised `RuntimeError: Event
   loop is closed` / `Future attached to a different loop`, which `_reap_runtime_executor` logged +
   swallowed while the container **leaked** (`--rm` is no backstop for `sleep infinity`).
   - `src/decode/sandbox/docker_executor.py` — `aclose()` now branches on the shell's recorded loop
     (`_shell_loop`, captured in `_ensure_shell`): **same live loop** (interactive REPL exit) → the
     clean await teardown as before (`_teardown_shell_clean`); **foreign/closed loop** (headless reap)
     → `_kill_shell_loop_free()` — `os.killpg(SIGKILL)` + reap the zombie via the underlying
     `Popen.wait` / `os.waitpid` + `_neutralize_shell_transports()` (close the pipe fds and defuse the
     orphaned transports' `__del__` so no `ResourceWarning` prints as a traceback), **never touching the
     dead loop**. `docker rm -f <id>` (a fresh subprocess needing no old loop) **always** runs — the
     load-bearing reap. REPL path unchanged.
   - **Modal investigated, NOT affected — no code change.** The Tester flagged `ModalExecutor.aclose`
     as "very likely affected". I verified against real modal (creds present): my first `poll()`-based
     measurement was unreliable (the *same-loop* control also read "still running"), so I re-measured
     with `Sandbox.list()` — the reliable signal. Result: `terminate.aio()` through the stale cached
     handle reaps the sandbox cross-loop just as well as same-loop (modal's `synchronicity` proxies
     every `.aio()` onto its own persistent background-thread loop, insulating it from the caller's loop
     lifecycle — unlike docker's raw asyncio subprocess). A speculative `from_id` re-resolution fix I'd
     started was **reverted**; `modal_executor.py` carries only a doc note recording the finding.

2. **SECONDARY — daemon dies mid-session → unhandled `RuntimeError` escapes `bash()`. FIXED.**
   - `src/decode/sandbox/docker_executor.py` — `run()` wraps `_ensure_container` / `_ensure_shell` in
     `except (RuntimeError, OSError)` (the KNOWN infra-failure shapes this code raises — verified: its
     own `RuntimeError` wrapper on a non-zero `docker run`, `OSError`/`FileNotFoundError` on spawn),
     logs, discards the stale session (`_discard_session`, loop-free), and returns a rendered
     `ExecResult(exit_code=125, note="Docker daemon became unreachable — the sandbox session was
     lost.")` so the model reacts. Scoped catch — a genuine bug (e.g. `ValueError`) still surfaces.

**Regression tests (all FAIL on the buggy code — verified by temporarily reverting the fix)**
- `tests/unit/decode/sandbox/test_docker_executor.py`:
  - `test_aclose_reaps_a_loop_bound_shell_from_a_fresh_closed_loop` — THE loop-bound guard the round-1
    `AsyncMock` spy missed: a real `sleep` child created on loop1, loop1 **closed**, reaped via `aclose`
    on a fresh loop2 → must not raise AND the child is actually killed. (Also `…_cleanly_reaps_a_same_loop_shell` for the REPL branch.)
  - `test_run_returns_a_rendered_failure_when_the_container_cannot_start`, `…_survives_a_missing_docker_binary`,
    `…_lets_an_unexpected_error_surface` — the daemon-death `ExecResult` + the scoped-catch guard.
- `tests/unit/decode/tools/test_bash_sandbox_selection.py::test_bash_renders_a_daemon_loss_without_raising`
  — the tool boundary: `bash()` renders the 125/daemon-lost failure, no exception escapes.
- `tests/unit/decode/runtime/test_executor_teardown.py::test_reap_runtime_executor_reaps_a_loop_bound_executor_cross_loop`
  — drives the REAL `_reap_runtime_executor` against a loop-bound `DockerExecutor`; asserts the reap logs
  no "headless sandbox teardown failed" AND the child dies (closes the exact gap the Tester named).
- `tests/integration/test_sandbox_teardown.py` — real-docker (`skipif` no daemon): the REAL bypass
  `@flow` runs one bash call in a real container, then asserts the container is reaped + no teardown
  warning. (Added a fixture-teardown `_close_lingering_event_loops` so the flow's abandoned per-call loop
  — harmless in a real `decode run`, which exits immediately — cannot trip a later test's
  `filterwarnings=error` GC.)

**Evidence**
```
$ make ci
1201 passed in 151.22s   (1165 unit + 36 integration; uv lock --check + format-check + lint-check clean)

# real e2e — the AC5 proof against real docker + real Gemini
$ SANDBOX_MODE=docker decode run "Use the bash tool to run pwd. Report only its output."
  stdout:  The current working directory is `/workspace`.        (exit 0)
  log:     [sandbox] mode=docker
           [sandbox] docker start 070b4f5b… image=python:3.12-slim
           [sandbox] docker stop  070b4f5b…                       # same id → container reaped
  $ docker ps -a --filter ancestor=python:3.12-slim   → (empty)  # no leak
  stderr:  only the durable exec_id + replay hint (ADR-0010 §2) — no traceback, no "Event loop is closed"

# regressions genuinely catch the bug (temporarily reverted aclose to always-same-loop):
  test_aclose_reaps_a_loop_bound_shell_from_a_fresh_closed_loop            FAILED
  test_reap_runtime_executor_reaps_a_loop_bound_executor_cross_loop        FAILED  (+ "subprocess N is still running")
# fix restored → both pass; stressed the loop-bound tests 12× → no flakiness.

# modal reap verified reliable (real modal, Sandbox.list):
  stale-handle terminate.aio() cross-loop → sandbox drops off live list (reaped)  ✓  (no fix needed)
```

**Notes for the Tester**
- No commit (awaiting your re-review). Files changed round 2: `docker_executor.py` (the fix),
  `modal_executor.py` (doc-only — behavior reverted to the original one-liner), and the five test files
  above. `flow.py` / `bash.py` / `registry.py` / `__init__.py` / `app.py` are unchanged since round 1.
- Leaked QA containers (`44839a3e…`, `b594ecb1…`) are gone; `docker ps` + modal `Sandbox.list` both clean.
- Scope held: no architectural fork, no permission/config edits. The daemon-death catch is deliberately
  narrow (RuntimeError/OSError only) so real bugs still crash loudly.

### [Tester] 2026-07-03 08:10 — QA (round 2 — re-verify the two round-1 FAILs)

**Test summary**
- Format / lint: PASS (157 files formatted; ruff clean)
- Unit tests: 1165 passed / 0 failed (round 1: 1158 — +7 loop-bound regressions)
- Integration tests: 36 passed / 0 failed (round 1: 35 — +`test_sandbox_teardown.py` real-docker reap)
- Warnings: 0 (`filterwarnings=["error"]`); `uv lock --check` clean. Total 1201.

**E2E adversarial pass** (real Gemini)
- Original failing e2e re-run TWICE (the leak reproduced twice in round 1): `SANDBOX_MODE=docker decode
  run "…pwd…"` → exit 0, answer `/workspace` both times; `docker start`+`docker stop` log the SAME id;
  `docker ps` clean (0 `python:3.12-slim` after); stderr = only the `exec_id`/replay hint, **0**
  traceback/loop-error hits. The round-1 headline leak + `Event loop is closed` traceback are GONE.
- My round-1 loop harness re-run: SAME-LOOP `REAPED=YES` (REPL unchanged); CROSS-LOOP now `REAPED=YES`
  (was `NO — LEAKED` + `RuntimeError: got Future attached to a different loop`).
- Daemon-down mid-session (round-1 secondary FAIL) re-run with a dead `DOCKER_HOST`: `bash()` now returns
  a rendered `Exit code: 125.` + the daemon-lost note + the failure on stderr — no unhandled exception.
- (b) Zombie sweep after cross-loop reaps: no decode `<defunct>` host process (the one zombie found is a
  child of Zed.app, unrelated); no lingering decode/uv/python processes.
- (c) HITL teardown: shares the identical `_reap_runtime_executor` `finally` (flow.py unchanged since
  round 1), directly proven by `test_reap_runtime_executor_reaps_a_loop_bound_executor_cross_loop`.
- (d) The loop-free/cross-loop/daemon-death regressions pass under explicit `-W error` (10/10). NOTE: a
  real `decode run` under `PYTHONDEVMODE=1` segfaults during GC — but `SANDBOX_MODE=none` segfaults
  IDENTICALLY (same crash, no docker executor involved), so it is a **pre-existing dev-mode C-extension
  (grpc) crash, NOT a task-074 defect**; normal runs exit 0 cleanly.
- (e) Modal cross-loop terminate (one real sandbox): `aclose()` returned clean on a fresh loop and
  `Sandbox.list()` showed the sandbox gone (`REAPED=YES`) — the SWE's "not affected" claim holds
  (synchronicity proxies `.aio()` onto modal's own loop). No sandbox left behind.
- Cost hygiene: 0 leaked `python:3.12-slim` containers, 0 live modal sandboxes, tree clean.

**Fix review** — the reshape is sound: `aclose()` branches on `shell_loop is _running_loop()` — same live
loop → clean await (`_teardown_shell_clean`, REPL); foreign/closed loop → `_kill_shell_loop_free`
(`os.killpg` SIGKILL + `Popen.wait`/`os.waitpid` reap + best-effort transport neutralize), never touching
the dead loop; `docker rm -f <id>` (a fresh subprocess) always runs and is the load-bearing reap. The
transport-neutralize reaches into CPython internals (`_transport._proc`, `_pipe`, `_closing`, `_closed`) —
fully suppressed, cleanliness-only, degrades to a harmless GC warning if an internal is renamed; the
container reap does not depend on it. The round-1 test-quality gap is genuinely closed (real `sleep` child
loop-bound tests, red-on-buggy verified by the SWE).

**Acceptance criteria** — all 7 verified:
- [x] AC1–AC4, AC6, AC7 — re-confirmed green (unchanged since round 1; suite 1201 green, lock clean).
- [x] AC5 — teardown wired into BOTH the REPL exit and the headless flow; a real `decode run` reaps its
      container (verified twice) and modal cross-loop terminate reaps too. The round-1 FAIL is resolved.

**Other issues found**
- None blocking. Minor/advisory: the internal-attribute neutralization in `_kill_shell_loop_free` is
  CPython-version-coupled (suppressed + non-load-bearing) — a candidate PR-reviewer note, not a defect.
- Pre-existing (out of 074 scope): the kitaru/grpc stack segfaults under `PYTHONDEVMODE=1` at GC in ALL
  modes incl. `none`; worth a separate follow-up but does not affect normal `decode run`.

**VERDICT: PASS**
