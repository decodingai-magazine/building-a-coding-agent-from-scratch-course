---
id: 028-skills-tui-slash-invocation
feature: skills
status: done
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

- [x] `parse_skill_command` is pure and returns: `("commit", "")` for `/commit`; `("commit", "fix X")`
      for `/commit fix X`; `None` for a non-slash line; `None` for `/quit`, `/agent build`, `/mode plan`
      (reserved). Unit-tested in `tests/unit/decode/tui/test_app.py`.
- [x] A **known** skill injects its body as the turn input and runs a turn through the existing
      `runner.submit` pipeline (the body — not the literal `/commit` — is what is submitted). Tested.
- [x] **Trailing text** after the name is appended to the body (`/commit ship it` submits the commit
      body followed by `ship it`). Tested.
- [x] An **unknown** `/<x>` (not reserved, not a skill) emits the available-skills line and submits
      **no** turn. Tested.
- [x] A **reserved** command is not shadowed by a same-named skill: with a project skill named `mode`
      present, typing `/mode plan` still switches the mode (reserved handler wins) and the skill stays
      reachable via the dispatcher. Tested (uses a tmp project skill).
- [x] The pure parser + handler are unit-tested mirroring the existing TUI test conventions; the live
      loop path is covered by the `run_app` regression test (`test_app_e2e.py`) driving `/commit`.
- [x] `make ci` green, 0 warnings; `tests/unit/decode/tui/` continues to mirror `src/decode/tui/`.

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

### [SWE] 2026-06-26 10:30 — Implementation

**Files modified**
- `src/decode/tui/app.py` — added the pure `parse_skill_command` parser, the `_handle_skill_command`
  handler (resolves via `load_skills(cwd)`, returns the body / appends trailing / emits a discovery
  line), the `_RESERVED_COMMANDS` set + `_SKILL_*` message constants, the `load_skills` import, the
  docstring decidable-pieces entry, and the `run_app` skill branch (after `/agent`+`/mode`, before
  the empty-line check).
- `tests/unit/decode/tui/test_app.py` — `parse_skill_command` table (incl. reserved → None), the
  three `_handle_skill_command` cases (known body / trailing append / unknown discovery), the empty
  catalog "no skills available" branch (mocked `load_skills`), and the reserved-not-shadowed test
  (tmp project skill named `mode`).
- `tests/unit/decode/tui/test_app_e2e.py` — three live-`run_app` regression tests driving `/commit`
  (body injected, not the literal slash), `/commit <trailing>` (appended), and an unknown `/<x>`
  (intercepted with the discovery line, no turn) via the existing piped-prompt_toolkit harness.

**Tests**
- Unit: 652 passing, 0 failing (`make unit-tests`); the 21 added/relevant skill cases all green.
- Integration: 1 passing (capstone unchanged — no infra touched).
- `make ci` green, 0 warnings (653 = 652 unit + 1 integration; `filterwarnings=["error"]`).

**Acceptance criteria**
- [x] `parse_skill_command` pure cases — `tests/unit/decode/tui/test_app.py::test_parse_skill_command`.
- [x] Known skill injects its body via `runner.submit` — `test_app_e2e.py::test_run_app_skill_slash_injects_the_body_and_runs_a_turn` + `test_app.py::test_handle_skill_command_returns_the_known_skill_body`.
- [x] Trailing text appended — `test_app_e2e.py::test_run_app_skill_slash_appends_trailing_text` + `test_app.py::test_handle_skill_command_appends_trailing_text`.
- [x] Unknown `/<x>` emits available-skills line, no turn — `test_app_e2e.py::test_run_app_unknown_slash_is_intercepted_and_runs_no_turn` + `test_app.py::test_handle_skill_command_unknown_emits_available_skills_and_returns_none`.
- [x] Reserved command not shadowed by same-named skill — `test_app.py::test_reserved_command_is_not_shadowed_by_a_same_named_skill`.
- [x] Pure parser + handler unit-tested; live loop covered by `run_app` regression.
- [x] `make ci` green, 0 warnings; `tests/unit/decode/tui/` mirrors `src/decode/tui/`.

**Evidence**
```
$ make ci
... 653 passed in 7.01s

$ uv run python -c "from pathlib import Path; from decode.tui import app; ..."
parse /commit            -> ('commit', '')
parse /commit ship it    -> ('commit', 'ship it')
parse /mode plan (resvd) -> None
parse hello (non-slash)  -> None
known /commit -> first body line: 'You commit the work in the **current working tree** autonomously: ...'
emitted lines on match: []
unknown /frobnicate -> returns: None
discovery line: Decode - unknown command '/frobnicate'; available skills: commit, review-diff.
```

**Notes**
- **Stray-`/foo` behavior change:** no existing test asserted that a `/foo` is submitted to the model
  (searched `tests/` for `submit("/`, `"/foo"`, `unrecognis*`, `unknown command` — none), so no test
  needed updating. The new behavior is locked in by the e2e
  `test_run_app_unknown_slash_is_intercepted_and_runs_no_turn` (the unknown slash never reaches the
  model and the REPL stays alive).
- `name` is used only as a `dict.get` key and in display strings — never interpolated into a path or
  shell command (025/026 Tester forward-note honoured).
- Reserved precedence is enforced twice (belt-and-suspenders): the `/agent`+`/mode` handlers run
  before the skill branch in `run_app`, AND `parse_skill_command` returns `None` for any
  `_RESERVED_COMMANDS` entry.
- DO NOT COMMIT — handing off to the Tester.

