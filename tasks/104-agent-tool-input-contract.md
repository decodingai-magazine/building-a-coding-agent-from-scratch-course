---
id: 104
feature: subagent-fanout
status: pending
---

# Agent tool input contract: hardened description + deterministic substance guard

Depends on: 103. Implements ADR-0017 §3.

## Scope

Enforce prompt quality on the way IN, with zero extra LLM calls: a hardened model-facing tool
description states the required shape, and a deterministic guard in the tool body raises
`ModelRetry` on an under-specified prompt so the parent model rewrites it before any child spawns.
Each prompt stays a **free-form string** — no rigid task/context/expected_output slots.

**`src/decode/tools/agent.py`**

- **Hardened tool description** (the function docstring — pydantic-ai lifts it into the tool
  schema). It must state, model-facing:
  - each prompt must carry: the QUESTION to answer, the SCOPE to search (directories, files, or
    patterns to start from), and WHAT THE REPORT MUST CONTAIN;
  - "for a broad question like 'explore the repo', give at least 3 DISTINCT angles";
  - a single focused question = a one-element list;
  - at most 6 prompts per call (matches 103's width cap).
- **Deterministic substance guard**, applied per prompt element, before any spawn, alongside
  103's structural guards. The exact heuristic is the SWE's call (e.g. a minimum-substance
  check — length/word floor plus presence of scope-ish content), but it MUST be: deterministic,
  cheap (no LLM, no I/O, no network), and its `ModelRetry` message must name WHICH prompt
  (by index) is under-specified and WHAT is missing (question / scope / expected report content).
- Guard failures never spawn a child and never consume semaphore slots.

**Tests**

- `tests/unit/decode/tools/test_agent.py` — NEW: an under-specified prompt (e.g. `"explore"`)
  raises `ModelRetry` naming the offending index and the missing part(s); a well-specified prompt
  passes; a mixed list (one good, one bad) is rejected as a whole with the bad index named;
  the guard is deterministic (same input, same outcome, twice); guard fires before spawn
  (spy: `agent.run` not called); the tool schema description carries the "3 DISTINCT angles"
  push and the per-prompt shape (assert on the registered tool's description / docstring).
- **Update every existing test whose scripted spawn prompts are now too terse to survive the
  guard** — the loop-driven prompts in `tests/unit/decode/tools/test_agent.py`, and the scripted
  parents in `tests/integration/test_subagents_capstone.py` /
  `test_observability_capstone.py` (e.g. `"explore area 0"`): rewrite them as well-formed
  prompts (question + scope + expected report). Terse prompts remain ONLY inside the
  guard-specific tests.

## Acceptance Criteria

- [ ] The registered `agent` tool's model-facing description states the per-prompt shape (question + scope + report content), the "at least 3 DISTINCT angles for broad questions" push, the one-element-list case, and the width cap of 6.
- [ ] An under-specified prompt raises `ModelRetry` whose message names the offending prompt index AND what is missing; no child is spawned (spy on `agent.run` proves it).
- [ ] A well-specified list of prompts passes the guard and spawns normally.
- [ ] The guard is deterministic and makes no LLM/network/file call (unit-testable in isolation, same result on repeat).
- [ ] All previously-green tests pass with their scripted prompts upgraded to guard-passing form; `make ci` green.

## Out of scope

- Prompt-injection hardening (feature non-goal, ADR-0017).
- Deduping duplicate prompts (allowed by design — decision locked).
- Output-side validation (106); persona wording (105); footer (107).

## Log
