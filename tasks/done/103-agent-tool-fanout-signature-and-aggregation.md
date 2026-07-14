---
id: 103
feature: subagent-fanout
status: done
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

- [x] `agent` accepts `prompts: list[str]`; a one-element list returns an aggregate with exactly one `## Subagent 1 — "<prompt>"` section.
- [x] N prompts (N ≤ 6) spawn N children CONCURRENTLY from ONE tool call — genuine overlap proven (barrier), bounded by `subagent_max_parallel`; the parent sink sees exactly ONE `agent` ToolCallStarted/ToolResult.
- [x] `prompts=[]` raises `ModelRetry` naming the fix; `len(prompts)==7` raises `ModelRetry` telling the model to consolidate; `len(prompts)==6` passes; both guards fire BEFORE any child spawns (spy: `agent.run` never called).
- [x] Duplicate prompts are accepted (two identical prompts → two sections).
- [x] The aggregate lists sections in prompt order, each headed by its own prompt; each child body is truncated to `subagent_result_max_bytes // len(prompts)` bytes (line-boundary snapped, via the shared `truncate()`).
- [x] A child that raises still yields a section carrying an explicit failure note, and every sibling's report folds intact — no exception escapes the tool.
- [x] The `agent` tool registers with a per-tool `retries` budget ≥ 2 (pinned by a registry test), still `ToolKind.READ_ONLY`, still granted to build/plan/code-reviewer and never to explore.
- [x] No usage threading (parent gauge parent-only) and per-child `UsageLimits(request_limit=subagent_max_requests)` — existing invariants re-pinned under the new shape.
- [x] No new `Settings` field and no `.env.example` change (`grep -n "MAX_FANOUT" src/decode/config/settings.py` empty; width cap is a module constant).
- [x] `tests/unit/decode/tools/test_agent.py`, `tests/integration/test_subagents_capstone.py`, and `tests/integration/test_observability_capstone.py` all green under the new signature; `make ci` green.

## Out of scope

- The hardened model-facing tool description + per-prompt substance guard (104).
- Bad-report detection / one-shot child retry (106) — in this task an EMPTY child report folds as-is.
- The Synthesis Footer (107), the explore persona rewrite (105), capstone extension (108).
- Prompt-injection hardening and model-flake retry/backoff (feature non-goals, ADR-0017).

## Log

### [SWE] 2026-07-13 — Implementation