### [Tester] 2026-06-26 01:01 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 106 files clean; `ruff check` all passed).
- Unit tests: 652 passed / 0 failed.
- Integration tests: 1 passed / 0 failed.
- `make ci`: 653 passed (`uv lock --check` + format-check + lint-check clean).
- Warnings: 0 (`filterwarnings=["error"]` active — a green run guarantees zero warnings).

**E2E adversarial pass**
- Happy path: live `run_app` driving `/commit` → the built-in commit **body** ("Conventional
  Commits", not the literal `/commit`) is submitted as the turn input and a turn runs (PASS).
- Happy path 2: live `run_app` driving `/deploy to staging` against a tmp **project-only** skill →
  body injected with trailing appended (`body\n\nto staging`); confirms project skills (not just
  built-ins) resolve through the live loop (PASS).
- Break path 1 (parser — boundary/whitespace/casing): `parse_skill_command` table over 22 inputs —
  `/commit`→`('commit','')`, `/commit fix the bug`→`('commit','fix the bug')`, `/commit   x  y`→
  `('commit','x  y')` (ends stripped), `  /commit   ship it  `→`('commit','ship it')`, non-slash→
  `None`, bare `/`→`None`, `/   `→`None`, reserved `/quit`/`/agent build`/`/agent`/`/mode plan`/
  `/mode`→`None`, `/COMMIT`→`('COMMIT','')` (case-sensitive, won't resolve). All as expected (PASS).
- Break path 2 (reserved-not-shadowed, **driven through the live loop** — harder than the SWE's
  unit coverage): tmp cwd with a real project skill named `mode` under `.decode/skills/`; typing
  `/mode plan` → mode switches to plan (`Decode - mode: plan.`), the skill body marker is **never**
  submitted to the model, **no** "available skills" discovery line, and a subsequent normal line
  still runs a turn. Confirms the `/mode` reserved handler wins live, not just in the parser (PASS).
- Break path 3 (hostile path/shell metacharacters, live loop): `/../../etc/passwd` and
  `/deploy;rm -rf /` → only the discovery line renders, no turn runs, the traversal/shell strings
  never reach the model, and `name` is used purely as a `dict.get` key (verified in handler) — the
  REPL survives and a normal line afterwards still runs (PASS). 025/026 forward-note honoured.
- Break path 4 (unknown-slash interception + REPL survival, live loop): unknown `/<x>` emits the
  sorted available-skills line and submits no turn; a following normal line runs a turn (PASS).
- Output path: all user-facing lines flow through `emit_line` → `console.print(render.render_event(...))`;
  no bare `print(` in `app.py` (PASS).

**Acceptance criteria**
- [x] PASS — `parse_skill_command` pure (`/commit`→`('commit','')`; `/commit fix X`→`('commit','fix
      X')`; non-slash→`None`; reserved `/quit`/`/agent build`/`/mode plan`→`None`) — `test_app.py::
      test_parse_skill_command` (10 cases) + my 22-input adversarial table all match.
- [x] PASS — known skill injects its **body** via `runner.submit` (not the literal `/commit`) —
      `test_app_e2e.py::test_run_app_skill_slash_injects_the_body_and_runs_a_turn`; reproduced live.
- [x] PASS — trailing text appended as `body\n\n<trailing>` — `test_app_e2e.py::
      test_run_app_skill_slash_appends_trailing_text` + handler check: separator is exactly `\n\n`.
- [x] PASS — unknown `/<x>` emits sorted available-skills line, no turn —
      `test_app_e2e.py::test_run_app_unknown_slash_is_intercepted_and_runs_no_turn`; empty-catalog
      branch (`… no skills available.`) covered by `test_handle_skill_command_with_no_skills_available…`.
- [x] PASS — reserved not shadowed by same-named skill — `test_app.py::
      test_reserved_command_is_not_shadowed_by_a_same_named_skill` (parser) **and** my live-loop
      driver with a real `.decode/skills/mode.md`: `/mode plan` switches mode, never injects the body.
- [x] PASS — pure parser + handler unit-tested; live loop covered by `run_app` regression driving
      `/commit`; module docstring lists `parse_skill_command` among the pure decidable pieces.
- [x] PASS — `make ci` green (653 passed), 0 warnings; `tests/unit/decode/tui/` mirrors
      `src/decode/tui/` (`test_app.py`/`test_render.py` ↔ `app.py`/`render.py`; `test_app_e2e.py` is
      the existing live-loop harness).

**Independent check — stray-`/foo` behavior change**
Confirmed no prior test relied on the old fall-through: every `send("/...")` in HEAD's
`test_app_e2e.py` is a reserved command (`/quit`/`/agent`/`/mode`); a repo-wide grep for
`submit("/`, `"/foo"`, `unrecognis*`, `unknown command` finds matches only in the **new** task-028
e2e test. SWE's claim holds — no test needed updating.

**Evidence**
```
$ make ci
... 653 passed in 7.07s
$ uv run pytest <9 skill tests> -v
... 18 passed in 1.26s
$ python -c "drive run_app with tmp .decode/skills/mode.md; /mode plan"
saw 'mode: plan' confirmation: True
skill body marker submitted to model: False (must be False)
discovery 'available skills' shown: False (must be False — /mode handled it)
LIVE-LOOP RESERVED-NOT-SHADOWED: PASS
```

**Other issues found**
- Non-blocking: a bare `/` (or `/` followed only by spaces) parses to `None` and is then submitted
  to the model as the literal text `/` (it is truthy, so it passes the empty-line guard). This
  matches the spec ("bare `/` → `None` → fall through") and is harmless, but a future polish could
  intercept it too. Out of scope for this AC.
- Non-blocking: trailing text strips only its ends, not internal whitespace (`/commit   x  y` →
  trailing `x  y`); this matches `/agent` / `/mode` arg handling and the "both stripped" wording.

**VERDICT: PASS**
