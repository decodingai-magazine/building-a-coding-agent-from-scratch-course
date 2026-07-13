---
id: 106
feature: subagent-fanout
status: pending
---

# Subagent Report validation: bad-report detection + exactly one child retry

Depends on: 103 (per-child runner + sections), 105 (the report contract the nudge points back to).
Implements ADR-0017 §7.

## Scope

Validate each child's report on the way OUT; retry a bad child exactly ONCE with a nudge; fold an
explicit failure note if the retry is also bad. Never infinite retry — a broken child must not eat
the run's budget.

**`src/decode/tools/agent.py`**

- **Bad-report predicate** (deterministic, framework-verified): a child report is BAD iff
  - (i) its output text is empty/whitespace-only, OR
  - (ii) the child made ZERO tool calls — no `ToolCallPart` in any `ModelResponse` of
    `result.all_messages()` (verified: `AgentRunResult.all_messages()`, pydantic-ai 1.95
    `run.py:461`) — it answered from model memory instead of reading the code.
  - The defensive `DeferredToolRequests` output also classifies as BAD (it enters this machinery
    instead of short-circuiting to a note, as it did after 103).
- **One retry**: re-spawn that child once — same prompt + an appended nudge (a module constant;
  it must say what was wrong, e.g. "Your previous report was unusable: it was empty or cited no
  code you actually read. Use your tools to read the code, then report the finding with file:line
  evidence."). Fresh child deps, fresh semaphore acquisition, same `UsageLimits` — exactly like
  any spawn.
- **Second bad → failure note**: fold an explicit note for that child (e.g. "The subagent
  returned no usable report.") into its section. Exactly 2 attempts max, ever.
- A bad child + retry never delays or corrupts sibling sections (each child's
  attempt/retry/give-up cycle is private to its own gather slot); section order stays prompt
  order. The retry's report is budget-truncated exactly like a first-attempt report.
- Log each retry and each give-up at `logger.warning` (names/index only — never report bodies at
  warning level).

**Tests** (`tests/unit/decode/tools/test_agent.py`, scripted `FunctionModel` children)

- Empty-first / good-second child → exactly 2 spawns for that prompt, the good report folds.
- Empty twice → exactly 2 spawns, its section carries the failure note verbatim.
- Non-empty but ZERO-tool-call child (pure text answer) → detected as bad and retried, even
  though text is present.
- A child that calls a real read-only tool then reports → NOT bad, never retried (spawn count 1).
- The retry prompt = original prompt + the nudge (assert both present on the second spawn).
- Sibling isolation: in a 3-wide Fan-out with one twice-bad child, the other two sections carry
  their intact reports.
- The retry's output respects the per-child byte budget.

## Acceptance Criteria

- [ ] An empty/whitespace-only child report triggers exactly one retry with the nudge appended; a good retry report folds into that child's section.
- [ ] A non-empty report from a child that made zero tool calls (checked via `result.all_messages()`) is classified BAD and retried — pinned by a test whose child answers text-only.
- [ ] A second bad attempt folds the explicit failure note; total spawns for that prompt == 2, never 3 (spy-counted).
- [ ] A good child is never retried (spawn count == 1).
- [ ] Sibling reports are unaffected by another child's retry/failure (order + content pinned).
- [ ] A `DeferredToolRequests` child output routes through the same bad-report machinery.
- [ ] `make ci` green.

## Out of scope

- Model-flake retry/backoff (transport-level) — feature non-goal, ADR-0017.
- Validating file:line presence in a syntactically fine report (persona-quality lever, 105).
- Any change to the input guards (104).

## Log
