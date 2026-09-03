---
id: 131
feature: kitaru-replay-runtime
status: done
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

- [x] `src/decode/runtime/flow.py` and `modal_app.py` no longer exist; `grep -rn "from kitaru\|import kitaru" src/` matches only `config/settings.py`.
- [x] `decode replay …` exits with Click's no-such-command error; `decode run --hitl` exits with no-such-option.
- [x] `decode run "<task>"` (mocked model) prints ONLY the agent's final text on stdout; guard failures print one friendly stderr line + exit non-zero, in the documented order.
- [x] A gated tool (e.g. `bash`) executes inline under `decode run` with no approval pause; `ask_user` returns its headless no-op.
- [ ] `decode run --repo <local-path>` on a docker sandbox completes and ships a `decode/<session-id>` branch (integration test through the rewired teardown/capstone tests).
- [x] `sleep` tool awaits `asyncio.sleep` directly; clamp and `ModelRetry` guardrails still tested.
- [x] `build_agent` has no `flow_mode` parameter; settings expose no `runtime_checkpoint_strategy`/`runtime_wait_timeout_s`; `.env.example` matches (drift test green).
- [ ] Full unit suite green (integration tests needing the downgraded adapter may not run until 133, but must not reference deleted modules).

## Out of scope

- The recording wrap (tasks 134-135), worker entry (136), dependency swap (133).
- `config/settings.py`'s bucket import (132); `scripts/` and `.sh` files; AGENTS.md/docs (138).
- Any HITL replacement or persistence — deleted, not deferred.

## Log

### [SWE] 2026-08-22 10:20 — Implementation

**Files modified**
- `src/decode/runtime/headless.py` — NEW: the plain headless runner (`run_headless_task`) — one `asyncio.run` around `build_agent()`, bypass gate, Workspace prep + warm-up, executor reap on a dedicated loop, host-side Hand-back, `init_tracing()` + one root span.
- `src/decode/runtime/__init__.py` — re-exports `run_headless_task` / `RUN_SPAN_NAME`; flow/HITL/replay exports gone.
- `src/decode/runtime/flow.py`, `src/decode/runtime/modal_app.py` — DELETED (605 + 55 lines).
- `src/decode/cli.py` — `decode replay` + every `_replay_*` helper + `_REPLAY_NO_FROM_MESSAGE` + the `kitaru.errors` import deleted; `--hitl` / `_run_hitl` / `_echo_replay_anchor` deleted; `run` now calls `run_headless_task` and echoes only the answer.
- `src/decode/tools/sleep.py` — reverted to plain `await asyncio.sleep`; `_SLEEPER`/`install_durable_sleeper`/`reset_sleeper`/`_durable_sleep` gone, clamp + `ModelRetry` guardrails kept.
- `src/decode/agent/factory.py` — `flow_mode` + `_flow_mode_http_client()` deleted from `build_agent`/`_build_model` (httpx import with them).
- `src/decode/tools/approval.py`, `src/decode/agent/deps.py` — the `headless_durable_waits` HITL wait posture deleted (dead once the HITL flow was: nothing set it; ADR-0019 §1 "all wait plumbing ... no stubs, no shims"). Flagged for the reviewer as the one deletion beyond the task's enumerated list.
- `src/decode/config/settings.py` + `.env.example` — `runtime_checkpoint_strategy` / `runtime_wait_timeout_s` deleted (clean-break note in both); `runtime_enabled` kept as the master gate.
- `src/decode/agent/context_window.py` — docstring reference to the deleted `_flow_mode_http_client` rewritten.
- `tests/unit/decode/runtime/test_headless.py` — NEW: 25 tests on the runner (answer, model override, bypass inline `bash`/`write`, `ask_user` no-op, deps shape, tracing order + run-id sharing, reap on success/error/fresh-loop/failure, Workspace prep + fatal clone + warm-up degrade, hand-back ordering/skips/failure).
- `tests/unit/decode/runtime/test_run_command.py` — rewritten on the plain runner: stdout purity, guard chain, `--model`/`--repo`/`--local` threading, `replay`/`--hitl` removal.
- `tests/unit/decode/test_cli.py` — `run` is the only subcommand; kitaru-free import docstring retargeted.
- `tests/unit/decode/tools/test_sleep.py`, `tests/unit/decode/tools/test_approval.py`, `tests/unit/decode/agent/test_factory.py`, `tests/unit/decode/config/test_settings.py`, `tests/unit/decode/test_kitaru_dependency.py` — durable-seam sections replaced with clean-break pins (retired names must not come back).
- `tests/integration/test_opik_headless_trace.py` — retargeted onto the plain runner (one `decode_run` root, nested model/tool spans, per-run thread id, inactive = zero spans).
- `tests/integration/test_sandbox_teardown.py` — retargeted onto `run_headless_task` (real docker, skips without a daemon).
- `tests/integration/test_sandbox_capstone.py`, `test_subagents_capstone.py`, `test_observability_capstone.py` — KitaruAgent-spy / cache-disable / `flow_mode` sections removed.
- DELETED tests: `tests/unit/decode/runtime/{test_flow,test_flow_tracing,test_hitl,test_modal_app,test_replay_command,test_store_isolation,test_sandbox_replay_safety,test_executor_teardown}.py`, `tests/support/runtime_fixtures.py` (+ its `tests/conftest.py` registration), `tests/integration/test_runtime_capstone.py`.

