# 0017. Resilient, parallel-by-default Explore fan-out — one agent call, N validated children, one labelled aggregate

**Status:** Accepted
**Date:** 2026-07-13
**Supersession:** Partially supersedes [ADR-0013](0013-explore-subagents.md) — specifically §7's
fan-out *mechanism* ("N `agent(...)` calls in one response run concurrently … no custom
`asyncio.gather`" and the `agent(prompt)` single-prompt signature it implies) and §8's *result
shape* ("the child's final text — truncated to `subagent_result_max_bytes` — is returned as the
tool result"). **Retained from ADR-0013 and still in force:** §1 (in-process nested-run boundary),
§2 (read-only child toolset), §3 (subagent axis), §4 (grants), §5 (permissions come free), §6 (the
set-once main-agent seam), §7's per-loop semaphore + `subagent_max_parallel` + per-child
`UsageLimits` + no-usage-threading, §8's `truncate()` idiom + ephemeral transcripts +
silent-until-done, §9 (headless no-special-casing) and §10.

## Context

ADR-0013 shipped `agent(prompt)`: one call, one read-only Explore child. Parallelism existed but
was *voluntary* — it only happened if the parent model chose to emit N tool calls in one response.
Three gaps surfaced in practice: (a) no default fan-out — a lazy parent explores serially; (b) no
input contract — a one-word spawn prompt produces a useless child; (c) no output validation — an
empty report, or a child that answered from model memory without reading any code, folds back as
if it were evidence.

Scope is deliberately narrow (human-locked): resilience = input contract + output validation.
**Non-goals:** prompt-injection hardening; transport-level model-flake retry/backoff.

Framework facts verified against the installed `pydantic-ai-slim` 1.95 (not docs-from-memory):

* **`ModelRetry`** raised from a tool sends a retry prompt to the model (`exceptions.py`). The
  per-tool retry budget **defaults to 1** (`Agent.__init__`: `effective_retries = retries if
  retries is not None else 1`); exceeding it raises `UnexpectedModelBehavior` and aborts the run —
  so a tool that nags via `ModelRetry` must register with a raised `retries=` budget.
* **`AgentRunResult.all_messages()`** returns the child's full transcript (`run.py:461`); "the
  child made zero tool calls" = no `ToolCallPart` in any of its `ModelResponse`s.
* Nested `agent.run()` re-entry and **`UsageLimits(request_limit=…)`** behave as ADR-0013
  recorded — unchanged.

## Decision

All of the following are one design; each numbered item is a facet, not a separate decision record.

1. **BREAKING signature: `agent(ctx, prompts: list[str]) -> str`.** ONE tool call spawns N
   children; the parent model authors the N distinct angles (it has the question); the **harness**
   guarantees the parallelism, the aggregation, and the minimum count via the tool description's
   "at least 3 DISTINCT angles for a broad question". A single-child exploration is a one-element
   list. Fan-out no longer depends on the model volunteering N tool calls.
2. **Structural guards (deterministic, pre-spawn).** Empty list → `ModelRetry` ("give at least one
   prompt"). `len(prompts) > 6` → `ModelRetry` (consolidate). The width cap is a module constant —
   least mechanism, not a config knob; `subagent_max_parallel` (4) remains the separate
   CONCURRENCY ceiling (a 6-wide fan-out runs 4, then 2). Duplicates allowed — a prompt-quality
   issue the substance guard already nags.
3. **Input contract, zero extra LLM calls.** Free-form string per angle (no rigid slots). Quality
   enforced by (i) the hardened tool description — each prompt carries the question, the scope to
   search, and what the report must contain — and (ii) a deterministic, cheap substance guard
   raising `ModelRetry` that names the offending prompt index and what is missing. Exact heuristic
   is the implementer's call. Because the default per-tool retry budget is 1, the `agent` tool
   registers with `retries=` ≥ 2 so guard nags coach the model instead of aborting the run.
4. **Concurrency: harness `asyncio.gather` inside the tool body**, order-preserving, each child
   attempt acquiring the existing per-loop semaphore. (ADR-0013 relied on pydantic-ai scheduling N
   separate tool calls; with one list-carrying call, the gather moves into the tool — the
   semaphore and per-child `UsageLimits` are unchanged.)
5. **Aggregation = labelled concatenation, NO synthesis LLM call.** Each child's section is headed
   by its own prompt — `## Subagent {i} — "{prompt}"` — in prompt order. A failed child STILL gets
   its section, carrying an explicit failure note: partial results beat an exception that discards
   the sibling reports. The parent model is the synthesizer.
6. **Shared context budget.** Each child's report is truncated (shared `truncate()` idiom) to
   `subagent_result_max_bytes // len(prompts)`, so the TOTAL fold stays ~16 KB regardless of
   width — this is what makes "3 by default" a free default instead of a context tax.
7. **Output validation + exactly one retry.** A report is BAD iff it is (i) empty/whitespace-only,
   or (ii) the child made ZERO tool calls (`all_messages()` scan — it answered from memory instead
   of reading the code). A bad report gets exactly ONE re-spawn with an appended nudge; a second
   bad attempt folds the failure note ("The subagent returned no usable report."). Never infinite
   retry. The defensive `DeferredToolRequests` output classifies as BAD.
8. **Child report contract (persona).** `explore.md`'s body is rewritten: the report is a tight
   structured summary — the finding, the file:line evidence, the trace followed — deliberately
   compressed for sibling-shared budgets. A summary with no file:line evidence is a hallucination
   tell (pairs with §7-ii).
9. **Synthesis Footer.** The harness appends one instruction to every aggregated result: compile
   the N reports into ONE answer — prose PLUS a text-based diagram of the structure found
   (ASCII/box-drawing default; Mermaid only for genuine graphs — the Rich TUI renders Mermaid as
   raw source). Just-in-time, one place, never in persona prompts; appended post-truncation so it
   costs no child budget.
10. **Discipline (unchanged).** TDD-first, `filterwarnings=["error"]`, full annotations, library
    code logs, no new settings, tests mirror `src/` 1:1.

## Diagram

**Guard → fan out → validate → aggregate** — one `agent(prompts)` call, N resilient children, one labelled fold.

```mermaid
flowchart TD
    model["parent model response:<br/>ONE agent(prompts=[p1..pN])"]:::call
    guards{"deterministic guards<br/>empty? · width > 6? · under-specified?"}:::gate
    nag["ModelRetry — names the fix<br/>(tool registered with retries ≥ 2)"]:::retry
    gather["asyncio.gather (prompt order)<br/>each attempt under the per-loop Semaphore<br/>subagent_max_parallel"]:::tool

    model --> guards
    guards -- "bad input" --> nag --> model
    guards -- ok --> gather

    subgraph children["Explore children — fresh read-only deps, UsageLimits per child"]
        c1["child 1"]:::child
        c2["child 2"]:::child
        cn["child N"]:::child
    end
    gather --> c1 & c2 & cn

    validate{"report BAD?<br/>empty · zero tool calls<br/>(all_messages scan)"}:::gate
    c1 --> validate
    c2 --> validate
    cn --> validate
    validate -- "bad, 1st time" --> retry["ONE re-spawn<br/>same prompt + nudge"]:::retry --> validate
    validate -- "bad again" --> note["failure note<br/>'no usable report'"]:::fail
    validate -- good --> trunc["truncate() to<br/>subagent_result_max_bytes // N"]:::result

    trunc --> agg["labelled aggregate<br/>## Subagent i — &quot;prompt&quot; …"]:::result
    note --> agg
    agg --> footer["+ Synthesis Footer:<br/>one answer, prose + text diagram"]:::result
    footer --> model

    classDef call fill:#1e293b,stroke:#0ea5e9,color:#e2e8f0
    classDef gate fill:#713f12,stroke:#eab308,color:#fef9c3
    classDef retry fill:#4c1d95,stroke:#a855f7,color:#ede9fe
    classDef tool fill:#334155,stroke:#a855f7,color:#e9d5ff
    classDef child fill:#14532d,stroke:#22c55e,color:#dcfce7
    classDef fail fill:#7f1d1d,stroke:#ef4444,color:#fecaca
    classDef result fill:#334155,stroke:#38bdf8,color:#bae6fd
```

## Consequences

- **Breaking, clean break.** Every caller of the old shape moves: the tool schema, the unit tests
  (`tests/unit/decode/tools/test_agent.py`), BOTH capstones (`test_subagents_capstone.py`,
  `test_observability_capstone.py` — its fan-out uses `args={"prompt": …}`), and the manual-e2e-qa
  playbook row. No `agent(prompt)` compatibility shim.
- **Parallelism is now a harness guarantee**, not a model courtesy — and the fold cost is
  width-independent (budget division), so wide exploration stops taxing the parent's context.
- **A broken child is bounded**: at most 2 spawns, then a note; siblings always survive. The
  failure note is honest UX — the parent model (and the human reading the transcript) sees which
  angle produced nothing.
- **The guards spend model turns, not tokens on children**: a nag is one cheap retry leg, and the
  raised per-tool retry budget is the price of coaching instead of aborting.
- **Replay/headless unchanged** (ADR-0013 §9 stands): the whole fan-out is still one opaque
  cache-safe tool call — richer inside, same checkpoint shape outside.
- **ADR-0013 stays the record** for the boundary, toolset, seam, permissions, and ephemerality;
  its header gains a dated amendment pointing here (body left unedited, ADR-0011 style).
- **Seams left open:** a `subagent_type` param when a second subagent persona arrives; bridging
  child events to the TUI (live sub-progress); an adaptive (model-scored) report validator if the
  deterministic predicate proves too coarse — a real measured limit would justify the upgrade.
