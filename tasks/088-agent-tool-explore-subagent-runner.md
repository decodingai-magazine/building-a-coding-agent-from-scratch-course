---
id: 088-agent-tool-explore-subagent-runner
feature: explore-subagents
status: done
---

# The `agent` tool + Explore-subagent runner + settings

Tags: `agents`, `tools`, `subagents`, `settings`
Depends on: #087
Blocks: #090

## Scope

Add the model-callable **`agent` tool** and the in-process **Explore-subagent runner** (ADR-0013
§1,5-9), plus the three tuning settings. A child is the same `Agent` re-entered via `agent.run()` with
fresh, narrowed read-only deps — reusing ADR-0003 §6-7's `prepare=` + instructions narrowing — so
there is no new loop and no subprocess.

- **Settings** (`config/settings.py`, near the tool caps at :130-139; use `Field(gt=0)` like :198,283)
  + mirror in `.env.example` under a new `--- Subagents ---` block:
  - `subagent_max_parallel: int = Field(4, gt=0)` — concurrent-children cap (Gemini free-tier).
  - `subagent_max_requests: int = Field(25, gt=0)` — per-child `UsageLimits(request_limit=…)` runaway cap.
  - `subagent_result_max_bytes: int = Field(16_000, gt=0)` — the child-report cap, applied via the
    shared `truncate()` idiom (`tools/truncate.py`; the project's truncation is byte/line-based, so the
    "char cap" from grooming is realized as a byte cap — ADR-0002 §7,10).
- **`tools/agent.py`** — mirror `tools/bash.py`'s tool-plus-`_EXECUTOR`-seam and `tools/skills.py`'s
  thin dispatcher (:49-81):
  - `AGENT_TOOL_NAME = "agent"`.
  - A set-once module seam: `set_main_agent(agent)` / `_require_main_agent()` (raises a clear error if
    unset — a misconfiguration, like bash's executor seam).
  - A **per-running-loop** semaphore: `_semaphore()` caches one `asyncio.Semaphore(subagent_max_parallel)`
    keyed by `asyncio.get_running_loop()`, so it is loop-safe under both the single REPL loop and
    Kitaru's per-call loops (the same per-call-loop hazard `agent/factory.py:111-125` handles for the
    HTTP client). Within one model response all N fan-out calls share one loop → the cap bites.
  - `async def agent(ctx: RunContext[AgentDeps], prompt: str) -> str`: build a **fresh** child
    `AgentDeps` — parent `ctx.deps.cwd` + `ctx.deps.harness_home`; a **no-op / log-only** `emit` so
    children are silent in the TUI (ADR-0013 §8); a **fresh** `PermissionGate`; a **fresh** empty
    `task_store`; `active_agent = load_agent("explore")` (assert `subagent is True` — you may only
    spawn a subagent); the headless deny resolvers (`runtime/flow.py:418-437` pattern) so a stray
    gated/ask call fails safe instead of hanging. Then, under `async with _semaphore()`:
    `r = await _require_main_agent().run(prompt, deps=child_deps,
    usage_limits=UsageLimits(request_limit=settings.subagent_max_requests))` — **without**
    `usage=ctx.usage` (ADR-0013 §7,10). Return `truncate(str(r.output), max_lines=…,
    max_bytes=settings.subagent_result_max_bytes).text`. Defensive: if `r.output` is a
    `DeferredToolRequests` (impossible for a read-only child — its tools never raise `ApprovalRequired`),
    return a short "subagent could not complete" note rather than the object.
  - Import `AgentDeps` / `PermissionGate` / `load_agent` / `truncate` **lazily inside the function** to
    avoid the tools→agents import cycle (mirrors the lazy `_default_active_agent` at `deps.py:76-85`).
- **Registry** (`tools/registry.py:78-148`): add
  `ToolSpec(name=agent_module.AGENT_TOOL_NAME, func=agent_module.agent, kind=ToolKind.READ_ONLY)` and
  import `decode.tools.agent`. READ_ONLY means it runs inline and never prompts (like the read-only file
  tools) and auto-allows in every mode (`permissions/gate.py:135-136`); it joins `KNOWN_TOOL_NAMES`
  automatically (`tools/__init__.py:35` derives it from `TOOL_KIND`).