**Tests**
- Unit: the files this task owns are fully green — 172 passing across `tests/unit/decode/runtime`, `config`, `test_sleep.py`, `test_approval.py`, `test_kitaru_dependency.py` + the retargeted Opik headless integration file.
- Full unit suite: 101 failed / 1983 passed / 78 errors — **every one pre-existing on this branch** (`pydantic-ai 2.33` is installed; `build_agent()` itself raises `TypeError: Agent.__init__() got an unexpected keyword argument 'output_retries'`). Baseline before this task: 108 failed / 1946 passed / 87 errors. Diffed both failure sets: this task REMOVES 18 (9 collection errors + 3 flow_mode + 5 durable-sleeper + the kitaru durability-surface smoke) and ADDS none — the only two "new" names are the renamed provider-key factory tests that fail on the same pre-existing `output_retries` TypeError.
- Integration: 46 failures/errors vs 51 at baseline, no new ones; the 3 collection errors on deleted modules are gone. Docker-gated tests SKIP (no daemon in this environment).

**Acceptance criteria**
- [x] `flow.py` / `modal_app.py` gone — `grep -rn "from kitaru\|import kitaru" src/` → only `config/settings.py:171`.
- [x] `decode replay …` → `Error: No such command 'replay'.`; `decode run --hitl` → `Error: No such option: --hitl` (both verified in a real process).
- [x] stdout purity — `tests/unit/decode/runtime/test_run_command.py::test_run_stdout_is_exactly_the_answer_and_stderr_is_silent` + the real-process e2e below; guard order verified by the bucket→provider→runtime→sandbox→repo tests in that file.
- [x] gated tool inline + `ask_user` no-op — `test_headless.py::test_a_gated_bash_call_runs_inline_with_no_approval_pause`, `::test_a_gated_write_touches_the_file_inline`, `::test_ask_user_feeds_the_no_interactive_user_message_back_to_the_model`.
- [ ] `decode run --repo <local-path>` on a **docker** sandbox — **NOT RUN: no docker daemon in this environment.** The path is rewired and covered: `tests/integration/test_sandbox_teardown.py` (real docker, skipped here) plus unit proof of the ordering/arguments in `test_headless.py::test_a_completed_run_hands_the_workspace_back_after_the_reap` (reap → ship, harness home = launch cwd, `session_id` == the trace `thread_id`).
- [x] `sleep` awaits `asyncio.sleep` directly; clamp + `ModelRetry` guardrails still tested (`test_sleep.py`, 8 passing incl. the clean-break pin).
- [x] no `flow_mode`; no `runtime_checkpoint_strategy` / `runtime_wait_timeout_s`; `.env.example` drift test green (`tests/unit/decode/config` 104 passing).
- [ ] Full unit suite green — **BLOCKED by task 133**, not by this task: the branch already installs `pydantic-ai 2.33`, whose `Agent.__init__` rejects decode's `output_retries` / `tool_retries`, so every test that builds a real agent fails at HEAD too. Delta-checked instead (see Tests): −18 failures, +0.

