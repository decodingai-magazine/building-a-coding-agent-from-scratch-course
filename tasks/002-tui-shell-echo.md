---
id: 002-tui-shell-echo
feature: m1-vanilla-agent
status: done
---

# TUI shell (echo)

## Scope
The terminal UI layer per [ADR-0002 §6](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md): `patch_stdout()` + concurrent `prompt_async()` input with append-style Rich output. No agent yet — echoes input.

## Acceptance criteria
- [x] `tui/app.py` runs a persistent input line via `prompt_async()` inside `patch_stdout()`; `Alt+Enter` and `Esc` keybindings registered; footer hint shows steer/follow-up/abort.
- [x] `tui/render.py` holds pure `entities.events` → Rich renderables; tool calls rendered on completion (no flicker).
- [x] A typed line echoes back via Rich; `Ctrl-D` / `/quit` exits.
- [x] Render functions unit-tested on fake events; no `print()` in library code.

## Out of scope
- Wiring to the harness/agent (task 003+); in-place live regions / full-screen rendering.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Verify the prompt stays pinned under heavy streaming.

### [SWE] 2026-06-19 20:45 — Implementation

**Files modified**
- `src/decode/tui/__init__.py` — new TUI package docstring (input: prompt_toolkit, output: Rich, append-style)
- `src/decode/tui/render.py` — pure event-to-Rich-renderable functions + minimal local event contract (`EchoEvent`/`MessageEvent`/`ToolCallEvent`); tool calls render as a Panel on completion
- `src/decode/tui/app.py` — async echo REPL: `prompt_async()` inside `patch_stdout()`, `Alt+Enter`/`Esc` keybindings (record intent), bottom-toolbar footer hint, `Ctrl-D`/`/quit` exit; pure helpers `is_quit_command`, `footer_hint`, `InputIntent`
- `src/decode/cli.py` — replaced the `click.echo` bootstrap stub with `asyncio.run(run_app())`; `init_logger()` stays at module level
- `tests/unit/decode/tui/test_render.py` — pure render-function tests on fake events
- `tests/unit/decode/tui/test_app.py` — pure quit-intent / footer-hint / intent-enum tests

