---
id: 011-tools-askuser-deferred
feature: m1-vanilla-agent
status: pending
---

# Tools: AskUser (deferred)

## Scope
The one blocking tool — the model asks the human a question, routed through the same deferred-pause path as approvals ([ADR-0002 §2](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)).

## Acceptance criteria
- [ ] `tools/askuser.py` `ask_user(question)` produces a deferred request that pauses the turn and surfaces the question in the TUI.
- [ ] The user's typed answer becomes the tool result; only one AskUser in flight at a time.
- [ ] Errors cleanly when no TUI is attached (headless-safe).
- [ ] Tested with a fake resolver (no real terminal).

## Out of scope
- Structured multiple-choice questionnaires.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Uses the deferred mechanism (not an inline Future) for M7 HITL transport.