**Evidence**
```
$ uv run pytest tests/unit/decode/runtime tests/unit/decode/config tests/unit/decode/tools/test_sleep.py \
      tests/unit/decode/tools/test_approval.py tests/unit/decode/test_kitaru_dependency.py \
      tests/integration/test_opik_headless_trace.py -q -p no:randomly
172 passed in 2.14s

$ make format-check && make lint-check
300 files already formatted
All checks passed!

$ uv run pytest tests/unit -q -p no:randomly --continue-on-collection-errors   # HEAD baseline vs now
baseline: 108 failed, 1946 passed, 87 errors
now:      101 failed, 1983 passed, 78 errors      # diff of the FAILED/ERROR sets: -18, +0 new

# e2e — the REAL `decode run` CLI in a real process, only the model boundary swapped
$ cd $(mktemp -d) && uv run python e2e_run.py     # sys.argv = ["decode", "run", "print a greeting"]
exit=0
--- STDOUT (piped, what a consumer sees):
bash said: Exit code: 0.

stdout:
hello-from-decode-run
--- STDERR:
(empty)

$ uv run decode replay exec-1 --from step
Error: No such command 'replay'.

$ uv run decode run --hitl "do it"
Error: No such option: --hitl Did you mean --help?

$ RUNTIME_ENABLED=false uv run decode run "do it"          # exit 1
Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true in your environment or .env to use `decode run` (see .env.example).

$ LLM_PROVIDER=gemini GEMINI_API_KEY= uv run decode run "do it"    # exit 1
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).

$ uv run decode run --repo /tmp/x "do it"                  # exit 1 (SANDBOX_MODE=none)
Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace, which only exists in a sandbox mode — ...
```

**Notes**
- **Deviation from the enumerated scope (deliberate):** also deleted `AgentDeps.headless_durable_waits` + its branch in `tools/approval.py`. It was HITL wait plumbing with no producer left, and ADR-0019 §1 says the wait plumbing dies with no shims. Says so in the diff and pinned by `test_approval.py::test_the_headless_durable_waits_posture_is_gone`.
- **Design note:** the reap deliberately stays on a dedicated short-lived loop (`_reap_executor`), run after `asyncio.run` returns and before the Hand-back — same ordering the flow had, and it keeps the cross-loop teardown regression test meaningful.
- The run's **session id is a fresh `uuid4()` per run**, used for BOTH the Opik `thread_id` and the `decode/<session-id>` Hand-back branch (the old id was the Kitaru `exec_id`, which no longer exists). Pinned by `test_headless.py::test_the_run_opens_one_root_span_whose_thread_id_is_the_hand_back_session_id`.
- Trace-span naming: `_tool_spans` in the Opik integration test accepts BOTH `running tool` (pydantic-ai ≤2.22, the pin task 133 lands) and `execute_tool <name>` (2.23+, installed today) so the proof survives the downgrade.
- Left alone, per the task's Out of scope: `scripts/deploy.sh` + `scripts/demo-multiple-attempts.sh` comments naming `runtime/modal_app.py` / `run_agent_task`, `docker/flow.Dockerfile`, and all `docs/` (ADR-0008/0010 text, AGENTS.md) — task 138.
- **NOT COMMITTED** — handing off to the Tester.

### [Tester] 2026-08-22 01:15 — QA

