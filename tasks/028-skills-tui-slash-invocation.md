---
id: 028-skills-tui-slash-invocation
feature: skills
status: pending
---

# Skills: user-facing `/<skill-name>` TUI invocation (second entry point)

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (two entry points — the user's TUI command).
Depends on: 025 · Blocks: 029

## Scope

Add a **second entry point** for skills: the user types `/<skill-name>` (e.g. `/commit`,
`/review-diff`) in the TUI to invoke a skill directly, alongside the model's `skill` dispatcher
(026). Both entry points resolve through the same `load_skills(cwd)` to the same body. Mirror the
existing `/agent` / `/mode` slash parsing + dispatch in `src/decode/tui/app.py` — read those first,
then mirror their shape exactly (pure parser + handler + `Decode - …` inline messaging).

- **`tui/app.py` — pure parser** `parse_skill_command(line) -> tuple[str, str] | None`, mirroring
  `parse_agent_command` / `_parse_slash_arg`:
  - `"/commit"` → `("commit", "")`; `"/commit fix the bug"` → `("commit", "fix the bug")` (name +
    trailing text, both stripped).
  - A non-slash line (`"hello"`) → `None` (fall through to normal `runner.submit`).
  - A **reserved** slash command (`"/quit"`, `"/agent …"`, `"/mode …"`) → `None`, so the existing
    handlers win (belt-and-suspenders: they are already matched earlier in the loop). The reserved set
    is the existing `_QUIT_COMMAND` / `_AGENT_COMMAND` / `_MODE_COMMAND` (`/resume` is a CLI flag, not
    a TUI command — not reserved here).
  - Add `parse_skill_command` to the module docstring's list of pure, unit-tested "decidable pieces".
- **`tui/app.py` — handler** `_handle_skill_command(name, trailing, *, cwd, emit) -> str | None`:
  - Resolve `name` via `load_skills(cwd)`. On a **match**, return the skill `body` as the turn input;
    if `trailing` is non-empty, append it: `f"{body}\n\n{trailing}"`. The caller submits this through
    the existing `runner.submit(...)` pipeline (no new turn path).
  - On **no match**, `emit` a friendly one-line message listing the available skills (sorted names)
    and return `None` (no turn submitted) — this doubles as discovery. Match the `/agent`/`/mode`
    unknown-argument style (`Decode - unknown command '/<name>'; available skills: commit, review-diff.`;
    when there are none: `… no skills available.`). Add `_SKILL_*` constants for the messages.
- **`run_app` main loop** — add the skill branch **after** the `/agent` and `/mode` checks and before
  the empty-line check, so reserved commands win:
  ```python
  skill_cmd = parse_skill_command(text)
  if skill_cmd is not None:
      name, trailing = skill_cmd
      turn_input = _handle_skill_command(name, trailing, cwd=deps.cwd, emit=emit_line)
      if turn_input is not None:
          await runner.submit(turn_input, intent)
      continue
  ```
- **Built-in TUI commands win:** `/quit`, `/agent`, `/mode` take precedence over a same-named skill; a
  skill whose name collides with a reserved command is still reachable via the `skill` dispatcher tool
  (ADR-0004 §3,5). Our two skills (`commit`, `review-diff`) do not collide.
- **Behavior change to flag:** today a stray `/foo` falls through to `runner.submit("/foo", …)` (sent
  to the model). With this task, an unrecognised `/<x>` that is neither reserved nor a known skill is
  intercepted with the available-skills line (no turn). Check `tests/unit/decode/tui/test_app.py` and
  `test_app_e2e.py` for any assertion that a `/foo` is submitted to the model and update it.

## Acceptance criteria

- [ ] `parse_skill_command` is pure and returns: `("commit", "")` for `/commit`; `("commit", "fix X")`
      for `/commit fix X`; `None` for a non-slash line; `None` for `/quit`, `/agent build`, `/mode plan`
      (reserved). Unit-tested in `tests/unit/decode/tui/test_app.py`.
- [ ] A **known** skill injects its body as the turn input and runs a turn through the existing
      `runner.submit` pipeline (the body — not the literal `/commit` — is what is submitted). Tested.
- [ ] **Trailing text** after the name is appended to the body (`/commit ship it` submits the commit
      body followed by `ship it`). Tested.
- [ ] An **unknown** `/<x>` (not reserved, not a skill) emits the available-skills line and submits
      **no** turn. Tested.
- [ ] A **reserved** command is not shadowed by a same-named skill: with a project skill named `mode`
      present, typing `/mode plan` still switches the mode (reserved handler wins) and the skill stays
      reachable via the dispatcher. Tested (uses a tmp project skill).
- [ ] The pure parser + handler are unit-tested mirroring the existing TUI test conventions; the live
      loop path is covered by the `run_app` regression test (`test_app_e2e.py`) driving `/commit`.
- [ ] `make ci` green, 0 warnings; `tests/unit/decode/tui/` continues to mirror `src/decode/tui/`.

## Out of scope
- The model-facing dispatcher tool — task 026 (done).
- The catalog injection — task 027 (done).
- The capstone integration test — task 029.
- Updating `footer_hint` to advertise `/<skill>` (optional polish; keep the footer's existing
  `/agent /mode /quit` to avoid churn — discovery is served by the unknown-slash message).
- A `~/.decode/skills` source (deferred, ADR-0004).

## Log
### [PA] 2026-06-25 — Grooming
Round-2 delta: skills get a **second entry point** — the user's `/<skill-name>` TUI command — in
addition to the model's dispatcher. I read `tui/app.py`: the reserved slash commands are `/quit`,
`/agent`, `/mode` (handled in that order before the fall-through `runner.submit`); `/resume` is a CLI
flag, **not** a TUI command, so it's not a shadowing concern. `parse_skill_command` mirrors
`parse_agent_command`'s pure shape; the handler resolves via `load_skills(cwd)` and injects the body
as the turn input through the existing submit pipeline (no new path). Placed the branch after `/agent`
/ `/mode` so built-ins win; a same-named skill stays reachable via the dispatcher. Flagged the
behavior change for a stray `/foo` (was submitted to the model; now intercepted with an available-
skills discovery line) so the SWE updates any test that relied on the old fall-through. Depends only
on the loader (025); placed after the catalog task per the plan, before the capstone.
