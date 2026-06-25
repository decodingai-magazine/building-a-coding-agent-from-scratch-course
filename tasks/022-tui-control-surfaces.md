---
id: 022-tui-control-surfaces
feature: permission-system-agents-catalog
status: pending
---

# TUI control surfaces: /agent, /mode, --mode, Shift+Tab cycle, footer

Implements [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md) §9.
Depends on: 018, 020, 021 · Blocks: —

## Scope

The human-driven control surfaces for switching agent + mode, plus the footer showing the active
state. Reuse the single input surface — never open a second `prompt_async()` (the M1 Decision Channel
invariant holds).

- **`cli.py`** — add `--mode NAME` (default = the selected agent's default mode; validate against the
  four mode names; unknown → one friendly stderr line + non-zero exit). Pass to `run_app`.
- **`tui/app.py` slash commands** — alongside `is_quit_command`, add pure parsers/handlers for
  `/agent <name>` and `/mode <name>` in the input loop: `/agent build` runs the task-020 selection
  helper (sets `deps.active_agent`, resets mode, loads agent rules) and renders a confirmation;
  `/mode plan` calls `gate.set_mode(...)` and renders a confirmation. Unknown name/mode → a friendly
  inline line (not a crash). Keep the decidable parsers pure + unit-tested (mirroring `is_quit_command`).
- **Shift+Tab keybind** — in `_build_key_bindings`, add `@bindings.add("s-tab")` (verified key id)
  that cycles the gate mode `default → edit → plan → bypass → default` and renders the new mode. No
  collision with the existing Alt+Enter / Esc bindings; route through the existing input mechanism.
- **Footer** — extend `footer_hint()` (kept a pure string) to include the active agent + mode, e.g.
  `agent:build mode:default | Enter steer | Alt+Enter follow-up | Esc abort | Shift+Tab mode | /agent
  /mode /quit`. The bottom toolbar reads the live agent/mode each render.
- **Confirmations** — switching agent or mode renders a single confirmation line through the existing
  event/render path (no second render surface).

## Acceptance criteria

- [ ] `decode --mode plan` starts in plan mode; `decode --mode nope` prints one friendly stderr line
      and exits non-zero. Driven through the CLI.
- [ ] `/agent plan` mid-session switches the active agent (prompt + tool set + mode all change on the
      next turn) and renders a confirmation; `/agent nope` renders a friendly inline error and keeps
      the session alive. Driven through the real `run_app`.
- [ ] `/mode bypass` mid-session sets the gate to bypass and renders a confirmation; the next mutating
      tool runs without a prompt. `/mode nope` → friendly inline error. Driven through `run_app`.
- [ ] Shift+Tab cycles `default → edit → plan → bypass → default`, each press rendering the new mode;
      the cycle is unit-testable as a pure next-mode function and the keybind is exercised via the
      piped-terminal `run_app` test. No collision with Alt+Enter / Esc.
- [ ] `footer_hint()` (pure) includes the active agent + mode and is unit-tested; the bottom toolbar
      reflects a mode change after Shift+Tab / `/mode`.
- [ ] **Working looks like:** the footer shows `agent:build mode:default`; Shift+Tab flips it to
      `mode:edit` and a `write` then runs without a prompt; `/mode plan` flips to `mode:plan` and a
      `write` is denied.
- [ ] `make ci` green, 0 warnings; no second `prompt_async` introduced (the single-input-surface
      invariant from task 005 holds).

## Out of scope
- Subagent spawning; per-user/global rule tiers; richer plan-mode UX.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §9. The human-driven counterpart to task 021's model-driven plan-mode tools.
Mode cycle includes `edit`. Single-input-surface invariant from M1 must hold.
