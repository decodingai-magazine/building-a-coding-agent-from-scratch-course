---
id: 015-e2e-glue
feature: m1-vanilla-agent
status: pending
---

# End-to-end glue + Testing E2E docs

## Scope
Tie the milestone together: fill the `AGENTS.md` "Testing E2E" section and add a full-stack integration test.

## Acceptance criteria
- [ ] `AGENTS.md` "Testing E2E" documents the concrete `decode` launch + what "working" looks like for each surface.
- [ ] An integration test under `tests/integration/` drives the full stack with `TestModel`, exercising: a read, a gated write (approve **and** deny paths), a task update, an AskUser, a web fetch, a session-log replay, and the on-exit memory line.
- [ ] `make ci` green.

## Out of scope
- Real-Gemini calls in CI (manual e2e only).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Final integration task for the feature.