**Files modified**
- `src/decode/tools/agent.py` — BREAKING: `agent(ctx, prompts: list[str]) -> str`. Structural guards
  (`[]` → `ModelRetry`; `> MAX_FANOUT_PROMPTS` (module constant, 6) → `ModelRetry` "Consolidate…"),
  harness `asyncio.gather` over a new `_spawn_child(ctx, prompt, *, max_bytes)` helper (the old
  fresh-narrowed-deps construction moved in unchanged; semaphore acquired per child ATTEMPT), per-child
  failure isolation (`except Exception` → `logger.warning(exc_info=True)` + a failure note as that
  child's section), shared byte budget (`subagent_result_max_bytes // len(prompts)`), labelled
  concatenation `## Subagent {i} — "{prompt}"`. New `AGENT_TOOL_RETRIES = 3`.
- `src/decode/tools/registry.py` — `ToolSpec.retries: int | None = None`; `register_tools` passes it to
  `agent.tool(…, retries=…)`; the `agent` spec carries `retries=AGENT_TOOL_RETRIES` (3 ≥ floor 2), still
  `ToolKind.READ_ONLY`. Every other tool keeps the Agent default (`None`).
- `src/decode/config/settings.py` — comment-only: subagent block anchors ADR-0017 §2,6 and spells out
  concurrency-ceiling vs width-cap vs shared-fold-budget. No new field; `.env.example` untouched.
- `.claude/skills/manual-e2e-qa/SKILL.md` — subagents row: ONE `agent(prompts=[…])` call, harness gather,
  width cap 6 + `ModelRetry` nags, shared per-child budget, labelled aggregate, failure note.
- `tests/unit/decode/tools/test_agent.py` — list-shaped throughout; NEW: empty→ModelRetry (spawn spy never
  called), 7→"Consolidate", cap==6 passes, 1 section / N sections in prompt order (echo agent proves the
  pairing), duplicates not deduped, per-child budget `//N` + single child keeps the whole budget, one child
  raising → failure note + siblings intact + logged, deferred-note as a section, semaphore bounds a 6-wide
  fan-out from ONE call, registry retries pinned on spec AND on the registered Tool (`max_retries`).
- `tests/integration/test_subagents_capstone.py` — `_fan_out(n)` is ONE `ToolCallPart(args={"prompts": […]})`;
  barrier test now proves the HARNESS guarantees the overlap (`len(sink.tool_calls()) == 1`); fold assertions
  parse sections; truncation test proves the SHARED (divided) budget; ephemeral/resume test = one spawn call.
- `tests/integration/test_observability_capstone.py` — its `_fan_out` moved to the single-call shape (span
  assertions are presence/nesting-based, so they hold unchanged).

**Tests**
- Unit: 1494 passing, 0 failing (`make unit-tests`); `tests/unit/decode/tools/test_agent.py` 27 passing.
- Integration: 112 passing, 2 skipped (live Gemini / live Opik smokes — no key in this worktree).
- `make ci`: 1606 passed, 2 skipped, lint + format clean.

**Acceptance criteria** — all 10 checked above. Notable verifications:
- ONE tool call, N concurrent children: `test_parallel_fanout_overlaps_and_is_bounded_by_subagent_max_parallel`
  (barrier trips only with `cap` children simultaneously inside; `len(sink.tool_calls()) == 1`).
- Guards pre-spawn: `test_empty_prompts_raises_model_retry_before_any_child_spawns`,
  `test_more_than_six_prompts_raises_model_retry_telling_the_model_to_consolidate` (`_require_main_agent`
  spy asserted never called).
- Retry budget: `test_agent_registers_with_a_raised_retry_budget` pins `TOOL_SPECS` **and**
  `built._function_toolset.tools["agent"].max_retries == 3`.
- No new setting: `grep -n "MAX_FANOUT" src/decode/config/settings.py` → empty; `git diff .env.example` → empty.

**Evidence**

```
$ make ci
================= 1606 passed, 2 skipped in 397.99s (0:06:37) ==================

$ uv run pytest tests/unit/decode/tools/test_agent.py -q
27 passed in 2.54s

$ make lint-check && make format-check
All checks passed! / 181 files already formatted
```

Live e2e (real Gemini, real Explore children reading this repo, permission-free, one turn):

```
[tool call] agent args={"prompts":["Explain how the permission gate decides allow/ask/deny.",
                                   "Describe how tool output is truncated.",
                                   "Detail how the agent tool spawns subagents."]}
[tool result] agent ok=True
## Subagent 1 — "Explain how the permission gate decides allow/ask/deny."
The permission gate in `src/decode/permissions/gate.py` decides … (lines 25-108) …
## Subagent 2 — "Describe how tool output is truncated."
Tool output is truncated by the `decode.tools.truncate` module … 2000 lines or 50 KB …
## Subagent 3 — "Detail how the agent tool spawns subagents."
The `agent` tool, located at `src/decode/tools/agent.py`, spawns subagents by … `asyncio.gather()` …
=== agent tool calls: 1 ===
```

**Notes**
- Failure-note wording is deliberately terse ("This subagent failed before producing a report.") — task 106
  owns the bad-report machinery and will re-word/extend it; the exception detail goes to the WARNING log, not
  the model's context.
- Out of scope and NOT built (per the task): hardened tool description + substance guard (104), explore persona
  rewrite (105), bad-report detection / one-shot retry (106) — an empty child report folds as-is today —, and
  the Synthesis Footer (107).
- The live-Gemini smoke in the subagents capstone still SKIPS here (no `GEMINI_API_KEY` in this worktree's
  env); the e2e above ran it manually with the key from the main checkout's `.env`.

### [Tester] 2026-07-13 23:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` → 181 files already formatted; `make lint-check` →
  All checks passed; `make pre-commit` → 1494 passed in 93.80s)
- Unit tests: 1494 passed / 0 failed (`make pre-commit`, which runs the unit suite)
- Integration tests: 112 passed / 2 skipped (`make integration-tests` → `112 passed, 2 skipped in 310.79s`;
  the 2 skips are the pre-existing live-Gemini/live-Opik smokes, no key in this worktree — same as SWE
  reported)
- Warnings: 0 (project runs `filterwarnings=["error"]`; any warning would be a failure, and every suite
  above is green)

**E2E adversarial pass**

