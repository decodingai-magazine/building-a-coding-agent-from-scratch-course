---
id: 022-tui-control-surfaces
feature: permission-system-agents-catalog
status: done
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

- [x] `decode --mode plan` starts in plan mode; `decode --mode nope` prints one friendly stderr line
      and exits non-zero. Driven through the CLI.
- [x] `/agent plan` mid-session switches the active agent (prompt + tool set + mode all change on the
      next turn) and renders a confirmation; `/agent nope` renders a friendly inline error and keeps
      the session alive. Driven through the real `run_app`.
- [x] `/mode bypass` mid-session sets the gate to bypass and renders a confirmation; the next mutating
      tool runs without a prompt. `/mode nope` → friendly inline error. Driven through `run_app`.
- [x] Shift+Tab cycles `default → edit → plan → bypass → default`, each press rendering the new mode;
      the cycle is unit-testable as a pure next-mode function and the keybind is exercised via the
      piped-terminal `run_app` test. No collision with Alt+Enter / Esc.
- [x] `footer_hint()` (pure) includes the active agent + mode and is unit-tested; the bottom toolbar
      reflects a mode change after Shift+Tab / `/mode`.
- [x] **Working looks like:** the footer shows `agent:build mode:default`; Shift+Tab flips it to
      `mode:edit` and a `write` then runs without a prompt; `/mode plan` flips to `mode:plan` and a
      `write` is denied.
- [x] `make ci` green, 0 warnings; no second `prompt_async` introduced (the single-input-surface
      invariant from task 005 holds).

## Out of scope
- Subagent spawning; per-user/global rule tiers; richer plan-mode UX.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §9. The human-driven counterpart to task 021's model-driven plan-mode tools.
Mode cycle includes `edit`. Single-input-surface invariant from M1 must hold.

### [SWE] 2026-06-25 14:30 — Implementation

**Files modified**
- `src/decode/cli.py` — added `--mode NAME` Click option; validates against the four mode names
  (unknown → friendly stderr line + non-zero exit, after the no-key/agent guards), passes `mode` to
  `run_app`.
- `src/decode/tui/app.py` — new pure parsers/helpers (`parse_agent_command`, `parse_mode_command`,
  `parse_mode_name`, `next_mode`, `mode_switch_confirmation`, `agent_switch_confirmation`); extended
  `footer_hint(agent, mode)`; `_handle_agent_command` / `_handle_mode_command`; `s-tab` keybind in
  `_build_key_bindings` (cycles gate mode, renders, invalidates); `_bottom_toolbar(deps, gate)` reads
  live state; `run_app` gained `mode=` + `_apply_startup_mode`, reordered so deps/gate/`select_agent`
  precede the `PromptSession`, and slash-command routing on the single input surface before submit.
- `tests/unit/decode/tui/test_app.py` — pure-parser, next-mode, confirmation, live-toolbar, and
  `_handle_*` handler tests; updated the two `footer_hint` callers for the new signature.
- `tests/unit/decode/test_cli.py` — `--mode` pass-through / default-None / unknown-mode-exit /
  `--mode plan` reaching the gate / `--agent plan --mode default` override.
- `tests/unit/decode/tui/test_app_e2e.py` — real-`run_app` piped tests for `/agent`, `/mode`,
  Shift+Tab cycle (raw `\x1b[Z` keys via a new `_drive_run_app_with_keys`), and the working-looks-like
  write-runs-without-prompt (edit / bypass) + write-denied (plan) flows.

**Tests**
- Unit: 568 passing, 0 failing (`make pre-commit` / `make test`).
- Integration: 1 passing — M1 capstone (`tests/integration/test_milestone1_capstone.py`) still green.
- Total `make ci`: 569 passing, 0 warnings.

**Acceptance criteria**
- [x] `decode --mode plan` / `--mode nope` — `test_cli_mode_plan_starts_the_real_repl_in_plan_mode`,
      `test_cli_with_an_unknown_mode_exits_nonzero_with_a_friendly_line` + manual smoke.
- [x] `/agent` switch + confirmation, `/agent nope` friendly + alive —
      `test_run_app_agent_slash_switches_and_an_unknown_name_stays_alive` + `_handle_agent_command` units.
