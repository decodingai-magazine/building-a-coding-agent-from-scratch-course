---
id: 105
feature: subagent-fanout
status: pending
---

# Explore persona rewrite: the tight structured Subagent Report contract

Depends on: 103 (references the shared fold budget). Implements ADR-0017 §8.

## Scope

Rewrite the BODY of `src/decode/agents/builtin/explore.md` so a child returns a **tight
structured summary** — deliberately compressed so N of them fit the parent's ~16 KB fold budget.

**`src/decode/agents/builtin/explore.md`**

- Frontmatter UNCHANGED: `name: explore`, `tools: read/glob/grep/lsp`, `subagent: true`,
  `mode: default` — every loader/narrowing test must stay green untouched.
- Body rewrite (final message IS the report — retained), the report must carry:
  - **the finding** — the direct answer to the question asked;
  - **the file:line evidence** — the specific files/functions/line ranges backing every claim;
    a summary with no file:line evidence is a hallucination tell (pairs with 106's
    zero-tool-call detection);
  - **the trace it followed** — the call/config chain across files, not one snippet in isolation.
- State the compression contract explicitly: the report is one of up to N sibling reports sharing
  the caller's budget — keep it tight (findings and evidence, no preamble, no methodology essay);
  a child's report may be byte-truncated, so lead with the finding.
- Do NOT put any parent-synthesis instruction here — that rides the Synthesis Footer (107),
  by decision.

**Tests**

- `tests/unit/decode/agents/` (wherever the persona/loader tests live) — existing frontmatter
  tests stay green byte-for-byte on the frontmatter. Add one stable content pin: the explore body
  names the three report parts (finding / file:line evidence / trace) — assert on stable
  structural markers, not full sentences (keep it un-brittle; SWE's call on the exact anchors).

## Acceptance Criteria

- [ ] `explore.md` frontmatter is byte-identical to before (tools exactly `read/glob/grep/lsp`, `subagent: true`); all existing loader/narrowing/persona tests pass unmodified.
- [ ] The body instructs the child to return the finding + file:line evidence + the trace followed, compressed, leading with the finding — pinned by one stable content test.
- [ ] The body contains NO parent-synthesis / diagram instruction (that is the footer's job — a grep-style test or reviewer check).
- [ ] `make ci` green.

## Out of scope

- Enforcing the contract at runtime (106 enforces the two BAD conditions; file:line presence is a persona-quality lever, not a hard validator — decision locked).
- Any change to build/plan/code-reviewer persona bodies.

## Log