Ran an independent script (not the SWE's own tests) exercising the REAL `agent()` / `_spawn_child()`
functions directly with hand-rolled stub main-agents, from a hermetic environment (no live model needed —
same technique the unit suite already uses for direct-call tests).

- Happy path: real 3-child fan-out through a stub main agent → one aggregate, 3 `## Subagent i — "…"`
  sections in prompt order, each carrying its own report. Also independently verified duplicate prompts
  (`["same prompt", "same prompt"]`) → 2 independent sections. PASS.
- Break path 1 (guards fire BEFORE any spawn — spy the spawn seam): patched `_require_main_agent` to a
  spy that counts calls; `agent(ctx, [])` → `ModelRetry` raised, spy call count **0**; `agent(ctx,
  7 prompts)` → `ModelRetry` ("Consolidate your angles into at most 6 prompts…"), spy call count **0**.
  PASS — guard genuinely fires pre-spawn, not spawn-then-check.
- Break path 2 (concurrency is REAL, not sequential awaits dressed as gather): 4 children each sleeping
  0.3s under `subagent_max_parallel=4` → observed wall-clock elapsed **0.31s** (not ~1.2s a sequential
  run would take) and observed peak-concurrent-live counter reached **4**. Then a 6-wide fan-out under
  `subagent_max_parallel=4`, each sleeping 0.2s → observed elapsed **0.41s** (two waves of ~0.2s, not
  six) and peak concurrency **exactly 4**, never exceeded. PASS — the semaphore genuinely bounds a
  6-wide fan-out to 4-at-a-time, and the gather is genuinely concurrent, independently timed.
- Break path 3 (one raising child leaves siblings intact, no exception escapes): a stub main agent
  raised `UsageLimitExceeded` for the middle of 3 prompts → returned aggregate had all 3 sections, prompt
  2's section read "This subagent failed before producing a report.", prompts 1 and 3's sections carried
  their real reports intact, and no exception propagated out of `agent()`. PASS. Additionally probed
  `asyncio.CancelledError` (a `BaseException`, not caught by the `except Exception` in `_spawn_child`) —
  as expected from reading the code, `CancelledError` propagates OUT of `agent()` rather than folding into
  that child's section. This is correct, standard asyncio practice (blindly catching `CancelledError`
  would break cooperative cancellation) and is consistent with the ADR's explicit non-goal of
  "transport-level model-flake retry/backoff" — noting it here per the brief, not a defect.
- Break path 4 (byte-budget integer-division edge, incl. a degenerate zero-per-child budget): patched
  `subagent_result_max_bytes=100` with 3 prompts (`100 // 3 = 33` floor) → each section body ≤ 33 bytes
  in practice (observed 25 bytes each, since `truncate()` snaps to a line boundary at or under the cap).
  Then the deliberately hostile case: `subagent_result_max_bytes=5` with 6 prompts → `5 // 6 == 0`. The
  tool did NOT crash and did NOT produce empty sections: `truncate()`'s "even the first line overflows,
  keep that one whole line regardless" floor (`src/decode/tools/truncate.py:91-93`) means a 0-byte
  per-child budget still yields a non-empty first line per child (observed 12 bytes each) — a graceful
  floor, not a crash or silent data loss. PASS, with a note (see Other issues below).

**Acceptance criteria**
- [x] PASS — `agent` accepts `prompts: list[str]`; one-element list → one `## Subagent 1 — "<prompt>"`
      section — `tests/unit/decode/tools/test_agent.py::test_agent_takes_ctx_and_prompts_only`,
      `::test_one_prompt_folds_one_labelled_section` pass; independently reproduced in the adversarial
      script (happy path above).
- [x] PASS — N ≤ 6 prompts spawn N children concurrently from ONE tool call, genuine overlap, bounded by
      `subagent_max_parallel`, parent sink sees exactly ONE `agent` ToolCallStarted/ToolResult —
      `tests/integration/test_subagents_capstone.py::test_parallel_fanout_overlaps_and_is_bounded_by_subagent_max_parallel`
      passes (`len(sink.tool_calls()) == 1`, `concurrency["peak"] == cap`); independently re-timed in
      break path 2 above (4-of-4 and 4-of-6 waves observed directly).
- [x] PASS — `prompts=[]` / `len==7` raise `ModelRetry` naming the fix / consolidate, `len==6` passes,
      both guards fire before any spawn — `test_empty_prompts_raises_model_retry_before_any_child_spawns`,
      `test_more_than_six_prompts_raises_model_retry_telling_the_model_to_consolidate`,
      `test_the_width_cap_is_six_and_six_prompts_pass_the_guard` pass; independently spied in break path 1
      above (spawn call count 0 in both failure cases).
- [x] PASS — duplicate prompts accepted, two sections — `test_duplicate_prompts_are_not_deduped` passes;
      independently reproduced (happy path above).
- [x] PASS — sections in prompt order, each headed by its own prompt, each body truncated to
      `subagent_result_max_bytes // len(prompts)` via shared `truncate()` —
      `test_each_child_report_is_truncated_to_the_shared_byte_budget`,
      `test_a_single_child_still_gets_the_whole_byte_budget`,
      `test_n_prompts_fold_n_sections_in_prompt_order_each_headed_by_its_own_prompt` pass;
      `src/decode/tools/agent.py:144` (`child_max_bytes = settings.subagent_result_max_bytes //
      len(prompts)`) and `:216-220` (`truncate(..., max_bytes=max_bytes)`); independently confirmed in
      break path 4 above, including the integer-division-to-zero edge.
