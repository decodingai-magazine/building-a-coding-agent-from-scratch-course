---
id: 045-compact-tui-command
feature: context-compaction
status: pending
---

# Manual `/compact` TUI command (forces full compaction)

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §7. A reserved slash command that
forces a **full** compaction now, wired like `/quit`, idle-only. An explicit user request compacts
regardless of the window-relative thresholds or `compaction_enabled`.
Depends on: 044 · Blocks: —

## Scope

In `src/decode/tui/app.py`:

- Add `_COMPACT_COMMAND = "/compact"` and a pure `is_compact_command(line) -> bool` (mirror
  `is_quit_command`).
- Intercept `/compact` **among the reserved commands** (with `/quit` / `/agent` / `/mode`, before
  `parse_skill_command`), so a `compact` skill can't shadow it.
- Behaviour:
  - `runner.phase is Phase.IDLE`: `await handler.compact()`. `True` → handler already emitted
    `ContextCompacted`; `False` → `emit_line("Decode - nothing to compact yet.")`.
  - Busy → `emit_line("Decode - busy; try /compact again once the turn finishes.")`, continue.
- Update `footer_hint` to list `/compact` (kept pure/unit-tested) and the startup hint if it enumerates
  commands.

## Acceptance criteria

- [ ] `is_compact_command("/compact")` / `"  /compact  "` are `True`; `"/compactx"`, `"compact"`,
      `"/quit"` are `False` (pure unit test).
- [ ] Typing `/compact` while **idle** calls `handler.compact()`; on a FunctionModel-seeded over-budget
      history, history becomes `[summary, *tail]` and a `ContextCompacted` line renders (no network).
- [ ] `/compact` with nothing to compact renders `Decode - nothing to compact yet.`, history unchanged.
- [ ] `/compact` while busy renders the busy line, no history mutation / no turn started.
- [ ] `/compact` matched before the skill branch (precedence test).
- [ ] `footer_hint` lists `/compact`; footer unit test updated.
- [ ] `make ci` green, 0 warnings, no network.

## Out of scope
- The auto cascade + `compact()`/`_microcompact()` (044); the gauge (047).
- A manual microcompaction command (auto-only).

## Log
