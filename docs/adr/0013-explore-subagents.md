# 0013. Explore subagents — the Agent tool & in-process parallel fan-out

**Status:** Accepted
**Date:** 2026-07-04
**Amended:** 2026-07-13 — §7's fan-out mechanism (N `agent(prompt)` calls per response) and §8's
single-report result shape are **superseded by
[ADR-0017](0017-resilient-parallel-subagent-fanout.md)**: the tool is now
`agent(prompts: list[str])` — one call, harness-gathered fan-out, labelled aggregation, input and
output validation. §1–6, the §7 semaphore / `UsageLimits` / no-usage-threading, the §8 `truncate()`
idiom / ephemerality / silence, §9 and §10 stand. The body below is left unedited.
**Supersession:** Partially supersedes [ADR-0003](0003-milestone-2-permission-system-and-agents-catalog.md)
§5 — specifically its "Four built-ins (main-agent only — **no subagent spawning** this milestone)"
clause and the matching "Seams left for later milestones: subagent spawning" consequence. Everything
else in ADR-0003 (the permission modes, the tool-kind classification, the rule engine, the per-tool
`prepare=` restriction, the dynamic instructions hook) is **retained and reused** — this ADR is built
on top of it, not against it.

## Context

ADR-0003 shipped an Agents Catalog (Build / Plan / Explore / Code-Reviewer) whose personas are
selected as the **one main agent** and deferred subagent spawning as a named future seam. The
glossary already promises the concept — "**Subagent** — A child agent spawned by the main agent via
the Agent tool; runs a scoped task and returns a compressed result" (`docs/glossary.md`) — and
AGENTS.md's Project Structure lists `agents/` as "agents catalog … + subagents". Milestone 9 pays
that promise off: the primary agent can spawn **Explore subagents** that read a large codebase in
parallel and hand back one compressed report, via a new **`agent` tool**.

The mechanism ADR-0003 §6-7 built is the key enabler and must not be reinvented: per-agent tool
restriction rides a per-tool `prepare=` callback reading `ctx.deps.active_agent.tools`
(`tools/registry.py`), and the per-agent system prompt rides the dynamic `@agent.instructions` hook
reading `ctx.deps.active_agent.prompt` (`agent/factory.py`). "One Agent, swapped state — not swapped
agents" (ADR-0003 Consequences). A subagent is therefore *the same Agent re-entered with fresh,
narrowed deps* — no new machinery.

Three framework facts were verified against the installed **pydantic-ai-slim** (`pyproject.toml`)
and the current Pydantic AI docs, because they shape the design:

* **Agent delegation is a nested `agent.run()` from inside a tool.** A tool may `await other.run(prompt,
  usage=ctx.usage, usage_limits=UsageLimits(request_limit=N))`; passing `usage=ctx.usage` folds the
  child's usage into the parent total. The delegate need not be a *different* Agent object — an Agent
  is a stateless run-factory, and `agent.override(model=…)` is contextvar-scoped, so re-entering the
  same object (even concurrently) is supported. decode already delegates this way for the compaction
  summarizer and memory extractor (`context/compaction.py`, `memory/extract.py`).
* **Parallel tool calls are native.** "When a model returns multiple tool calls in one response,
  Pydantic AI schedules them concurrently using `asyncio.create_task`, executing them in the order the
  model emitted them." So N `agent(...)` calls in one model response fan out concurrently with **no
  custom `asyncio.gather`**.
* **`UsageLimits(request_limit=…, total_tokens_limit=…, tool_calls_limit=…)`** bounds a run
  independently of usage accounting — the runaway cap for a child.

## Decision