- **Factory** (`agent/factory.py:95-108`): after `register_tools(agent)` + `_register_instructions(agent)`,
  call `set_main_agent(agent)` — the seam wiring, mirroring the established idiom.
- **Grant `agent`** to `build.md`, `plan.md`, `code-reviewer.md` `tools:` lists (NEVER `explore.md`).

## Acceptance Criteria

- [x] `TOOL_KIND["agent"] is ToolKind.READ_ONLY` and `"agent" in KNOWN_TOOL_NAMES`; a parent turn that
  calls `agent(...)` produces no `PermissionRequested` (it runs inline). Test in a new
  `tests/unit/decode/tools/test_agent.py` (+ a registry assertion).
- [x] build / plan / code-reviewer load with `"agent"` in `tools`; explore does NOT list `agent`
  (loader/agent-def tests).
- [x] The runner builds **fresh** deps: the child's `gate` and `task_store` are distinct objects
  from the parent's (assert identity), `emit` is the no-op sink, and `active_agent` is explore
  (`subagent is True`). Child model == parent model (same `Agent` via the seam; no `AgentDef.model`
  field). Driven by a `FunctionModel`-backed main agent set via `set_main_agent`.
- [x] The child run is invoked with `usage_limits=UsageLimits(request_limit=settings.subagent_max_requests)`
  and **without** `usage=ctx.usage`: after a spawn turn the parent handler's `last_input_tokens` /
  `run.usage()` (`agent/loop.py:146-155,407`) excludes the child's requests/tokens (spy/assert).
- [x] A read-only child never raises `ApprovalRequired`, so `agent.run()` resolves to text with **no**
  gate / Decision Channel involvement — the child's `read`/`glob` calls execute without any resolver
  being invoked (assert the deny resolvers are never called).
- [x] Recursion is impossible: with `active_agent=explore` the `agent` tool is hidden from the child by
  `prepare=` (`tools/registry.py:205-225`) — a child `FunctionModel` that emits an `agent` call finds no
  such tool; the child's visible toolset is exactly `{read, glob, grep, lsp}`.
- [x] A long child report is truncated to `subagent_result_max_bytes` and returned as a plain `str`.
- [x] The semaphore bounds concurrency: with `subagent_max_parallel` set low (e.g. 2) and an
  instrumented blocking child, at most that many children run at once (an overlap counter never
  exceeds the cap).
- [x] `config/settings.py` + `.env.example` carry the three new vars (documented, `Field(gt=0)`);
  `import decode.cli` stays kitaru-free.
- [x] `make pre-commit` green; `filterwarnings=["error"]` clean; `uv lock --check` passes.

## Out of scope