- [x] `/mode bypass` + confirmation, mutating tool runs without prompt, `/mode nope` friendly —
      `test_run_app_mode_slash_switches_and_an_unknown_mode_stays_alive`,
      `test_run_app_mode_bypass_lets_a_mutating_tool_run_without_a_prompt`.
- [x] Shift+Tab cycle (pure `next_mode` + piped keybind) — `test_next_mode_cycles_*`,
      `test_run_app_shift_tab_cycles_through_all_four_modes`.
- [x] `footer_hint` includes agent + mode; toolbar reflects a live change —
      `test_footer_hint_includes_the_active_agent_and_mode`, `test_bottom_toolbar_reads_the_live_agent_and_mode`.
- [x] Working looks like — `test_run_app_shift_tab_to_edit_lets_a_write_run_without_a_prompt`,
      `test_run_app_mode_plan_denies_a_write_without_asking`.
- [x] `make ci` green, 0 warnings; single-input-surface invariant holds (no second `prompt_async`).

**Evidence**
```
$ make ci
uv lock --check
Resolved 166 packages in 4ms
uv run ruff format --check
95 files already formatted
uv run ruff check
All checks passed!
uv run pytest
...
tests/integration/test_milestone1_capstone.py .                          [100%]
============================= 569 passed in 7.60s ==============================

$ uv run decode --help
  --mode NAME         Start in this permission mode (default / plan / edit /
                      bypass); defaults to the agent's own default mode.

$ GEMINI_API_KEY=dummy-key uv run decode --mode nope
Decode: unknown mode 'nope'; valid modes: default, plan, edit, bypass.   # exit=1

$ GEMINI_API_KEY="" uv run decode --mode plan
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).   # exit=1
```

**Notes**
- The Shift+Tab keybind cycles + renders directly (closure over `gate` + the `emit_line` confirmation
  sink) and calls `event.app.invalidate()` so the live footer redraws — it does not exit the prompt, so
  the typed buffer is preserved. No collision with `escape` / `escape enter` (distinct `s-tab` key).
- The footer reads live state because `bottom_toolbar` is a `lambda` closing over `deps`/`gate`
  (re-read each render), not a snapshot. `--mode` override is applied after `select_agent` (which resets
  to the agent default), so an explicit `--mode` wins.
- The interactive REPL can't be launched from a non-TTY shell (prompt_toolkit needs a terminal fd), so
  the slash/keybind/footer surfaces are proven by the real-`run_app` piped e2e tests rather than a live
  manual REPL — same production wiring, network boundary swapped for a `FunctionModel`.
- NOT COMMITTED — handing to the Tester first per the workflow.

### [Tester] 2026-06-25 16:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 95 files clean; `ruff check` all passed)
- Unit tests: 568 passed / 0 failed
- Integration tests: 1 passed / 0 failed (M1 capstone)
- `make ci`: 569 passed; Warnings: 0 (`filterwarnings=["error"]` in effect; grep of full run shows no warning/deprecation lines)

**E2E adversarial pass** (real `run_app`, piped prompt_toolkit input + `FunctionModel`; same wiring the SWE used)
- Happy path: `decode --help` shows the `--mode NAME` option; `GEMINI_API_KEY=dummy decode --mode nope` → `Decode: unknown mode 'nope'; valid modes: default, plan, edit, bypass.` exit=1 (PASS)
- Break path 1 (single-input-surface / decision hijack): typed `/mode bypass` AT a pending permission prompt → routed to the decision channel as the (deny) answer; mode STAYED default, write body never ran, turn resumed to completion. Slash command did NOT hijack the pending decision (PASS)
- Break path 2 (state edge: agent switch changes mode+toolset): `/agent plan` mid-session → confirmation `Decode - agent: plan (mode: plan).`; a subsequent `write` was DENIED without asking, proving the gate genuinely flipped to plan (not just a cosmetic line) (PASS)
- Break path 3 (keybind collision): bare `\x1b` (Esc) still aborts a streaming turn after `s-tab` was added — `[aborted]` rendered (PASS)
- Break path 4 (keybind collision): `\x1b\r` (Alt+Enter) still queues a follow-up turn — `reply-1` produced (PASS)
- Break path 5 (buffer preservation): typed `partA`, pressed `\x1b[Z` (Shift+Tab → mode:edit), typed `partB`+Enter → model received `partApartB`; keybind did not exit the prompt or clear the buffer (PASS)
- Break path 6 (boundary/malformed parser inputs): `/agent`/`/mode` (no name), `/agent   ` (whitespace), `/AGENT build` / `/MODE bypass` (uppercase command → fall-through, consistent with `/quit`), `/agentfoo`/`/agentplan` (not a command), `/agent build extra args` / `/mode plan extra` (trailing junk), `parse_mode_name("  Plan  ")` (case/whitespace) — none crash; bare names render usage lines, junk renders a friendly "unknown" line, state left unchanged (PASS)
- Guard ordering: `GEMINI_API_KEY="" decode --mode nope` → no-key line wins; `decode --agent nope --mode alsobad` → agent error wins; `--mode ""` → friendly unknown-mode line. Order is key → agent → mode (PASS)

