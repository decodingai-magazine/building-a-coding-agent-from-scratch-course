---
id: 108
feature: subagent-fanout
status: pending
---

# Capstone: resilient parallel fan-out end-to-end + docs verification

Depends on: 103, 104, 105, 106, 107. Proves ADR-0017 composed, through the full real stack.

## Scope

Extend `tests/integration/test_subagents_capstone.py` (same style: real `build_agent` + `Runner`
+ `AgentTurnHandler` + gate + `render_event`, one scripted `FunctionModel` driving parent AND
children; no network except the skipif-gated live smoke).

**New composed scenarios**

- **The resilience matrix in one turn** — one `agent` call, 4 well-formed prompts; children
  scripted as: A good (real read-only tool + report), B empty-first → nudged retry → good,
  C text-only (zero tool calls) → retry → still text-only → failure note, D good. Assert: ONE
  `agent` ToolCallStarted on the sink; the aggregate carries 4 sections in prompt order; B's
  section = its retry report (B spawned exactly twice); C's section = the failure note; per-child
  budget division holds (patched small setting); Synthesis Footer present after the sections;
  zero `PermissionRequested`; children silent-until-done; parent usage gauge excludes children;
  session log + `--resume` replay carry the single spawn + aggregate only.
- **Width-cap round-trip through the loop** — parent first emits 7 prompts → the `ModelRetry` nag
  reaches the model → the scripted model re-emits 3 consolidated prompts → the turn completes
  green (also proves 103's raised per-tool retries budget end-to-end).
- **Substance-guard round-trip** — parent first emits one under-specified prompt → nag names the
  index + missing parts → model rewrites → children spawn.

**Live Gemini smoke** (existing `test_live_gemini_fanout_smoke`, still skipif-gated on
`GEMINI_API_KEY`) — updated: the prompt asks for a broad multi-angle exploration of named files;
assert presence-only: ≥1 `agent` call whose args carry a `prompts` list, an aggregated result
containing `## Subagent` heading(s) and the footer, no permission prompt.

**Docs verification (this feature's drift check — read, verify, fix if drifted)**

- `docs/glossary.md`: **Fan-out**, **Subagent Report**, **Synthesis Footer** rows + the amended
  **Agent tool** / **Subagent** rows match shipped behavior (written in the grooming commit —
  verify, don't duplicate).
- `docs/adr/0013-explore-subagents.md` header carries the dated amendment pointing at ADR-0017;
  `docs/adr/0017-*.md` present and matches what shipped.
- `.claude/skills/manual-e2e-qa/SKILL.md` subagents row (updated in 103) matches final shipped
  behavior — one `agent(prompts=[…])` call, width cap, budget split, retry-once, failure notes,
  footer.

## Acceptance Criteria

- [ ] The resilience-matrix test passes: 4 sections in prompt order, retried child folded, failed child noted, budget split, footer present, one tool call on the sink, no prompts, silent children, parent-only usage, resume-clean log.
- [ ] The 7-prompt width-cap round-trip completes green through the real loop (nag → consolidate → run), proving the raised tool-retries budget.
- [ ] The substance-guard round-trip completes green through the real loop.
- [ ] The live Gemini smoke (skipif-gated) asserts the new shape presence-only and stays green when the key is set.
- [ ] Glossary rows, ADR-0013 amendment header, ADR-0017, and the manual-e2e-qa row all match shipped behavior (verified by reading; fixed here if drifted).
- [ ] `make ci` green.

## Out of scope

- New runtime behavior — this task adds proof and verifies docs, not features.
- A deployed-stack headless subagent replay proof (ADR-0013 open seam, unchanged).

## Log