- README / AGENTS.md prose + the e2e manual-QA row (#089).
- The capstone (#090).
- A `subagent_type` parameter; bridging children's events to the TUI; usage threading / per-child cost
  (M10 / Opik) — all ADR-0013 non-goals.
- Any change to `runtime/flow.py` — headless needs no special-casing (ADR-0013 §9); `agent` is
  deliberately NOT added to the `{"cache": False}` set at `flow.py:396-415`.

## Log

### [SWE] 2026-07-05 14:20 — Implementation

**Files modified**
- `src/decode/tools/agent.py` — NEW: the `agent` tool + Explore-subagent runner. Set-once main-Agent
  seam (`set_main_agent` / `_require_main_agent` / `reset_main_agent`, mirrors bash's `_EXECUTOR`),
  per-running-loop `asyncio.Semaphore` cache (`_semaphore` / `_reset_semaphores`), no-op child event
  sink (`_silent_emit`), child deny permission resolver, and `agent(ctx, prompt)` — fresh narrowed
  read-only child deps + nested `agent.run()` under the semaphore with `UsageLimits(request_limit=…)`
  and NO `usage=ctx.usage`, folding the child's truncated final text.
- `src/decode/tools/registry.py` — register `ToolSpec("agent", agent, READ_ONLY)` + import the module.
- `src/decode/agent/factory.py` — call `set_main_agent(agent)` after `register_tools` + instructions.
- `src/decode/config/settings.py` — `subagent_max_parallel` (4) / `subagent_max_requests` (25) /
  `subagent_result_max_bytes` (16_000), all `Field(gt=0)`.
- `.env.example` — new `--- Subagents ---` block documenting the three vars.
- `src/decode/agents/builtin/{build,plan,code-reviewer}.md` — grant `agent` in `tools:` (NOT explore).
- `tests/conftest.py` — autouse `_reset_subagent_seam` (clears the seam + semaphore cache per test).
- `tests/unit/decode/tools/test_agent.py` — NEW: 18 tests (seam, kind/known-names, persona grants,
  fresh-deps identity + BYPASS gate, no-usage-threading, deny-resolvers-untouched, recursion-impossible
  toolset, truncation, DeferredToolRequests fallback, semaphore cap, kitaru-free import).
- `tests/unit/decode/config/test_settings.py` — 9 subagent settings tests (defaults / env / `gt=0`
  rejection / `.env.example` drift guard).
- `tests/unit/decode/tools/test_registry.py`, `tests/unit/decode/agents/test_loader.py`,
  `tests/unit/decode/agent/test_factory.py` — updated the pre-existing exact-tool-set assertions to
  include `agent`; the 3 factory visibility tests stub the spawn seam so `TestModel(call_tools='all')`
  no longer spawns a real child.

**Tests**
- Unit: 1453 passing, 0 failing (`make unit-tests`). New/changed: 18 (test_agent.py) + 9 (settings).
- Integration: 97 passing, 0 failing (`make integration-tests`, 6m12s) — factory/registry touched.

**Acceptance criteria** — all met (see checked boxes above). Highlights:
- [x] READ_ONLY + known-name + no `PermissionRequested` — `test_agent.py::test_agent_is_registered_as_a_read_only_spec`, `::test_tool_kind_agent_is_read_only`, `::test_spawn_through_the_loop_folds_the_child_report_and_never_prompts`.
- [x] persona grants — `::test_primary_agents_grant_agent_tool_and_explore_never_does` + loader tests.
- [x] fresh deps (distinct gate/task_store, silent emit, explore, BYPASS) — `::test_spawn_builds_fresh_narrowed_read_only_child_deps`.
- [x] UsageLimits + no usage threading — same test + `::test_child_run_does_not_thread_parent_usage`.
- [x] no gate/Decision-Channel touch — `::test_child_read_only_tool_runs_without_touching_any_resolver`.
- [x] recursion impossible, child toolset == {read,glob,grep,lsp} — `::test_child_toolset_is_exactly_read_glob_grep_lsp`.
- [x] truncation to `subagent_result_max_bytes` — `::test_long_child_report_is_truncated_to_the_byte_cap`.
- [x] semaphore cap — `::test_semaphore_bounds_concurrent_children`.

**Evidence**
```
$ make unit-tests
======================= 1453 passed in 100.72s (0:01:40) =======================

$ make integration-tests
======================== 97 passed in 372.48s (0:06:12) ========================

$ make format-check && make lint-check
173 files already formatted
All checks passed!

$ uv lock --check
Resolved 149 packages in 3ms

# Offline end-to-end through the REAL build_agent() (FunctionModel at the network boundary):
# a parent turn fans out TWO agent(...) spawns in one response.
FINAL ANSWER: Done — folded 2 subagent reports.
PARENT usage requests (excludes children): 2
CHILD toolsets observed: [['glob', 'grep', 'lsp', 'read'], ['glob', 'grep', 'lsp', 'read']]
recursion impossible (no 'agent' in any child): True
FOLDED CHILD REPORTS: ["[explore] report for 'map the tools/ package'", "[explore] report for 'map the agents/ package'"]
```

**Notes**
- **Design decision the Tester should probe (concurrent same-Agent re-entry):** the child gate is a
  fresh `PermissionGate(mode=BYPASS)`, NOT default mode. Reason: the child runs a plain `agent.run()`
  with no harness loop to resolve a deferred approval, and the read-only file tools (`read`/`glob`/
  `grep`/`lsp`) DO raise `ApprovalRequired` via `needs_approval` in non-BYPASS mode — so a default-mode
  child halts on `DeferredToolRequests` at its first tool call (I hit this in red). BYPASS makes
  `needs_approval` return `False`, so read-only tools run inline and nothing touches the gate/Decision
  Channel — which is exactly what ADR-0013 §2,5 ("its tools never raise ApprovalRequired") relies on
  and the same posture as `runtime/flow.py::_build_headless_deps`. Worth an adversarial look.
- **Concurrent same-Agent re-entry held** under both `FunctionModel` (unit) and the parallel fan-out
  e2e (2 children on one `Agent`, `override` contextvar-scoped) — the ADR "risk to confirm". Not
  proven against real Gemini (no key here); that's the manual-QA row #089.
- `set_main_agent` is overwrite-assign (like bash's `install_executor`), not raise-on-reset — every
  `build_agent` re-installs its own Agent; `_require_main_agent` raises only when UNSET. An autouse
  conftest fixture resets the seam per test for hermeticity.
- A child exception (e.g. an `UnexpectedModelBehavior`) propagates in-process — the ADR §1 accepted
  trade-off, so no broad catch was added. This is what made `TestModel(call_tools='all')` in the 3
  factory visibility tests spawn a child that drove `lsp` into a bad-op crash; those tests now stub the
  spawn seam (they test `prepare=` visibility, not sub-runs).
- No commit yet — handing to the Tester first.

### [Tester] 2026-07-05 16:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` 173 files formatted; `make lint-check` all
  checks passed; ruff scoped to the 6 changed source/test files: all passed)
- Unit tests: 1453 passed / 0 failed (`make pre-commit`)
- Integration tests: 97 passed / 0 failed (`make integration-tests`, 5m35s) — no docker-proxy flake
- Warnings: 0 (`filterwarnings=["error"]`; 1453 pass ⇒ zero warnings)
- `uv lock --check` passes; `import decode.tools.agent, decode.cli` stays kitaru-free (verified directly)

**E2E adversarial pass** (17 scratch probes written against the real `build_agent()` + loop with a
`FunctionModel` at the network boundary; all green; file removed after the run)
- Happy path: parent turn spawns a child, child report folds back as the tool result, no permission
  prompt (READ_ONLY runs inline) → PASS
- Break path 1 (**security: forbidden toolset**, THE design decision): a child `FunctionModel` that
  tries to call each of `write / edit / bash / web_fetch / todo_write / ask_user / skill / agent` —
  for **all 8**: the tool is absent from the child schema (`{read,glob,grep,lsp}` only), the child's
  call is rejected as an *Unknown tool* (captured in the child's ephemeral transcript), it **never
  executes** (no `ToolReturnPart`), **no file side-effect** (`pwned.txt` / `pwned_bash.txt` absent),
  and the `agent` case spawns **no grandchild** (exactly one `agent.run`) → PASS. **BYPASS is sound:
  the real security boundary is the narrowed toolset via `prepare=`, not the gate mode.**
- Break path 2 (**reproduce the SWE's red**, empirical validation): a child re-entered with a
  DEFAULT-mode gate HALTS on `DeferredToolRequests` at its first `read`; the same child with a BYPASS
  gate runs the read inline and resolves to text → PASS (confirms the choice, not just the argument)
- Break path 3 (**concurrency: native fan-out + cap**): 5 `agent()` calls in ONE model response fan
  out through the real loop; with `subagent_max_parallel=2` the peak overlap counter == 2 (reached but
  never exceeded); all 5 reports fold back → PASS
- Break path 4 (**failure mode: child raises mid-fan-out**): one of two concurrent children raises;
  the `RuntimeError` propagates in-process (ADR §1 accepted), no hang, **no leaked-task warning**
  under `filterwarnings=error` → PASS
- Break path 5 (**parent gauge honesty**): parent `last_input_tokens` is IDENTICAL whether the child
  did 1 request (104 tok) or 5 (336 tok) — decisive proof child usage never threads into the parent
  (no `usage=ctx.usage`) → PASS
- Break path 6 (**boundary: multibyte truncation**): a 300-line UTF-8 (Japanese) report over a 200 B
  cap truncates at a `\n` boundary — valid UTF-8, no U+FFFD, no exception; a single giant `\n`-less
  multibyte line keeps the whole line cleanly → PASS
- Break path 7 (**runaway**): a child that loops `glob` forever is stopped by
  `UsageLimits(request_limit=3)` → `UsageLimitExceeded` (bounded, not an infinite hang) → PASS (see note)
- Break path 8 (**deny resolvers fail-safe**): `_deny_permission_resolver` returns a DENY decision
  (never hangs); `deny_user_question_resolver` raises `NoInteractiveUserError` → PASS
- Startup guard (**settings**): `SUBAGENT_MAX_PARALLEL=0` and `=-3` each raise a clean
  `ValidationError: Input should be greater than 0` at load (fail-fast, no deep fan-out crash) → PASS

**Acceptance criteria**
- [x] PASS — `TOOL_KIND["agent"] is READ_ONLY` + `"agent" in KNOWN_TOOL_NAMES`, spawn never prompts —
      `test_tool_kind_agent_is_read_only`, `test_agent_is_a_known_tool_name`,
      `test_spawn_through_the_loop_folds_the_child_report_and_never_prompts`; registry.py:151
- [x] PASS — build/plan/code-reviewer grant `agent`, explore does not — `load_builtin_agents()`
      confirmed directly (build/plan/code-reviewer `agent_in_tools=True`, explore `False`);
      `test_primary_agents_grant_agent_tool_and_explore_never_does` + loader tests
- [x] PASS — fresh deps (distinct gate+task_store, silent emit, explore `subagent=True`, BYPASS,
      child model == parent) — `test_spawn_builds_fresh_narrowed_read_only_child_deps`; agent.py:181-195
- [x] PASS — child run under `UsageLimits(request_limit=…)`, no `usage=ctx.usage`; parent gauge
      excludes child — `test_child_run_does_not_thread_parent_usage` + adversarial
      `test_parent_gauge_excludes_child_work` (light==heavy despite 336 vs 104 child tokens)
- [x] PASS — read-only child never raises `ApprovalRequired`; resolvers untouched —
      `test_child_read_only_tool_runs_without_touching_any_resolver` + red/BYPASS A/B probe
- [x] PASS — recursion impossible; child toolset == `{read,glob,grep,lsp}` —
      `test_child_toolset_is_exactly_read_glob_grep_lsp` + adversarial 8-forbidden-tool probe
- [x] PASS — long child report truncated to `subagent_result_max_bytes`, returned as `str` —
      `test_long_child_report_is_truncated_to_the_byte_cap` + multibyte edge probes
- [x] PASS — semaphore bounds concurrency (cap 2, overlap never exceeds) —
      `test_semaphore_bounds_concurrent_children` + adversarial loop-driven `peak==2` probe
- [x] PASS — 3 settings + `.env.example`, `Field(gt=0)`; cli kitaru-free — `test_subagent_defaults`,
      `test_rejects_non_positive_subagent_caps`, `test_env_example_lists_every_subagent_var`,
      `test_importing_the_agent_tool_and_cli_stays_kitaru_free`; startup guard verified directly
- [x] PASS — `make pre-commit` green, `filterwarnings` clean, `uv lock --check` passes — see summary

**Evidence**
```
$ make pre-commit
173 files already formatted
All checks passed!
======================= 1453 passed in 93.33s (0:01:33) =======================

$ make integration-tests
======================== 97 passed in 335.80s (0:05:35) ========================

$ SUBAGENT_MAX_PARALLEL=0 uv run python -c "from decode.config.settings import Settings; Settings(_env_file=None)"
  Input should be greater than 0 [type=greater_than, input_value='0', input_type=str]
```

**Other issues found** (non-blocking)
- **BYPASS confirmed sound — cite this in the ADR risk-note.** The child gate is BYPASS, so
  `needs_approval` returns `False` for every child call and nothing reaches the gate/Decision Channel.
  The security boundary is therefore *entirely* the narrowed toolset (`prepare=` hides all 8
  mutating/forbidden tools). Verified adversarially: every forbidden tool is invisible, unreachable,
  side-effect-free, and non-recursive. BYPASS can only "bypass" gates on tools the child can see —
  and it can see only `read/glob/grep/lsp`, all read-only. The choice is correct and matches the
  `runtime/flow.py::_build_headless_deps` precedent.
- **Runaway degrades as a hard exception, not a graceful note.** A child that exhausts
  `request_limit` raises `UsageLimitExceeded`, which propagates out of `agent()` and up through the
  spawning parent turn (no broad catch). This is ADR-0013 §1 consistent ("a child exception surfaces
  in-process") and bounded (not a hang), so it is not a defect — but a future refinement could catch
  it and fold a "subagent exceeded its request budget" note so one runaway child does not abort the
  whole parent turn. Flagging for PA/SWE consideration; out of scope for #088.

**VERDICT: PASS**
