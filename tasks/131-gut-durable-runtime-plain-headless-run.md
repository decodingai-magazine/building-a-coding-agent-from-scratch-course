---
id: 131
feature: kitaru-replay-runtime
status: pending
---

# Gut the durable runtime — `decode run` becomes a plain headless runner

Tags: `runtime`, `cli`, `refactor`
Depends on: None
Blocks: 133

This task implements ADR-0019. Kitaru 0.22.2 removed flows/checkpoints/waits entirely
(`from kitaru import ImageSettings, checkpoint, flow, save` in `runtime/flow.py:26` is an
ImportError today), so the durable runtime is DEAD CODE. Delete it — no stubs, no shims — and
replace `decode run` with the smallest headless path: `asyncio.run` around the same
`build_agent()` the REPL uses.

## Scope

**Delete (whole files):**
- `src/decode/runtime/flow.py` (605 lines) and `src/decode/runtime/modal_app.py`.
- `tests/unit/decode/runtime/test_flow.py`, `test_flow_tracing.py`, `test_hitl.py`,
  `test_modal_app.py`, `test_replay_command.py`, `test_store_isolation.py`,
  `test_sandbox_replay_safety.py`, `test_executor_teardown.py` (re-home any executor-teardown
  assertions that still apply to the plain runner into the new runner's test file).
- `tests/support/runtime_fixtures.py` (`isolated_kitaru_store` — ZenML store redirect, obsolete)
  and its registration in `tests/conftest.py`.
- `tests/integration/test_runtime_capstone.py` (built on `@flow` + local-stack replay — the new
  capstone is task 137's worker replay, not an in-process one).

**`src/decode/cli.py`:**
- Delete the `decode replay` command, all `_replay_*` helpers, `_echo_replay_anchor`,
  `_echo_replay_fork`, `_REPLAY_NO_FROM_MESSAGE`, and the `kitaru.errors` import.
- Delete `--hitl` and `_run_hitl` from `decode run`.
- `decode run TASK [--model ID] [--repo URL-OR-PATH] [--local]` keeps its UX contract:
  same `_runtime_config_preflight` guard chain (bucket → provider → `runtime_enabled` →
  sandbox → repo), same context-window notice, agent answer on **stdout only** (pipe-safe),
  diagnostics on stderr. The stderr `exec_id:`/replay-hint lines die with the flow.

**New plain runner** (suggested: `src/decode/runtime/headless.py`, `runtime/__init__.py`
re-exporting it; SWE picks names):
- Builds `build_agent(model=...)`, constructs headless `AgentDeps` (cwd/harness_home/sandbox
  Workspace resolution as before — read `flow.py` before deleting it), runs ONE task to
  completion via the agent's async API, returns the final text.
- **Bypass semantics preserved**: every gated tool runs inline with no prompt (gate in bypass
  mode); `ask_user` is a headless no-op. No wait, no pause — ever.
- **Hand-back preserved**: a completed `decode run --repo` still ships the Workspace as a
  `decode/<session-id>` Session Branch, now from this host-side runner process (the
  "runs inside the flow" rationale is dead). `--hitl` unwiring is moot — the flag is gone.
- **Opik tracing preserved**: the runner calls `observability.init_tracing()` before building
  the agent, exactly like the TUI. `observability/tracing.py` itself is untouched.

**`src/decode/tools/sleep.py`:** revert to plain `asyncio.sleep` — delete the `_SLEEPER` seam,
`install_durable_sleeper`, `reset_sleeper`, `_durable_sleep`; keep the clamp + negative/nan
`ModelRetry` guardrails. Trim `tests/unit/decode/tools/test_sleep.py` accordingly.

**`src/decode/agent/factory.py`:** delete the `flow_mode` parameter and
`_flow_mode_http_client()` — their only rationale was Kitaru 0.18's per-call event loops
(ADR-0010 §3, dead). `build_agent(model=...)` remains for the Model Override. Update
`tests/unit/decode/agent/test_factory.py`.

**`src/decode/config/settings.py` + `.env.example`:** delete `runtime_checkpoint_strategy`
and `runtime_wait_timeout_s` (and their `.env.example` lines). KEEP `runtime_enabled` as the
headless master gate. Update `tests/unit/decode/config/test_settings.py`.

**Rewrite in place:**
- `tests/unit/decode/runtime/test_run_command.py` + `tests/unit/decode/test_cli.py`: guard
  chain, stdout purity, `--model` threading, removal of `replay`/`--hitl` (unknown-command /
  unknown-option errors).
- `tests/integration/test_opik_headless_trace.py`, `test_sandbox_teardown.py`, and the
  KitaruAgent-spy sections of `test_sandbox_capstone.py`: re-target from `decode.runtime.flow`
  onto the plain runner (spans/teardown/hand-back through the new path).

After this task the ONLY kitaru references left in `src/` are `config/settings.py`'s lazy
bucket import (task 132) and comments.

## Acceptance Criteria

- [ ] `src/decode/runtime/flow.py` and `modal_app.py` no longer exist; `grep -rn "from kitaru\|import kitaru" src/` matches only `config/settings.py`.
- [ ] `decode replay …` exits with Click's no-such-command error; `decode run --hitl` exits with no-such-option.
- [ ] `decode run "<task>"` (mocked model) prints ONLY the agent's final text on stdout; guard failures print one friendly stderr line + exit non-zero, in the documented order.
- [ ] A gated tool (e.g. `bash`) executes inline under `decode run` with no approval pause; `ask_user` returns its headless no-op.
- [ ] `decode run --repo <local-path>` on a docker sandbox completes and ships a `decode/<session-id>` branch (integration test through the rewired teardown/capstone tests).
- [ ] `sleep` tool awaits `asyncio.sleep` directly; clamp and `ModelRetry` guardrails still tested.
- [ ] `build_agent` has no `flow_mode` parameter; settings expose no `runtime_checkpoint_strategy`/`runtime_wait_timeout_s`; `.env.example` matches (drift test green).
- [ ] Full unit suite green (integration tests needing the downgraded adapter may not run until 133, but must not reference deleted modules).

## Out of scope

- The recording wrap (tasks 134-135), worker entry (136), dependency swap (133).
- `config/settings.py`'s bucket import (132); `scripts/` and `.sh` files; AGENTS.md/docs (138).
- Any HITL replacement or persistence — deleted, not deferred.

## Log
