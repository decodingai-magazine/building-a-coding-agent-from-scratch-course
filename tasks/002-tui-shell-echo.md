---
id: 002-tui-shell-echo
feature: m1-vanilla-agent
status: pending
---

# TUI shell (echo)

## Scope
The terminal UI layer per [ADR-0002 §6](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md): `patch_stdout()` + concurrent `prompt_async()` input with append-style Rich output. No agent yet — echoes input.

## Acceptance criteria
- [ ] `tui/app.py` runs a persistent input line via `prompt_async()` inside `patch_stdout()`; `Alt+Enter` and `Esc` keybindings registered; footer hint shows steer/follow-up/abort.
- [ ] `tui/render.py` holds pure `entities.events` → Rich renderables; tool calls rendered on completion (no flicker).
- [ ] A typed line echoes back via Rich; `Ctrl-D` / `/quit` exits.
- [ ] Render functions unit-tested on fake events; no `print()` in library code.

## Out of scope
- Wiring to the harness/agent (task 003+); in-place live regions / full-screen rendering.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Verify the prompt stays pinned under heavy streaming.
