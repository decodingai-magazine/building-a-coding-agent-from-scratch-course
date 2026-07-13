---
id: 103
feature: subagent-fanout
status: pending
---

# Agent tool fan-out: `prompts: list[str]`, harness-guaranteed concurrency, labelled aggregation

Depends on: none (first task of the feature). Implements ADR-0017 §1–2, §4–6.

## Scope

**BREAKING signature change** on the `agent` tool: ONE tool call now spawns N Explore Subagents
concurrently and folds ONE labelled aggregate back. ADR-0013 §7's "N `agent(...)` calls in one
response" fan-out shape is superseded — the harness now guarantees the parallelism and the
aggregation instead of hoping the model volunteers N calls.

**`src/decode/tools/agent.py`**

- Signature: `async def agent(ctx: RunContext[AgentDeps], prompts: list[str]) -> str`. A
  single-child exploration is a one-element list.
- **Structural guards, deterministic, before any child spawns** (ADR-0017 §2):
  - `prompts == []` → `ModelRetry` ("give at least one exploration prompt" — name the fix).
  - `len(prompts) > 6` → `ModelRetry` telling the model to consolidate its angles into at most 6.
    The width cap is a **module constant** (e.g. `MAX_FANOUT_PROMPTS = 6`), NOT a new setting —
    no `.env.example` / `Settings` churn (least mechanism; the drift test from 101 stays quiet).
  - Duplicate prompts are allowed — not deduped (a prompt-quality problem, task 104's territory).
- **Per-child runner helper** (e.g. `_spawn_child(...)`): the existing fresh-narrowed-deps
  construction moves in here unchanged (explore persona, BYPASS gate, `_silent_emit`, deny
  resolvers, fresh task_store, no usage threading, `UsageLimits(request_limit=
  settings.subagent_max_requests)`). The per-loop semaphore is acquired **per child attempt** —
  `subagent_max_parallel` (4) remains the CONCURRENCY ceiling and is a different limit from the
  width cap: a 6-wide Fan-out runs 4 at a time, then the last 2.
- **Concurrent spawn**: the tool body `asyncio.gather`s the N children (order-preserving). A child
  that RAISES (e.g. `UsageLimitExceeded`) must NOT discard its siblings: catch per child, fold an
  explicit failure note into that child's section, `logger.warning` the exception. Partial results
  beat an exception that discards the sibling reports (ADR-0017 §5). The defensive
  `DeferredToolRequests` case keeps producing its "could not complete" note as that child's
  section text (task 106 upgrades it into the bad-report machinery).
- **Shared context budget** (ADR-0017 §6): each child's report is truncated via the shared
  `truncate()` idiom to `max_bytes=settings.subagent_result_max_bytes // len(prompts)` (keep
  `max_lines=settings.max_output_lines`), so the TOTAL fold stays ~16 KB regardless of width.
- **Aggregation** (ADR-0017 §5): labelled concatenation, NO synthesis LLM call. Each child's
  section headed by its own prompt — `## Subagent {i} — "{prompt}"` (1-based, prompt order,
  blank-line separated), then its (truncated) report or its failure note.
- Update the module docstring; keep the tool docstring minimally accurate for the new shape (the
  full hardened model-facing description is task 104's).

**`src/decode/tools/registry.py`**

- Register the `agent` tool with a **raised per-tool retry budget** (e.g. `retries=3`; SWE's call,
  floor 2). Verified framework fact: pydantic-ai's per-tool `max_retries` defaults to **1**, so
  back-to-back `ModelRetry` nags (width cap → substance guard) would otherwise abort the whole
  run with `UnexpectedModelBehavior`. Registration stays `ToolKind.READ_ONLY`.

**`src/decode/config/settings.py`** — comment-only: the subagent block's ADR anchor gains ADR-0017.

**`.claude/skills/manual-e2e-qa/SKILL.md`** — update the `agent (Explore subagents)` row's
mechanics: one `agent(prompts=[…])` call, harness gather (no longer "no custom gather"), width
cap 6, per-child budget split, labelled aggregate panel.

**Tests** (TDD-first; the signature change breaks three existing files — updating them is
in-scope work, not cleanup):

- `tests/unit/decode/tools/test_agent.py` — `test_agent_takes_ctx_and_prompt_only` → params
  `["ctx", "prompts"]`; `_fanout_model` and every direct `agent_module.agent(ctx, "…")` call move
  to the list shape (prompts substantial enough to survive task 104's guard later — write them
  well-formed now). NEW: empty list → `ModelRetry`; 7 prompts → `ModelRetry` whose message says
  consolidate; 6 prompts pass the width guard; one-element list → one section; N-element →
  N sections in prompt order, each heading carrying its own prompt verbatim; per-child byte
  budget is `subagent_result_max_bytes // N` (patch the setting small, assert each section's
  body capped); one child raising → its section carries a failure note AND siblings' reports
  still fold; semaphore still bounds concurrent children (existing tracker test, list-shaped);
  registry test: `agent`'s spec/registration carries the raised retries budget.
- `tests/integration/test_subagents_capstone.py` — `_fan_out(n)` becomes ONE
  `ToolCallPart(tool_name=AGENT_TOOL_NAME, args={"prompts": [...n prompts...]})`;
  `len(sink.tool_calls()) == 1`; `_folded_reports` yields ONE aggregate containing N sections;
  the barrier/rendezvous test now proves the stronger claim — the HARNESS guarantees the overlap
  from a single tool call (no reliance on the model emitting N calls); ephemeral-transcripts +
  resume test updated to the single-spawn-call shape.
- `tests/integration/test_observability_capstone.py` — line ~183's `args={"prompt": …}` fan-out
  moves to one `args={"prompts": [...]}` call; span-count assertions updated accordingly.

## Acceptance Criteria

- [ ] `agent` accepts `prompts: list[str]`; a one-element list returns an aggregate with exactly one `## Subagent 1 — "<prompt>"` section.
- [ ] N prompts (N ≤ 6) spawn N children CONCURRENTLY from ONE tool call — genuine overlap proven (barrier), bounded by `subagent_max_parallel`; the parent sink sees exactly ONE `agent` ToolCallStarted/ToolResult.
- [ ] `prompts=[]` raises `ModelRetry` naming the fix; `len(prompts)==7` raises `ModelRetry` telling the model to consolidate; `len(prompts)==6` passes; both guards fire BEFORE any child spawns (spy: `agent.run` never called).
- [ ] Duplicate prompts are accepted (two identical prompts → two sections).
- [ ] The aggregate lists sections in prompt order, each headed by its own prompt; each child body is truncated to `subagent_result_max_bytes // len(prompts)` bytes (line-boundary snapped, via the shared `truncate()`).
- [ ] A child that raises still yields a section carrying an explicit failure note, and every sibling's report folds intact — no exception escapes the tool.
- [ ] The `agent` tool registers with a per-tool `retries` budget ≥ 2 (pinned by a registry test), still `ToolKind.READ_ONLY`, still granted to build/plan/code-reviewer and never to explore.
- [ ] No usage threading (parent gauge parent-only) and per-child `UsageLimits(request_limit=subagent_max_requests)` — existing invariants re-pinned under the new shape.
- [ ] No new `Settings` field and no `.env.example` change (`grep -n "MAX_FANOUT" src/decode/config/settings.py` empty; width cap is a module constant).
- [ ] `tests/unit/decode/tools/test_agent.py`, `tests/integration/test_subagents_capstone.py`, and `tests/integration/test_observability_capstone.py` all green under the new signature; `make ci` green.

## Out of scope

- The hardened model-facing tool description + per-prompt substance guard (104).
- Bad-report detection / one-shot child retry (106) — in this task an EMPTY child report folds as-is.
- The Synthesis Footer (107), the explore persona rewrite (105), capstone extension (108).
- Prompt-injection hardening and model-flake retry/backoff (feature non-goals, ADR-0017).

## Log
