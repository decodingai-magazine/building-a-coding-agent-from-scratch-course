---
id: 012-memory-files-injection
feature: m1-vanilla-agent
status: pending
---

# Memory: files layer + injection

## Scope
Read the project memory files and inject them into the agent's instructions ([ADR-0002 §8](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)).

## Acceptance criteria
- [ ] `memory/files.py` discovers `AGENTS.md` + `MEMORY.md` walking cwd→repo-root (cwd-most wins); `CLAUDE.md` skipped.
- [ ] `memory/service.py` `assemble_memory(cwd)` concatenates with provenance headers; `MEMORY.md` capped at 200 lines AND 25 KB with a visible truncation note when exceeded.
- [ ] Injected at prompt-build time via a dynamic `@agent.instructions` hook; verified via `result.all_messages()`.
- [ ] Missing files are skipped, not errors.

## Out of scope
- Memory write-back (task 013); `@`-import resolution; user-global `~/.decode/`.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Dual 200-line/25 KB cap validated against claude-code.
