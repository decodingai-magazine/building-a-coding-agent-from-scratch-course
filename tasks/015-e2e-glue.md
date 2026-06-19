---
id: 015-e2e-glue
feature: m1-vanilla-agent
status: done
---

# End-to-end glue + Testing E2E docs

## Scope
Tie the milestone together: fill the `AGENTS.md` "Testing E2E" section and add a full-stack integration test.

## Acceptance criteria
- [x] `AGENTS.md` "Testing E2E" documents the concrete `decode` launch + what "working" looks like for each surface.
- [x] An integration test under `tests/integration/` drives the full stack with `TestModel`, exercising: a read, a gated write (approve **and** deny paths), a task update, an AskUser, a web fetch, a session-log replay, and the on-exit memory line.
- [x] `make ci` green.

## Out of scope
- Real-Gemini calls in CI (manual e2e only).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Final integration task for the feature.

### [SWE] 2026-06-20 10:05 — Implementation

**Files modified**
- `AGENTS.md` — filled the "Testing E2E" section: concrete `uv run decode` launch, a per-surface
  table (plain chat / read / write approve+deny / bash / todo / web_fetch / ask_user), mid-turn
  steer (Enter) / follow-up (Alt+Enter) / abort (Esc), `--resume` replay, and the on-exit
  `MEMORY.md` summary round-trip; points at the capstone test as the automated proof.
- `src/decode/cli.py` — no-key startup guard (task-004 carryover): before `run_app`, if
  `settings.gemini_api_key` is empty, `click.echo` one friendly line to stderr and
  `raise click.exceptions.Exit(1)` instead of letting `build_agent()` dump a raw `UserError`.
- `tests/unit/decode/test_cli.py` — added the two guard tests (clean message + non-zero exit on
  empty key; present key does not trip the guard); extended the autouse key fixture to patch
  `decode.cli.settings` too so the existing CLI tests still reach `run_app`.
- `tests/integration/test_milestone1_capstone.py` — NEW capstone: a scripted six-step
  conversation (read → gated write approve → gated write deny → todo_write → ask_user →
  web_fetch) through the real `build_agent()` + `Runner` + `AgentTurnHandler` + `render_event` +
  `SessionLog` + `extract_on_exit`, asserting the file appears/doesn't, the denial reaches the
  model, the JSONL log replays, `--resume` (load_latest) reseeds a fresh handler, and a dated
  `MEMORY.md` line is written. No network: `FunctionModel` model, `TestModel` summarizer,
  `httpx.MockTransport` web seam, `sessions_dir`/`MEMORY.md` under `tmp_path`.
- `tests/integration/conftest.py` — NEW (was only `.gitkeep`): header note for the suite.

**Tests**
- Unit: 346 passing, 0 failing (was 344; +2 CLI guard tests).
- Integration: 1 passing (the capstone) — `tests/integration/test_milestone1_capstone.py`.
- Full `make ci`: 347 passed; `uv lock --check`, `ruff format --check`, `ruff check` all clean.

**Acceptance criteria**
- [x] `AGENTS.md` "Testing E2E" — filled with runnable, project-specific instructions per surface.
- [x] Integration test under `tests/integration/` drives the full stack (read, gated write
  approve+deny, todo_write, ask_user, web_fetch, session-log replay, on-exit memory line) —
  `tests/integration/test_milestone1_capstone.py::test_milestone1_capstone_full_stack`.
- [x] `make ci` green.

**Evidence**
```
$ make ci
uv lock --check
Resolved 166 packages in 4ms
uv run ruff format --check
79 files already formatted
uv run ruff check
All checks passed!
uv run pytest
...
tests/integration/test_milestone1_capstone.py .                          [100%]
============================= 347 passed in 6.13s ==============================

$ env -u GEMINI_API_KEY uv run decode   # no-key startup guard (stderr)
decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).
# exit_code=1, nothing on stdout, no traceback

$ ls .decode   # repo session dir stays absent after the full run
ls: .decode: No such file or directory
```