**Test summary**
- Format / lint: PASS (`ruff format --check` — 300 files already formatted; `ruff check` — all checks passed)
- Unit tests: 101 failed / 1983 passed / 78 errors (independently reproduced, matches SWE's reported numbers exactly)
- Integration tests: 35 failed / 51 passed / 16 skipped / 11 errors = 46 failed+errored (independently reproduced, matches SWE's reported numbers exactly)
- Warnings: 0 (`filterwarnings=["error"]` — any warning would already show as a failure; none did)
- Baseline re-measured independently by stashing this diff and re-running both suites: unit 108F/1946P/87E, integration 37F/48P/16skip/14E=51. Confirms the SWE's stated baseline.
- Failure-set diff (not just counts): built sorted `FAILED`/`ERROR` name lists for baseline vs. now, both unit and integration, via `comm`. Unit: 2 "new" names (`test_the_provider_key_comes_from_settings_for_gemini`/`_openrouter` in `test_factory.py`) — traced via `git diff` to a pure rename of `test_flow_mode_reads_the_gemini_key_from_settings`/`_openrouter_key_from_settings` (both present in the baseline failure list under their old names), failing on the same pre-existing `TypeError: Agent.__init__() got an unexpected keyword argument 'output_retries'` (pydantic-ai 2.33, task 133's problem). Zero genuinely new unit failures. Integration: zero new names at all (`comm -13` empty).
- `code-review` plugin is enabled in `.claude/settings.json` but no invocation tool (SlashCommand/Task) was available in this Tester session — folded findings into the manual checklist below instead; this is a session tooling limitation, not a gap in the review depth.

**E2E adversarial pass**
- Happy path: real `decode.cli.cli()` invocation (`decode run "print a greeting"`) with only `_build_headless_agent` swapped for a scripted `FunctionModel` agent that calls `bash` then returns text → stdout = exactly `hello-from-decode-run`, stderr empty, exit 0 (PASS)
- Break path 1 (removed surfaces): `decode replay exec-1 --from step` → `Error: No such command 'replay'.` exit 2; `decode run --hitl "do it"` → `Error: No such option: --hitl` exit 2 (PASS)
- Break path 2 (guard-chain ordering under compounding failures): `LLM_PROVIDER=gemini GEMINI_API_KEY= RUNTIME_ENABLED=false SANDBOX_MODE=docker decode run "do it"` → provider guard fires first (`set GEMINI_API_KEY…`); fixing the key but leaving `RUNTIME_ENABLED=false` → runtime guard fires next; fixing that with `SANDBOX_MODE=docker` and no daemon → sandbox guard fires last. Matches the documented order (bucket → provider → runtime_enabled → sandbox → repo) exactly (PASS)
- Break path 3 (crash mid-run, stdout purity under failure): agent's `run()` raises `RuntimeError("simulated model network failure")` mid-turn → stdout stays completely empty (still pipe-safe on a crash), traceback on stderr only, exit 1 — no partial/garbled output leaked to stdout (PASS)
- Break path 4 (sleep guardrails, hostile numeric input): `await sleep(None, -1)` → `ModelRetry("seconds must be a non-negative number")`; `await sleep(None, float('nan'))` → same `ModelRetry` (not a hang); `await sleep(None, settings.sleep_max_s + 1000)` → clamps to `settings.sleep_max_s` (60.0) and actually sleeps that long, confirmed by wall-clock (PASS)
- Break path 5 (empty-string task, boundary input): `decode run ""` with model boundary mocked → the empty string reaches the agent unmolested and the run completes normally, no special-casing needed or expected (PASS)

**Acceptance criteria**
- [x] PASS — `flow.py`/`modal_app.py` gone; `src/` kitaru imports only in `config/settings.py` — `grep -rn "from kitaru\|import kitaru" src/` → single hit, `config/settings.py:171`; `ls src/decode/runtime/` shows only `__init__.py` + `headless.py`.
- [x] PASS — `decode replay …` / `decode run --hitl` removed — verified in a real subprocess: `Error: No such command 'replay'.` / `Error: No such option: --hitl`.
- [x] PASS — stdout purity + guard order — `tests/unit/decode/runtime/test_run_command.py::test_run_stdout_is_exactly_the_answer_and_stderr_is_silent` + independently reproduced e2e (see happy path above) + independently reproduced compounding-guard-failure e2e (break path 2 above).
- [x] PASS — gated tool inline, `ask_user` no-op — `test_headless.py::test_a_gated_bash_call_runs_inline_with_no_approval_pause` + `::test_a_gated_write_touches_the_file_inline` + `::test_ask_user_feeds_the_no_interactive_user_message_back_to_the_model`, all reproduced green.
- [ ] Awaiting environment — `decode run --repo <local-path>` on a docker sandbox — Docker daemon unreachable in this session (`docker info` fails). Per orchestrator guidance, verified at unit level instead: `test_headless.py::test_a_completed_run_hands_the_workspace_back_after_the_reap` proves reap-then-ship ordering, harness home = launch cwd, and `ship_workspace(session_id=...)` receiving the same id as the trace `thread_id` — all reproduced green. Environment-limited, not a code defect.
- [x] PASS — `sleep` awaits `asyncio.sleep` directly, clamp + `ModelRetry` guardrails — `tests/unit/decode/tools/test_sleep.py` (8/8 passing) + manual negative/nan/clamp probes above (break path 4).
- [x] PASS — no `flow_mode`; no `runtime_checkpoint_strategy`/`runtime_wait_timeout_s`; `.env.example` drift green — `grep -rn "flow_mode"` in `src/` returns nothing; `tests/unit/decode/config` 104/104 passing (includes the drift test).
- [ ] Blocked by task 133, not this task — full unit suite green — confirmed the branch installs pydantic-ai 2.33 and `build_agent()` itself raises `TypeError: Agent.__init__() got an unexpected keyword argument 'output_retries'` at HEAD, independent of this task's changes. Delta-checked instead: unit failure/error SET has 0 new members (after accounting for a confirmed pure rename) and 18 fewer; integration failure/error SET has 0 new members and 5 fewer.

**Evidence**
```
$ uv run pytest tests/unit -q -p no:randomly --continue-on-collection-errors
101 failed, 1983 passed, 78 errors in 32.95s

$ git stash -u && uv run pytest tests/unit -q -p no:randomly --continue-on-collection-errors && git stash pop
108 failed, 1946 passed, 87 errors in 35.46s   # baseline, independently reproduced

$ uv run pytest tests/integration -q -p no:randomly --continue-on-collection-errors
35 failed, 51 passed, 16 skipped, 11 errors in 160.44s   # = 46 failed+errored, matches SWE claim

$ git stash -u && uv run pytest tests/integration ... && git stash pop
37 failed, 48 passed, 16 skipped, 14 errors in 157.09s   # = 51 failed+errored baseline, independently reproduced

$ comm -13 baseline_fails.sorted now_fails.sorted   # unit: names in now but not baseline
FAILED tests/unit/decode/agent/test_factory.py::test_the_provider_key_comes_from_settings_for_gemini
FAILED tests/unit/decode/agent/test_factory.py::test_the_provider_key_comes_from_settings_for_openrouter
# both confirmed via `git diff` to be renames of test_flow_mode_reads_the_{gemini,openrouter}_key_from_settings,
# present in the baseline failure list under the old name — not new failures.

$ comm -13 baseline_int_fails.sorted now_int_fails.sorted   # integration: empty — zero new names

$ uv run decode replay exec-1 --from step
Error: No such command 'replay'.
$ uv run decode run --hitl "do it"
Error: No such option: --hitl Did you mean --help?
```

**Other issues found**
- `tests/integration/test_runtime_store_isolation.py::test_adverse_collection_order_never_writes_a_real_store` shells out to `pytest tests/unit/decode/runtime/test_store_isolation.py tests/unit/decode/test_cli.py tests/unit/decode/runtime/test_run_command.py` — the first path no longer exists (deleted by this task), so the subprocess now fails with `ERROR: file or directory not found` instead of ever exercising its actual assertion. This test was already failing at baseline (for a different, also-obsolete reason — a kitaru `ImportError` during collection), so it is **not a new regression** and does not block this PASS. But its entire premise — guarding the `isolated_kitaru_store` fixture's collection-order hazard — died in this same task when `tests/support/runtime_fixtures.py` was deleted; as written the test can never pass again. Recommend deleting this file in a fast-follow (ideally folded into this task before merge, since it is squarely the same "no stubs, no shims" surface ADR-0019 §1 targets) rather than leaving a permanently red, unfixable integration test in the suite.
- Everything else checked out clean: no stray `kitaru`/`flow_mode`/`replay`/`hitl` references outside the two intentionally-kept/renamed spots; no orphaned fixture imports (`env_bucket_name`, `inline_wait_resolver`, `isolated_kitaru_store` — all zero remaining references anywhere in `tests/` or `src/`); the deliberate scope deviation (deleting `AgentDeps.headless_durable_waits` + its `approval.py` branch) is sound — genuinely dead code with no producer, correctly flagged by the SWE, and pinned by a regression test.

**VERDICT: PASS**

### [SWE] 2026-08-22 11:05 — Fast-follow

- Applied the Tester's fast-follow: deleted `tests/integration/test_runtime_store_isolation.py` — its premise (the `isolated_kitaru_store` collection-order hazard) died with `tests/support/runtime_fixtures.py` in this task, and it shelled out to the now-deleted `tests/unit/decode/runtime/test_store_isolation.py`. No references to it, `isolated_kitaru_store`, or `test_store_isolation` remain in `tests/`, `src/`, or the `Makefile`; `tests/integration` now collects 112 tests with zero collection errors, and this task's 172 owned tests plus `make format-check`/`make lint-check` are green.