1. **Boundary = in-process nested agent loop, framed for parallel fan-out (claude-code's model).** A
   child is the same `agent.run()` re-entered with narrowed deps — **not** a child session, **not** a
   subprocess. The three reference models, on the axes that matter here:

   | Reference model | Boundary | Isolation | Cost per child | Persistence | Result folding |
   |---|---|---|---|---|---|
   | **claude-code** (chosen) | in-process nested agent loop — same `Agent`, `agent.run()` re-entered with fresh narrowed deps | *logical*: fresh gate / task_store / history / event sink; shares the process + interpreter | cheapest — reuses the built `Agent`, model, and HTTP client; no spawn, no re-init | ephemeral — child transcript lives only for the call, then discarded | trivial — the child's final text *is* the tool result, folded inline |
   | **opencode** | persistent child *session* object, kept alive across calls | session-level state, still in-process | medium — a live session per child held open | durable for the session's lifetime | medium — read the child session's output back |
   | **pi** | out-of-process *subprocess* (child agent as an OS process) | strong — separate process / memory / fs | most expensive — process spawn + IPC + model re-init | independent process lifetime | heaviest — serialize + IPC the result home |

   **Why in-process wins for decode.** decode is a single-process teaching harness, and the narrowing
   mechanism (`deps.active_agent` + `prepare=` + the instructions hook) *already exists* from ADR-0003
   §6-7 — a child is literally the main Agent re-entered with fresh read-only deps, so the whole feature
   is deps construction + one thin tool, not new architecture. Parallel fan-out is native
   (`asyncio.create_task`), result-folding is trivial (the child's final text is the tool result), and
   cost is minimal (reuse the model + HTTP client) — which matters because fan-out multiplies model
   calls against Gemini free-tier limits. Subprocess isolation (pi) buys nothing here: children are
   **read-only by construction** (no writes/bash to contain). A persistent child session (opencode)
   buys durability a scoped read-only exploration does not need, and complicates the session log. The
   cost of the choice is honest and accepted: children share the parent's fault domain (a child
   exception surfaces in-process) and there is no memory/fs isolation — acceptable precisely because the
   toolset is read-only.

2. **Child toolset = `read` / `glob` / `grep` / `lsp` ONLY — read-only by construction.** Excluded, each
   for a reason: `bash` (no read-only guarantee), `web_fetch` / `write` / `edit` (side effects),
   `todo_write` (child bookkeeping is noise), `skill` (not needed for scoped reading), `ask_user`
   (**it blocks on the single Decision Channel — a child would deadlock the fan-out**), and `agent`
   itself (**recursion default-deny — opencode's lesson**). Because a child runs a plain `agent.run()`
   (not the harness `Runner`/`AgentTurnHandler`) and its tools never raise `ApprovalRequired`, the child
   never touches the gate or the Decision Channel — which is *why* `ask_user` is forbidden.

3. **Explore is demoted to subagent-only via a minimal primary/subagent axis.** `AgentDef` gains a
   boolean **`subagent`** field (default `False`; a deliberately non-colliding name — `AgentDef.mode`
   is the permission mode). `explore.md`'s `tools:` list is edited in the file to exactly
   `read/glob/grep/lsp`, it declares `subagent: true`, and its body states its final message *is* the
   report. `--agent explore` (CLI) and `/agent explore` (mid-session) are rejected with a friendly line
   listing only the **primary** agents (build / code-reviewer / plan). No spawn-time narrowing logic —
   explore is never a primary anymore.

4. **The `agent` tool is granted to build + plan + code-reviewer, never to explore**, and registered as
   `ToolKind.READ_ONLY`.

5. **Permissions come free — verified, not built.** `agent` is a non-gated READ_ONLY tool (it never
   raises `ApprovalRequired`, like the read-only file tools), so it runs inline and never prompts; and
   per `permissions/gate.py` READ_ONLY auto-allows in every mode. The child's tools are all READ_ONLY,
   so children never prompt either. Children still get a **fresh** `PermissionGate` + **fresh**
   `AgentDeps` (never the parent's mutable gate / task_store).

6. **Same-Agent re-entry via a module seam.** The `agent` tool reaches the running Agent through a
   set-once module seam (`set_main_agent(agent)` called by `build_agent`, mirroring bash's `_EXECUTOR`).
   Reusing the one built Agent (vs `build_agent()`-ing a child per spawn) means no model/HTTP-client
   rebuild per child, the child inherits the parent's model + any Model Override + the flow-mode
   keep-alive-free client for free, and one `agent.override(model=…)` in a test drives parent **and**
   children (a large hermetic-testability win). Recursion is structurally impossible: the child's
   `active_agent=explore` omits `agent`, so `prepare=` hides it — no depth counter needed.

7. **Native fan-out + a bounded, capped child run.** N `agent(...)` calls in one response run
   concurrently (`asyncio.create_task`, no custom gather). A **per-running-loop** `asyncio.Semaphore`
   sized to `subagent_max_parallel` (default 4) caps concurrent children (Gemini free-tier friendly);
   each child runs under `UsageLimits(request_limit=subagent_max_requests)` (default 25). The child run
   **does not** thread `usage=ctx.usage`, so the parent's `_last_input_tokens` footer gauge and the
   compaction trigger keep reflecting the parent's context only (`agent/loop.py`).

8. **Result = the child's final text only, compact and ephemeral.** The child's final message is
   truncated to `subagent_result_max_bytes` (default 16 KB) via the shared `truncate()` idiom
   (`tools/truncate.py`) and returned as the tool result — never the transcript. Child transcripts are
   ephemeral (not persisted): the parent history carries only the spawn call + summary, so `--resume`
   just works. The spawn renders through the **existing** pipeline (`ToolCallStarted` → announced,
   `ToolResult` → panel); children get a no-op event sink, so their internal events are silent
   (silent-until-done).

9. **Headless needs no special-casing.** The whole child run is one opaque tool call → one checkpoint
   under `checkpoint_strategy="calls"`. A read-only child's cached summary is **replay-safe** (contrast
   the sandbox-bash `{"cache": False}` in `runtime/flow.py`), so the default caching stands and `agent`
   is never added to the cache-disable set. Documented ceilings: nested child model calls are **not**
   individual replay anchors, a `decode replay --model` swap does not reach inside a child, and child
   token spend is invisible until Opik lands (M10). Child model = parent model (`AgentDef` has no
   `model` field, by design). **Closed by M10 (ADR-0014):** child token spend is now visible in Opik
   traces — the child `agent.run()` nests inside the parent turn's `chat_turn` trace (same asyncio task
   / contextvars), so per-child token usage rides its own spans.

10. **Discipline (unchanged).** `filterwarnings=["error"]`, UTC-aware datetimes, full annotations incl.
    `-> None`, library code logs (never `print()`), infra imported-not-abstracted, `tests/` mirror
    `src/` 1:1, TDD-first, no network in CI (`FunctionModel`; one live smoke `skipif`-guarded).

## Diagram

**Spawn-and-fold** — one model response fans out to N read-only children, each folded back as a tool result.

```mermaid
flowchart TD
    subgraph parent["Parent agent loop — build / plan / code-reviewer"]
        model["model response:<br/>N × agent(prompt) in one turn"]:::call
        atool["agent tool<br/>(READ_ONLY → runs inline, never prompts)"]:::tool
        sem{{"per-loop asyncio.Semaphore<br/>subagent_max_parallel"}}:::gate
    end

    model --> atool --> sem
    sem --> c1
    sem --> c2
    sem --> c3

    subgraph children["Explore subagents — in-process agent.run(), FRESH read-only deps"]
        c1["child 1 · active_agent=explore<br/>read · glob · grep · lsp"]:::child
        c2["child 2"]:::child
        c3["child N"]:::child
    end

    c1 -- "final text = compressed report" --> fold["truncate() → ToolResult"]:::result
    c2 --> fold
    c3 --> fold
    fold --> model

    note["fresh gate + fresh task_store + no-op emit (silent TUI)<br/>no agent tool in child → no recursion<br/>no usage threading → parent gauge + compaction unchanged"]:::note
    children -.-> note

    classDef call fill:#1e293b,stroke:#0ea5e9,color:#e2e8f0
    classDef tool fill:#334155,stroke:#a855f7,color:#e9d5ff
    classDef gate fill:#713f12,stroke:#eab308,color:#fef9c3
    classDef child fill:#14532d,stroke:#22c55e,color:#dcfce7
    classDef result fill:#334155,stroke:#38bdf8,color:#bae6fd
    classDef note fill:#0f172a,stroke:#64748b,color:#cbd5e1
```

## Consequences

- **The catalog grows a primary/subagent axis.** `AgentDef.subagent` distinguishes personas selectable
  as a primary (`--agent` / `/agent`) from those only spawnable via the `agent` tool. Adding a second
  subagent later is a new `subagent: true` file + a `subagent_type` param on the tool — a param
  addition, not a rewrite.
- **Explore stops being selectable as a primary.** `--agent explore` / `/agent explore` /
  `select_agent("explore")` now error with a primaries-only list; the existing explore-selection tests
  are updated (`test_loader.py`, `test_select.py`, `test_app_e2e.py`).
- **One Agent, re-entered — not rebuilt.** The child reuses the built model + client via the module
  seam, so `agent.override(model=…)` covers parent + children in tests.
- **Seams left for later:** a second subagent + a validated `subagent_type` param; bridging children's
  events to the TUI (live sub-progress); usage threading + per-child cost once Opik lands (M10); a
  deployed-stack proof that a headless subagent replays.
- **Per-child cost is now delivered (M10, ADR-0014).** The "per-child cost once Opik lands" seam above is
  closed: an Explore child's `agent.run()` nests inside the parent turn's Opik trace, so per-child token
  spend shows on its own spans. The remaining seams stay open.
- **Risks to confirm during implementation:** (a) the module semaphore's event-loop affinity — created
  per running loop so it is safe under both the single REPL loop and Kitaru's per-call loops; (b) that
  re-entering the same `Agent` object *concurrently* (parallel children) does not interfere under the
  real Gemini path (Agent is a stateless run-factory; `override` is contextvar-scoped); (c) that
  `prepare=` hides `agent` from a child under real Gemini as it does under `FunctionModel`; (d)
  fan-out against Gemini free-tier rate limits — the semaphore + `request_limit` are the guards.