**Notes**
- No commit — handing to the Tester per the lifecycle.
- Manual interactive e2e against real Gemini (streaming chat, the gated/steer/abort keystrokes)
  is the documented manual pass and is out of CI scope (the task's "Real-Gemini calls in CI" is
  out of scope); a `GEMINI_API_KEY=dummy uv run decode </dev/null` smoke confirmed the guard lets
  a configured launch reach `run_app` (prompt_toolkit then errors on the non-TTY stdin, which is
  expected and unrelated to this task).
- **Left for `/review` (task-012 carryover):** the memory walk's "project root" is the launch
  `cwd` (ADR-0002 §8; `append_session_summary` writes `cwd/MEMORY.md`, `assemble_memory` walks
  cwd→repo-root). The capstone pins `deps.cwd` to a tmp workspace, so it does not settle the open
  filesystem-root-vs-repo-root question from task 012 — flagging it for the reviewer rather than
  deciding it here (would be an architectural fork).

### [Tester] 2026-06-20 11:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`uv lock --check`, `ruff format --check` "79 files already
  formatted", `ruff check` "All checks passed!")
- Unit tests: 346 passed / 0 failed
- Integration tests: 1 passed / 0 failed (capstone)
- Full `make ci`: 347 passed, exit 0
- Warnings: 0 (filterwarnings=error — any warning would have failed the run)
- Repo cleanliness: after a clean-state `make ci`, the repo's `.decode/` and `MEMORY.md` stay
  **absent** (verified twice). `.decode/` is gitignored; `MEMORY.md` is not (informational — it
  is a committable memory file by design, not runtime state, and no on-quit summary is produced
  in CI).

**E2E adversarial pass**
- Happy path (no-key guard): `env -u GEMINI_API_KEY uv run decode </dev/null` → exit 1, stderr
  one line `decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).`,
  **stdout empty (0 bytes)**, **no traceback** (PASS). No `.env` present, so the guard fired on a
  genuinely empty key, not an env fallback.
