---
id: 107
feature: subagent-fanout
status: pending
---

# Synthesis Footer on the aggregated agent result

Depends on: 103. Implements ADR-0017 §9.

## Scope

Append a harness-owned instruction line to the aggregated `agent` tool result telling the parent
model how to synthesize — just-in-time (costs nothing on turns with no Fan-out), lives in ONE
place, cannot be forgotten by a future persona author.

**`src/decode/tools/agent.py`**

- A module-constant footer, appended after the last section on EVERY `agent` tool result
  (one-element lists included). It instructs the parent to compile the N Subagent Reports into
  ONE answer: prose PLUS a text-based diagram of the structure it found — ASCII/box-drawing by
  default, Mermaid only when the structure is a genuine graph (the TUI is Rich in a terminal —
  Mermaid renders as raw source there).
- The footer is appended AFTER per-child truncation — it never eats any child's byte budget
  (total result ≈ 16 KB of reports + headings + footer).
- The parent personas (`build.md` / `plan.md` / `code-reviewer.md`) get NO synthesis wording —
  decision-locked; a guard test keeps it that way.

**Tests** (`tests/unit/decode/tools/test_agent.py`)

- Footer present on a 1-wide and an N-wide fold; positioned after the last section.
- Footer present even when a section carries a failure note.
- Children's per-child budget unchanged by the footer (byte assertions from 103 still hold on
  section bodies).
- Persona guard: no `agents/builtin/*.md` body contains the footer's synthesis instruction
  (stable-marker grep test).

## Acceptance Criteria

- [ ] Every `agent` tool result ends with the Synthesis Footer: compile into one answer, prose + text diagram, ASCII default / Mermaid only for genuine graphs.
- [ ] The footer never reduces any child's byte budget (appended post-truncation, pinned by test).
- [ ] No persona file carries synthesis/diagram instructions (guard test).
- [ ] `make ci` green.

## Out of scope

- Rendering diagrams in the TUI (the footer instructs the MODEL; Rich renders its text as-is).
- Making the footer conditional on width (always appended — decision locked).

## Log
