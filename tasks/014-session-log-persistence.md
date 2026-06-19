---
id: 014-session-log-persistence
feature: m1-vanilla-agent
status: pending
---

# Session-log persistence (JSONL + resume)

## Scope
Append-only JSONL session log with replay ([ADR-0002 §9](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)), wired into the harness + a `decode --resume` flag.

## Acceptance criteria
- [ ] `context/session_log.py` writes a **header line 0** (`version, session_id, cwd, created_at` UTC) then appends each turn's `new_messages()` as **typed** JSONL to `.decode/sessions/<ts>_<uuid>.jsonl`.
- [ ] `decode --resume` rebuilds `message_history` from the latest session file.
- [ ] A truncated trailing line is tolerated on replay (skip, don't crash).
- [ ] `.decode/sessions/` is gitignored.

## Out of scope
- SQLite/Kitaru durability; session branching/forking; compaction entries (later milestones).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. JSONL header + typed entries validated against pi; confirm the Pydantic AI serialization API.