- [x] PASS — a raising child yields a failure-note section, siblings fold intact, no exception escapes —
      `test_a_child_that_raises_gets_a_failure_note_and_its_siblings_still_fold` passes; independently
      reproduced in break path 3 above with a fresh stub (not the SWE's test fixture).
- [x] PASS — `agent` registers with `retries` ≥ 2 (pinned by a registry test), still `ToolKind.READ_ONLY`,
      granted to build/plan/code-reviewer, never to explore —
      `test_agent_registers_with_a_raised_retry_budget` passes (`retries=3`, pins both `TOOL_SPECS` and
      `built._function_toolset.tools["agent"].max_retries`); independently confirmed via
      `TOOL_SPECS` dump (`agent ToolKind.READ_ONLY 3`, every other tool `None`) and grepping
      `src/decode/agents/builtin/{build,plan,code-reviewer}.md` (all list `agent`) vs `explore.md`
      (does not).
- [x] PASS — no usage threading, per-child `UsageLimits(request_limit=subagent_max_requests)` —
      `test_child_run_does_not_thread_parent_usage` passes;
      `src/decode/tools/agent.py:200-204` calls `.run(prompt, deps=child_deps, usage_limits=...)` with no
      `usage=` kwarg.
- [x] PASS — no new `Settings` field, no `.env.example` change — `grep -n "MAX_FANOUT"
      src/decode/config/settings.py` → empty; `git diff --stat -- .env.example` → empty; the only
      `settings.py` diff is a comment-only ADR-anchor update.
- [x] PASS — the three test files green under the new signature, `make ci` green — independently ran
      `make format-check`, `make lint-check`, `make pre-commit` (unit: 1494 passed), `make
      integration-tests` (112 passed, 2 skipped) — all green, matching the SWE's reported counts exactly.

**Evidence**
```
$ make pre-commit
======================= 1494 passed in 93.80s (0:01:33) ========================

$ make integration-tests
================== 112 passed, 2 skipped in 310.79s (0:05:10) ==================

$ uv run pytest tests/integration/test_subagents_capstone.py tests/integration/test_observability_capstone.py \
    tests/unit/decode/tools/test_agent.py tests/unit/decode/tools/test_registry.py -v
======================== 45 passed, 2 skipped in 4.17s =========================

$ grep -n "MAX_FANOUT" src/decode/config/settings.py
(empty)

$ git diff --stat -- .env.example
(empty)

$ uv run python -c "from decode.tools.registry import TOOL_SPECS; [print(s.name, s.kind, s.retries) for s in TOOL_SPECS]"
agent ToolKind.READ_ONLY 3
(every other tool: None)
```

**Other issues found**
- Not a blocker, but worth a follow-up thought: at the current default (`subagent_result_max_bytes=16000`,
  width cap 6), `16000 // 6 = 2666` — nowhere near the degenerate zero-budget edge probed above. The
  zero-budget case only bites if an operator hand-tunes `subagent_result_max_bytes` far below the width
  cap, which is not a realistic default-config scenario; `truncate()`'s existing "always keep at least one
  whole line" floor already prevents silent data loss / a crash in that case, so no fix is required here —
  flagging only as a "PASS with note" for anyone tuning that setting aggressively in the future.
- `git diff` is clean of unrelated files; the code-review plugin (`code-review@claude-plugins-official`) is
  enabled repo-wide, but its slash command operates on an open GitHub PR (`gh pr view`/`gh pr diff`) — this
  branch has no PR yet (uncommitted local work, file-mode tracker), so it was not invokable here; the
  equivalent manual checklist (CLAUDE.md/AGENTS.md compliance, bug scan, git-diff review) was applied by
  hand above and found no additional issues.
- The failure-note wording ("This subagent failed before producing a report.") is explicitly out-of-scope
  polish per the task (106 owns bad-report machinery) — confirmed genuinely not built ahead of schedule:
  `grep -n "Synthesis Footer\|substance guard\|all_messages\|zero tool calls\|re-spawn\|nudge"
  src/decode/tools/agent.py` is empty, and `src/decode/agents/builtin/explore.md` (the persona 105 would
  rewrite) is untouched in this diff.

**VERDICT: PASS**

### [PA] 2026-07-14 — Acceptance Review

**VERDICT: ACCEPT**

Reviewed as part of the subagent-fanout feature acceptance (PR #33). All 10 AC re-verified against shipped code: `agent(ctx, prompts: list[str])` at `src/decode/tools/agent.py:297`, guards pre-spawn (`:319`, `:323`, `MAX_FANOUT_PROMPTS=6` at `:59`), harness `asyncio.gather` (`:338`), shared budget `subagent_result_max_bytes // len(prompts)` (`:335`), labelled fold (`:349-352`), failure-note isolation (`:404-406`), `AGENT_TOOL_RETRIES=3` wired through `registry.py:120`. No new setting. User satisfaction verified at feature level — see the feature verdict in the 108 log entry.