**Tests**
- Unit: 23 passing, 0 failing (`make unit-tests`) — 13 new under `tests/unit/decode/tui/`, plus the 3 pre-existing `test_cli.py` tests still green (CliRunner's non-interactive stdin hits EOF and the loop exits cleanly)
- Integration: N/A — no infra changes

**Acceptance criteria**
- [x] `tui/app.py` persistent input via `prompt_async()` in `patch_stdout()`; `Alt+Enter`/`Esc` registered; footer hint — verified by `tests/unit/decode/tui/test_app.py::test_footer_hint_*` + PTY e2e (toolbar shows steer / Alt+Enter follow-up / Esc abort / /quit)
- [x] `tui/render.py` pure event→Rich; tool calls as Panel on completion — verified by `tests/unit/decode/tui/test_render.py::test_render_tool_call_event_is_a_panel` and siblings
- [x] typed line echoes via Rich; `Ctrl-D` / `/quit` exits — verified by `test_app.py::test_is_quit_command_*` + PTY e2e (echo of input; both Ctrl-D and /quit exit status 0)
- [x] render functions unit-tested on fake events; no `print()` in library code — `grep` confirms no `print(` in `src/decode/`; output goes through Rich/`click.echo`

**Evidence**
```
$ make pre-commit
uv run ruff format --check
15 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
collected 23 items
tests/unit/decode/config/test_settings.py ....                           [ 17%]
tests/unit/decode/test_cli.py ...                                        [ 30%]
tests/unit/decode/test_logging.py ...                                    [ 43%]
tests/unit/decode/tui/test_app.py ......                                 [ 69%]
tests/unit/decode/tui/test_render.py .......                             [100%]
============================== 23 passed in 0.19s ==============================

$ # e2e via PTY (uv run decode), typing a line then /quit:
startup banner: True
echo of input: True      # "echo hello decode" rendered above the prompt
goodbye: True            # "decode - bye."

$ # e2e PTY answering CPR so the bottom toolbar paints:
toolbar present (Alt+Enter): True
toolbar present (Esc abort): True
toolbar present (steer): True
toolbar present (/quit to exit): True

$ # e2e PTY sending Ctrl-D at an empty prompt:
Ctrl-D goodbye: True
exit status: 0
```

**Notes**
- AC#2 wording says "pure `entities.events` → Rich renderables", but the task **Scope** instructs to define only a *minimal local contract* in `render.py` (the full `entities.events` union lands in task 003). I followed the Scope: `render.py` owns `EchoEvent`/`MessageEvent`/`ToolCallEvent` locally; `entities/__init__.py` is untouched. Task 003 should swap the local contract for the canonical union and re-point `render_event`.
- `Alt+Enter` and `Esc` keybindings currently only *record intent* (an `InputIntent` returned from `prompt_async`) — there is no harness yet (per Scope / ADR-0002 §3-5). Follow-up intent is logged + echoed; abort intent prints `[abort]`. Task 003 wires these into the two-queue harness + cooperative-abort flag.
- Ambiguous-Unicode lint (RUF001/RUF002) flagged the prompt glyph and en/em-dashes; switched to ASCII (`> ` prompt, `|` separators, `-` dashes) to keep lint clean without per-file noqa.
- ADR-0002 §6 risk "prompt stays pinned under heavy streaming": not stress-tested here (no streaming source until task 004). `patch_stdout(raw=True)` is the mechanism; the heavy-streaming check belongs to the agent-loop task.
- `make integration-tests` NOT RUN — no infra changes; nothing under `tests/integration/`.

### [Tester] 2026-06-19 21:30 — QA

**Test summary**
- Format-check: PASS (`uv run ruff format --check` -> 15 files already formatted)
- Lint-check: PASS (`uv run ruff check` -> All checks passed!)
- Pre-commit (format + lint + unit): PASS
- Unit tests: 23 passed / 0 failed (13 new under `tests/unit/decode/tui/`)
- Integration tests: 0 collected (exit 5 "no tests ran") — nothing under `tests/integration/` applies to this task; not a failure of this work
- Warnings: 0 (`filterwarnings=["error"]` is in effect, so any warning would have failed the run)
- code-review plugin: enabled but is an interactive slash-command, not a CLI; applied its review lens manually over the (small) diff — no defects found

**E2E adversarial pass** (drove the real `uv run decode` via a raw-`pty` harness that answers CPR and sets `TIOCSWINSZ` so prompt_toolkit reserves the toolbar row; chunked non-blocking writes to avoid PTY backpressure)
- Happy path: type `hello decode\r` then `/quit\r` -> banner `decode - type a line; /quit exits.` paints, line echoes as `echo hello decode` above the prompt, `decode - bye.` prints, exit 0 (PASS)
- Bottom toolbar: paints exactly `Enter steer | Alt+Enter follow-up | Esc abort | Ctrl-D or /quit to exit` (steer / Alt+Enter follow-up / Esc abort / /quit all present) (PASS)
- Ctrl-D at empty prompt (`\x04`): goodbye printed, exit 0 (PASS)
- Break 1 (boundary: empty line + whitespace-only `   \t  `): skipped via `if not text.strip()` (app.py:132), 0 echo lines, no crash, exit 0 (PASS)
- Break 2 (state edge: `/quit now` trailing text): NOT treated as quit (`is_quit_command` uses `==` after strip, app.py:48-50) — echoed, REPL stays alive, then clean `/quit` exits, single goodbye, exit 0 (PASS). Leading-whitespace `   /quit` correctly DOES exit (PASS)
- Break 3 (boundary: 5000-char line): echoed in full, no crash/hang, exit 0, single goodbye (PASS)
- Break 4 (Unicode: `café 日本語 🚀 é`): all glyphs echoed, no UnicodeError, exit 0 (PASS)
- Break 5 (keybinding: Esc abort): prints `[abort]`, REPL stays alive, toolbar repaints, subsequent `/quit` exits 0 (PASS — note: a lone Esc has prompt_toolkit's inherent escape-disambiguation delay before it resolves; expected terminal behavior, not a defect)
- Break 6 (keybinding: Alt+Enter follow-up, `\x1b\r`): intent recorded, text echoed, REPL stays alive, `/quit` exits 0 (PASS)
- Break 7 (hostile: `$(rm -rf /); \`id\`; a && b | c > d`): echoed inertly (no shell — none wired yet), exit 0, no crash (PASS)
- Break 8 (slash lookalike `/help`): echoed, not mistaken for quit, exit 0 (PASS)
- Break 9 (multi-line burst one/two/three): all echoed in order, single goodbye, exit 0 (PASS)
- No stray `print()` output leaked past the prompt in any run; all user output flows through `console.print(...)` (Rich)

**Acceptance criteria**
- [x] PASS — `app.py` persistent input via `prompt_async()` inside `patch_stdout(raw=True)`; `Alt+Enter`/`Esc` keybindings registered; footer hint shows steer/follow-up/abort
      Evidence: app.py:107-119 (`PromptSession` + `patch_stdout(raw=True)` loop), :68-85 (`escape,enter` + `escape` bindings), :53-60 footer; `test_app.py::test_footer_hint_mentions_steer_followup_and_abort` + `test_footer_hint_mentions_quit`; e2e toolbar paint verified above
- [x] PASS — `render.py` pure event->Rich renderables; tool calls render on completion as a Panel (no flicker)
      Evidence: render.py:54-86 pure functions, `_render_tool_call` returns `Panel`; `test_render.py::test_render_tool_call_event_is_a_panel`, `::..shows_name_summary_and_result`, `::..without_result_still_renders`, `::test_render_event_rejects_unknown_event_type` (loud `TypeError`). Scope note: AC wording says `entities.events` but the union lands in task 003 — `entities/__init__.py` is intentionally empty of models ("events in 003"), so the documented minimal LOCAL contract in render.py is the correct call for task 002 per Scope + ADR-0002 seam list. Functions ARE pure event->Rich; only the event-type source differs (tracked for 003). Judged satisfied.
- [x] PASS — a typed line echoes back via Rich; `Ctrl-D` / `/quit` exits
      Evidence: app.py:137 echoes via `render.EchoEvent`, :120 `EOFError`->break (Ctrl-D), :124 `/quit`->break; `test_app.py::test_is_quit_command_*`; e2e: echo confirmed, both Ctrl-D and `/quit` exit 0
- [x] PASS — render functions unit-tested on fake events; no `print()` in library code
      Evidence: 7 render tests on fake events all pass; `grep -rn "print(" src/decode` -> only `console.print(...)` (Rich), zero raw `print()`; all `tui` defs carry return annotations

**Evidence**
```
$ make pre-commit
uv run ruff format --check
15 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
collected 23 items
tests/unit/decode/config/test_settings.py ....                           [ 17%]
tests/unit/decode/test_cli.py ...                                        [ 30%]
tests/unit/decode/test_logging.py ...                                    [ 43%]
tests/unit/decode/tui/test_app.py ......                                 [ 69%]
tests/unit/decode/tui/test_render.py .......                             [100%]
============================== 23 passed in 0.12s ==============================

$ # e2e PTY happy path: hello decode + /quit
EXIT 0
echo hello decode   # rendered above the prompt
decode - bye.

$ # e2e PTY bottom-toolbar paint (TIOCSWINSZ + CPR answered)
toolbar: Enter steer | Alt+Enter follow-up | Esc abort | Ctrl-D or /quit to exit

$ # e2e PTY break paths
very-long-line-5000: exit 0, echoed, no hang
unicode-line (café 日本語 🚀): exit 0, all glyphs echoed
esc-abort: [abort] printed, stays alive, /quit exit 0
shell-metachars / /help / multi-line-burst: all exit 0, echoed inertly
```

**Other issues found**
- None blocking. PASS-with-note items:
  - The bottom toolbar only renders when the terminal reports its size; verified in a real PTY with `TIOCSWINSZ`. Under a sizeless pipe/PTY (no winsize) prompt_toolkit falls back to a no-toolbar layout — expected, and the unit tests cover the hint string independently.
  - Bare `Esc` carries prompt_toolkit's inherent escape-sequence disambiguation delay before the abort fires; correct terminal behavior, just slower than a single keystroke.
  - ADR-0002 §6 "prompt stays pinned under heavy streaming" risk is intentionally NOT stress-tested here (no streaming source until task 004); `patch_stdout(raw=True)` is the mechanism. Belongs to the agent-loop task — agreed.
  - Scope deviation (local event contract vs `entities.events`) is documented and correct for task 002; task 003 must swap to the canonical union and re-point `render_event`.

**VERDICT: PASS**