**Acceptance criteria**
- [x] PASS — `decode --mode plan` plan / `--mode nope` friendly exit — manual run (exit=1, friendly line, no traceback) + `test_cli_mode_plan_starts_the_real_repl_in_plan_mode` (gate.mode is PLAN), `test_cli_with_an_unknown_mode_exits_nonzero_with_a_friendly_line`
- [x] PASS — `/agent plan` switch (prompt+toolset+mode) + confirmation, `/agent nope` friendly + alive — `test_run_app_agent_slash_switches_and_an_unknown_name_stays_alive` + Tester break path 2 (mode flip proven by the denied write) + `_handle_agent_command` units
- [x] PASS — `/mode bypass` + no-prompt mutating tool, `/mode nope` friendly — `test_run_app_mode_bypass_lets_a_mutating_tool_run_without_a_prompt`, `test_run_app_mode_slash_switches_and_an_unknown_mode_stays_alive`
- [x] PASS — Shift+Tab cycle default→edit→plan→bypass→default, no Alt+Enter/Esc collision — `test_next_mode_cycles_default_edit_plan_bypass_default`, `test_run_app_shift_tab_cycles_through_all_four_modes` + Tester break paths 3/4/5 (Esc + Alt+Enter still work, buffer preserved); three distinct `bindings.add` keys (`escape enter` / `escape` / `s-tab`) at app.py:405/409/413
- [x] PASS — `footer_hint()` pure includes agent+mode; toolbar reflects a live change — `test_footer_hint_*`, `test_bottom_toolbar_reads_the_live_agent_and_mode`; live output `agent:build mode:default | Enter steer | Alt+Enter follow-up | Esc abort | Shift+Tab mode | /agent /mode /quit`
- [x] PASS — Working-looks-like (Shift+Tab→edit write runs; /mode plan write denied) — `test_run_app_shift_tab_to_edit_lets_a_write_run_without_a_prompt`, `test_run_app_mode_plan_denies_a_write_without_asking` + Tester break path 2
- [x] PASS — `make ci` green, 0 warnings, no second `prompt_async` — 569 passed/0 warnings; grep src: exactly one `prompt_async` (app.py:628) + one `PromptSession` (app.py:604)

**Evidence**
```
$ make ci
uv lock --check ... uv run ruff format --check ... 95 files already formatted
uv run ruff check ... All checks passed!
============================= 569 passed in 6.90s ==============================

$ uv run pytest -q 2>&1 | grep -iE "warning|deprecat"   # (no output → 0 warnings)

# Tester adversarial e2e (real run_app, FunctionModel; temp file, removed after):
BREAK PATH 1 PASS: slash during pending decision did not hijack
BREAK PATH 2 PASS: /agent plan flipped mode to plan; write denied without asking
BREAK PATH 3 PASS: Esc still aborts mid-turn
BREAK PATH 4 PASS: Alt+Enter still queues a follow-up
BREAK PATH 5 PASS: Shift+Tab preserved the typed buffer (submitted 'partApartB')
5 passed
```

**Other issues found**
- None blocking. Note (PASS with note): the slash *command keyword* is case-sensitive — `/AGENT` / `/MODE` fall through to the model as chat (only `/agent` / `/mode` are recognized). This is intentional and consistent with the existing `/quit` (`is_quit_command`) convention; the *argument* is case/whitespace-insensitive (`parse_mode_name` lowercases). No change required.
- Note: `--mode ""` (explicit empty string) is treated as an unknown mode (friendly exit 1) rather than "use the agent default" (only the absence of the flag, `None`, keeps the default). Reasonable and non-blocking.

**VERDICT: PASS**
