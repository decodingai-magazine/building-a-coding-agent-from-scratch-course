---
id: 088-agent-tool-explore-subagent-runner
feature: explore-subagents
status: pending
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

- [ ] `TOOL_KIND["agent"] is ToolKind.READ_ONLY` and `"agent" in KNOWN_TOOL_NAMES`; a parent turn that
  calls `agent(...)` produces no `PermissionRequested` (it runs inline). Test in a new
  `tests/unit/decode/tools/test_agent.py` (+ a registry assertion).
- [ ] build / plan / code-reviewer load with `"agent"` in `tools`; explore does NOT list `agent`
  (loader/agent-def tests).
- [ ] The runner builds **fresh** deps: the child's `gate` and `task_store` are distinct objects
  from the parent's (assert identity), `emit` is the no-op sink, and `active_agent` is explore
  (`subagent is True`). Child model == parent model (same `Agent` via the seam; no `AgentDef.model`
  field). Driven by a `FunctionModel`-backed main agent set via `set_main_agent`.
- [ ] The child run is invoked with `usage_limits=UsageLimits(request_limit=settings.subagent_max_requests)`
  and **without** `usage=ctx.usage`: after a spawn turn the parent handler's `last_input_tokens` /
  `run.usage()` (`agent/loop.py:146-155,407`) excludes the child's requests/tokens (spy/assert).
- [ ] A read-only child never raises `ApprovalRequired`, so `agent.run()` resolves to text with **no**
  gate / Decision Channel involvement — the child's `read`/`glob` calls execute without any resolver
  being invoked (assert the deny resolvers are never called).
- [ ] Recursion is impossible: with `active_agent=explore` the `agent` tool is hidden from the child by
  `prepare=` (`tools/registry.py:205-225`) — a child `FunctionModel` that emits an `agent` call finds no
  such tool; the child's visible toolset is exactly `{read, glob, grep, lsp}`.
- [ ] A long child report is truncated to `subagent_result_max_bytes` and returned as a plain `str`.
- [ ] The semaphore bounds concurrency: with `subagent_max_parallel` set low (e.g. 2) and an
  instrumented blocking child, at most that many children run at once (an overlap counter never
  exceeds the cap).
- [ ] `config/settings.py` + `.env.example` carry the three new vars (documented, `Field(gt=0)`);
  `import decode.cli` stays kitaru-free.
- [ ] `make pre-commit` green; `filterwarnings=["error"]` clean; `uv lock --check` passes.

## Out of scope

- README / AGENTS.md prose + the e2e manual-QA row (#089).
- The capstone (#090).
- A `subagent_type` parameter; bridging children's events to the TUI; usage threading / per-child cost
  (M10 / Opik) — all ADR-0013 non-goals.
- Any change to `runtime/flow.py` — headless needs no special-casing (ADR-0013 §9); `agent` is
  deliberately NOT added to the `{"cache": False}` set at `flow.py:396-415`.

## Log