- Break path 1 (state: configured launch on non-TTY): `GEMINI_API_KEY=dummy uv run decode
  </dev/null` → guard does NOT fire, proceeds past it to `run_app` (banner "decode - type a line;
  /quit exits." on stdout), then prompt_toolkit raises `OSError: [Errno 22]` on the non-TTY stdin
  — expected and unrelated to this task; the guard correctly let a configured run through (PASS).
- Break path 2 (mutation: byte-write disabled in `tools/files.py::write`): capstone FAILS on
  `assert created.is_file() — "the approved write must create the file"` (PASS — assertion is
  load-bearing; reverted).
- Break path 3 (mutation: deny routed as approve in `agent/loop.py`): capstone FAILS on
  `assert not (...).exists() — "a denied write must not hit disk"` (PASS — reverted).
- Break path 4 (mutation: generic denial message instead of the gate reason): capstone FAILS on
  `"the denial must reach the model"` (PASS — proves the denial *reason* propagates as the tool
  result, independently of the file-absent check; reverted).
- Break path 5 (mutation: `session_log.append_turn` no-op): capstone FAILS on `"the JSONL log
  must replay the whole conversation"` (PASS — replay round-trip is real; reverted).
- Break path 6 (mutation: undated MEMORY.md line in `memory/extract.py`): capstone FAILS on
  `"the summary line must be dated (UTC)"` (PASS — reverted).
- Break path 7 (mutation: task-panel render emits "(tasks hidden)"): capstone STILL PASSES — the
  `_TODO_CONTENT in rendered` / `_ASK_QUESTION in rendered` assertions are satisfied by the
  generic `ToolCallStarted` args-render, not uniquely by the dedicated panel renderer. NOT a bug:
  the headline render guarantee (render_event runs on every emitted event without raising) is
  proven, and the dedicated `_render_task_list_updated` / `_render_ask_user_requested` are
  separately unit-tested in `test_render.py` (4 dedicated tests). Logged below as a non-blocking
  precision note; reverted.

All seven source mutations reverted; `git diff --stat src/` shows only the task's `cli.py` guard.

**Acceptance criteria**
- [x] PASS — `AGENTS.md` "Testing E2E" documents the concrete `decode` launch + per-surface
      "working looks like".
      Evidence: filled section is runnable and accurate to the real CLI — prompt `_PROMPT = "> "`,
      footer hint, no-key message byte-for-byte equals `cli._NO_KEY_MESSAGE`, `--resume [SESSION]`
      help text ("latest, or a named session id / filename") matches `decode --help`, keybindings
      (Enter steer / Alt+Enter follow-up / Esc abort / Ctrl-D|/quit exit) match `tui/app.py`
      `InputIntent` + `@bindings.add("escape","enter")` / `("escape")`, `bash_timeout_s=120.0`,
      `sessions_dir=.decode/sessions`, `MEMORY.md` write-back all match `config/settings.py`.
- [x] PASS — integration test drives the full stack (read, gated write approve+deny, todo_write,
      ask_user, web_fetch, session-log replay, on-exit memory line).
      Evidence: `tests/integration/test_milestone1_capstone.py::test_milestone1_capstone_full_stack`
      passes in isolation (1.03s) and under `make ci`; runs the REAL `build_agent()` + `Runner` +
      `AgentTurnHandler` + `render_event` + `SessionLog` + `extract_on_exit`, swapping only the
      network boundary (`FunctionModel` model, `TestModel` summarizer, `httpx.MockTransport` web).
      Mutation-tested (break paths 2–6) — every asserted surface fails the test when broken.
- [x] PASS — `make ci` green.
      Evidence: `make ci` exit 0, 347 passed, 0 warnings; repo `.decode/` and `MEMORY.md` absent
      after the run.

The two no-key CliRunner guard tests
(`test_cli_with_no_gemini_key_exits_nonzero_with_a_friendly_line`,
`test_cli_with_a_present_gemini_key_does_not_trip_the_guard`) assert non-zero exit + GEMINI_API_KEY
+ .env.example + no "Traceback" + `run_app` not awaited on the empty-key path, and exit 0 +
`run_app` awaited once on the present-key path. Note: `CliRunner` merges stdout/stderr by default,
so the unit test does not prove stderr routing — the **real** `env -u` run above proves stderr-only
(stdout 0 bytes), closing that gap.

**Evidence**
```
$ make ci   # exit 0
uv lock --check            → Resolved 166 packages
uv run ruff format --check → 79 files already formatted
uv run ruff check          → All checks passed!
uv run pytest              → 347 passed in 6.12s
$ ls .decode MEMORY.md     → both absent

$ env -u GEMINI_API_KEY uv run decode </dev/null   # stdout/stderr split
exit_code=1 ; stdout=0 bytes ; stderr="decode: set GEMINI_API_KEY ... (see .env.example)." ; no Traceback
```

**Other issues found** (non-blocking — for `/review` to weigh)
- Render-assertion precision (break path 7): `_TODO_CONTENT` / `_ASK_QUESTION` "in rendered"
  pass via the generic tool-call args render, not the dedicated panels. Tightening them (assert
  against the `tasks` panel / `ask:` line specifically) would pin the dedicated renderers, but
  those are already unit-covered, so this is a nicety, not a gap.
- `MEMORY.md` is not gitignored (a real on-quit summary would surface in `git status`). Appears
  intentional (project memory file, like `AGENTS.md`); out of scope for this task — noted only.
- task-012 memory-walk "project root" (filesystem-root vs repo-root) is deliberately left for
  `/review` per the SWE note — not re-litigated here.

**VERDICT: PASS**
