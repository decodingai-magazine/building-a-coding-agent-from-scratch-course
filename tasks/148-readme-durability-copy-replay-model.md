---
status: pending
feature: kitaru-replay-runtime
---

# README marketing copy: retire the pre-ADR-0019 durability phrasing (rows 111 / 135)

Tags: `docs`
Depends on: None
Blocks: —

Follow-up filed at the modal-remote-headless PA acceptance review (PR #65). Two README front-door
lines still describe the retired durable-execution model (kitaru 0.18 flows, killed by ADR-0019 —
"kitaru 0.22 removed durable execution", per 07_infra.md's own appendix). They pre-date tasks
141–146 and were out of every task's named scope, including 146's round-2 README fixes (which
corrected rows 159/259/311 only). Left standing, they promise capabilities the code no longer has.

## Scope

- `README.md:111` (Kitaru showcase card): "Every run recorded step by step in Kitaru — kill it,
  resume it, replay it with the model swapped". "kill it, resume it" is the retired
  checkpoint-resume story. Reword to the shipped record → replay model (e.g. "…— record it,
  replay it with the model swapped, fork the what-ifs"; SWE/author picks the final copy). Keep
  the Kitaru link + UTM parameters and the surrounding HTML structure byte-intact.
- `README.md:135` ("You'll Walk Away Knowing How To" bullet): "Add a runtime for durable
  execution, human-in-the-loop and replays when running parallel agents". Durable execution and
  HITL died with ADR-0019; replays and parallel attempts are real (ADR-0020). Reword around
  what shipped (e.g. "Add a runtime that records every run and replays it — including N
  parallel remote attempts at one task").
- Use glossary terms verbatim (Replay, Kitaru Session, Recording Seam — as fits marketing
  register). No other README line changes; rows 159/259/311 were already fixed in task 146.

## Acceptance Criteria

- [ ] `grep -n "resume it" README.md` and `grep -n "durable execution\|human-in-the-loop" README.md` return nothing (or only phrasing explicitly describing something retired/historical, which this README has no reason to carry).
- [ ] Row 111's replacement copy claims only shipped capabilities: recording, replay with overrides, forks/what-ifs (ADR-0019); the Kitaru `<a>` href + UTM params are unchanged.
- [ ] Row 135's replacement bullet claims only shipped capabilities: recording, replays, N parallel remote attempts (ADR-0020); no HITL, no durable-execution claim.
- [ ] The HTML table renders identically in structure (same `<td>`/`<p>` nesting); all README relative links still resolve.
- [ ] `make pre-commit` green (docs-only; no test change expected).

## User Stories

### Story: Prospective student reads the front page and finds only real features
1. Reader opens the README, sees the Kitaru card: it promises recording and replay/fork — nothing about resuming a killed run
2. Reader scrolls to "You'll Walk Away Knowing How To": the runtime bullet matches what 03_runtime.md and 08_evals_replays.md actually teach
3. Following any link (07_infra.md, Kitaru product page), nothing contradicts the claim they just read

---

Refs: ADR-0019 (durability retirement), ADR-0020 (parallel attempts), tasks/done/146-docs-remote-story-on-modal.md (rows 159/259/311 precedent)

## Log

### [PA] 2026-08-22 23:10 — Grooming

**Summary**
Two README front-door lines still market the retired durable-execution/HITL story; reword them to
the shipped record → replay → fork model, same class of fix as task 146's round-2 README edits.

**Key decisions**
- Docs-only, two lines; keep marketing register but claim only shipped capabilities.
- Preserve partner link + UTM params and HTML table structure byte-for-byte outside the copy.

**Dependencies**
- None.

**User stories**
- 1 story: the front-page reader journey.

Ready for implementation.
