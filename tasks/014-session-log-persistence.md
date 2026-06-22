---
id: 014-session-log-persistence
feature: m1-vanilla-agent
status: done
---

# Session-log persistence (JSONL + resume)

## Scope
Append-only JSONL session log with replay ([ADR-0002 §9](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)), wired into the harness + a `decode --resume` flag.

## Acceptance criteria
- [x] `context/session_log.py` writes a **header line 0** (`version, session_id, cwd, created_at` UTC) then appends each turn's `new_messages()` as **typed** JSONL to `.decode/sessions/<ts>_<uuid>.jsonl`.
- [x] `decode --resume` rebuilds `message_history` from the latest session file.
- [x] A truncated trailing line is tolerated on replay (skip, don't crash).
- [x] `.decode/sessions/` is gitignored.

## Out of scope
- SQLite/Kitaru durability; session branching/forking; compaction entries (later milestones).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. JSONL header + typed entries validated against pi; confirm the Pydantic AI serialization API.

### [SWE] 2026-06-20 09:30 — Implementation

**Files modified**
- `src/decode/context/__init__.py` — new package; re-exports `SessionLog`, `load`, `load_latest`, `resolve_session`.
- `src/decode/context/session_log.py` — append-only JSONL session log: `SessionLog.create` (typed header line 0 + `sessions_dir` creation, injectable tz-aware-UTC `now` / `uuid`), `append_turn` (typed `messages` lines, append-only), `load` / `load_latest` / `resolve_session` (tolerant replay → `list[ModelMessage]`).
- `src/decode/agent/loop.py` — `AgentTurnHandler` takes optional `session_log` + seed `message_history`; persists each turn's *new* messages at the would-stop boundary via a `_persisted_count` cursor (write failure logged, never fatal).
- `src/decode/tui/app.py` — `run_app(resume=...)` loads the requested session (latest / id / filename), seeds the handler, opens a fresh session log per run; friendly message when nothing matches.
- `src/decode/cli.py` — `--resume` value threaded through to `run_app(resume=...)`.
- `.gitignore` — added `.decode/` (session logs never committed).
- `.env.example` — documented `SESSIONS_DIR` (already in `settings.py`).
- `tests/unit/decode/context/test_session_log.py` — new: header, append-only, real-agent-run round-trip (`TestModel`), truncated/garbage-tail tolerance, empty session → `[]`, `load_latest`, `resolve_session`, naive-`now` rejection.
- `tests/unit/decode/agent/test_loop.py` — new: per-turn persistence, new-only batches, optional-log no-op.
- `tests/unit/decode/tui/test_app_e2e.py` — new: `run_app` persists to JSONL, `--resume latest` seeds history; autouse fixture redirects `sessions_dir` to tmp so e2e runs never touch the repo.
- `tests/unit/decode/test_cli.py` — new: `--resume` threads None / "latest" / `<id>` to `run_app`.

**Tests**
- Unit: 344 passing, 0 failing — `make unit-tests` / `make pre-commit` green.
- Integration: N/A — no infra changes (sync local-file append; ADR-0002 §7,10).

**Acceptance criteria**
- [x] Header line 0 + typed JSONL per turn — `tests/unit/decode/context/test_session_log.py::test_create_writes_a_typed_header_as_line_zero`, `::test_append_turn_appends_a_typed_messages_line`, `::test_load_round_trips_a_real_agent_run`.
- [x] `decode --resume` rebuilds `message_history` from the latest file — `tests/unit/decode/tui/test_app_e2e.py::test_run_app_resume_seeds_history_from_the_prior_session`, `tests/unit/decode/context/test_session_log.py::test_load_latest_picks_the_newest_session`, `tests/unit/decode/test_cli.py::test_cli_passes_latest_resume_for_the_bare_flag`.
- [x] Truncated trailing line tolerated — `::test_load_tolerates_a_truncated_trailing_line`, `::test_load_tolerates_a_garbage_non_json_trailing_line`.
- [x] `.decode/` gitignored — verified via `git check-ignore .decode/sessions/x.jsonl`.

**Evidence**
```
$ uv run decode --help
  --resume [SESSION]  Resume the latest session, or a named session id / filename.

$ # persist a real (offline TestModel) turn, simulate a crash, --resume replays it
session file: 20260619T090000Z_00000000-0000-0000-0000-0000000000e2.jsonl
file lines: 2
appended a truncated trailing line (simulated crash)
resumed message count: 2
resumed user prompt seen: True
round-trip equals original: True

$ git check-ignore .decode/sessions/x.jsonl
.decode/sessions/x.jsonl

$ make pre-commit
============================= 344 passed in 6.10s ==============================
$ uv lock --check
Resolved 166 packages in 2ms
```

**Notes**
- File append is **sync**, not async: each call writes one small line to a local file and the tool layer is sequential in v1 (ADR-0002 §7,10) — same rationale as the sibling `memory/extract.py` write-back; no concurrent writer to interleave.
- Persistence happens at the would-stop boundary (per turn), matching ADR-0002 §9's "persist new_messages to session log"; a resumed session counts its replayed prefix as already-persisted so resume never re-writes it.
- No new dependencies (`ModelMessagesTypeAdapter` ships with pydantic-ai 1.107); `uv.lock` unchanged.
- Two test-only fixes during TDD: `_conversation()` builds fresh `UserPromptPart` timestamps each call, so equality assertions now reuse the same object (a test bug, not an impl bug).

### [Tester] 2026-06-20 02:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 77 files clean; `ruff check` all passed; `make pre-commit` exit 0)
- Unit tests: 344 passed / 0 failed
- Integration tests: N/A (no infra changes — local-file append, ADR-0002 §7,10)
- `uv lock --check`: PASS (166 packages resolved, exit 0)
- Warnings: 0 (`filterwarnings=["error"]` would have failed the run otherwise)
- code-review plugin: enabled (`/code-review`, advisory slash-command — folded its correctness/security/error-handling/coverage checklist into the manual review below; nothing left to flag as a defect beyond the test-isolation FAIL)

**E2E adversarial pass** (real `run_app` + agent on `TestModel`/`FunctionModel`, tmp `sessions_dir`)
- Happy path: real `run_app` turn → opens exactly 1 `.jsonl`, `load` round-trips the prompt (PASS)
- Break path 1 (state edge — multi-turn round-trip + feed-back): 2-turn real agent run → `load` equals concatenated `new_messages()`; replayed history fed into a NEW `agent.run(message_history=...)` with no error (PASS)
- Break path 2 (crash mid-write — truncated trailing line): good turn + half-written `{"type":"messages",...` tail → `load` returns the good messages, no raise (PASS)
- Break path 3 (malformed — corrupt header / empty file / missing file): each → graceful `[]`, no crash (PASS)
- Break path 4 (hostile inputs — unicode + 200 KB content + tool-call/tool-result round-trip): byte/structurally equal after `load` (PASS)
- Break path 5 (resume e2e): run → quit → `run_app(resume="latest")` seeds prior history, the resumed turn's model sees `FIRST-SESSION-PROMPT`; resume opened a NEW 2nd file (PASS)
- Break path 6 (resume re-persist guard — §9 cursor): the new file contains ONLY the new turn, not the replayed prefix; the original file is untouched (PASS)
- Break path 7 (nothing to resume): `resume="latest"` against an empty dir → friendly "no prior session to resume.", fresh session, no crash; unknown id → "no session matching ..." (PASS)
- Break path 8 (no-clobber): two sessions at the identical timestamp → two uuid-distinct files (PASS)
- Probe scripts: `/tmp/probe_session.py` 19/19, `/tmp/probe_runapp.py` resume/persist checks all PASS

**Acceptance criteria**
- [x] PASS — `context/session_log.py` writes typed header line 0 + typed per-turn JSONL to `.decode/sessions/<ts>_<uuid>.jsonl` — `tests/unit/decode/context/test_session_log.py::test_create_writes_a_typed_header_as_line_zero` + `::test_append_turn_appends_a_typed_messages_line`; probe confirms production-`now` header is tz-aware UTC (`created_at` offset 0). Wired in `tui/app.py:298` via `SessionLog.create(settings.sessions_dir, ...)`; per-turn append at the would-stop boundary in `agent/loop.py:144,179`.
- [x] PASS — `decode --resume` rebuilds `message_history` from the latest session file — `::test_run_app_resume_seeds_history_from_the_prior_session`; e2e probe: resumed model saw the prior prompt; `cli.py:24-38` threads `--resume` (bare flag → "latest").
- [x] PASS — truncated trailing line tolerated on replay — `::test_load_tolerates_a_truncated_trailing_line`, `::test_load_tolerates_a_garbage_non_json_trailing_line`; probe confirms corrupt-header + empty-file are also graceful (`_parse_messages_line` skips, never raises).
- [x] PASS — `.decode/sessions/` gitignored — `.gitignore:150` adds `.decode/`; `git check-ignore .decode/sessions/x.jsonl` matches.

**Evidence**
```
$ make unit-tests
============================= 344 passed in 6.11s ==============================

$ # ROOT-CAUSE of the suite-pollution FAIL — the 3 CLI tests that hit the real run_app:
$ rm -rf .decode/ && uv run pytest \
    tests/unit/decode/test_cli.py::test_cli_runs_and_exits_zero \
    tests/unit/decode/test_cli.py::test_cli_accepts_resume_flag \
    tests/unit/decode/test_cli.py::test_cli_accepts_named_resume -q
3 passed in 0.97s
$ ls .decode/sessions/ | wc -l
3            # <-- 3 header-only files written into the REPO's real .decode/sessions

$ # the rest of the suite is clean (the test_app_e2e autouse fixture isolates its 6 tests):
$ rm -rf .decode/ && uv run pytest tests/unit -q \
    --deselect .../test_cli_runs_and_exits_zero \
    --deselect .../test_cli_accepts_resume_flag \
    --deselect .../test_cli_accepts_named_resume
341 passed, 3 deselected in 6.00s
$ ls .decode/sessions/ | wc -l
0            # <-- clean
```

**Other issues found**
- **[FAIL — test isolation / suite pollution]** `tests/unit/decode/test_cli.py::test_cli_runs_and_exits_zero`, `::test_cli_accepts_resume_flag`, `::test_cli_accepts_named_resume` invoke the **real** `cli` → real `run_app` → `SessionLog.create(settings.sessions_dir, ...)` with the **unredirected production default** `.decode/sessions`. Every `make unit-tests` run writes 3 header-only `.jsonl` files into the repo's real `.decode/sessions/` (the 24 stale 277-byte files found at QA start are this accumulating over prior runs). The SWE's own `test_app_e2e.py` already solves this with an autouse fixture (`monkeypatch.setattr(app_mod.settings, "sessions_dir", tmp_path/"sessions")`); the three `test_cli.py` tests that drive a real `run_app` are missing the same redirect. This is the explicit "suite polluting the repo `.decode/`" failure the task flagged to look for. Fix: add a `sessions_dir`→tmp autouse fixture to `test_cli.py` (mirroring `test_app_e2e.py`), or have those three tests mock `run_app`. `.decode/` being gitignored masks it from `git status` but does not stop the writes. (I removed the accumulated `.decode/` artifacts during QA — gitignored, so no diff impact.)
- (note, non-blocking) `console.print(...)` in `tui/app.py` is the legitimate Rich user-facing TUI path, not banned bare `print()` in library code — correct.
- (note, non-blocking) sync file append is justified in the module docstring + ADR-0002 §7,10; agreed for v1 (one small local-file write, sequential tool layer). No concurrent writer; same-timestamp sessions stay uuid-distinct.

**VERDICT: FAIL**
```

### [SWE] 2026-06-20 03:10 — Fixes (test isolation)

Addressed the Tester's single FAIL — suite pollution of the repo's real `.decode/`. **Test-only change; no behavior change.**

**Files modified**
- `tests/unit/decode/test_cli.py` — added an autouse `_isolate_sessions_dir` fixture that `monkeypatch.setattr(app_mod.settings, "sessions_dir", tmp_path / "sessions")`, mirroring the existing fixture in `tests/unit/decode/tui/test_app_e2e.py`. Patches `decode.tui.app.settings` — the `settings` object the `run_app` code path actually reads (`tui/app.py:225,236,298` reference `settings.sessions_dir` off `from decode.config.settings import settings`). The three real-`cli`→`run_app` tests now write their header-only `.jsonl` into a per-test tmp dir instead of the repo. Kept the tmp-redirect (not `run_app`-mock) so the CLI→`run_app`→`SessionLog.create` wiring still gets exercised, per the Tester's preference.

**Root cause**
- `test_cli_runs_and_exits_zero`, `test_cli_accepts_resume_flag`, `test_cli_accepts_named_resume` drove the real `cli` → `run_app` → `SessionLog.create(settings.sessions_dir, ...)` against the **unredirected production default** `.decode/sessions`, writing 3 header-only files into the repo on every `make unit-tests`. `.decode/` is gitignored so `git status` hid the writes; gitignore does not stop the writes.

**Verification (`.decode/`-stays-absent proof)**
```
$ # BEFORE the fix — repro:
$ rm -rf .decode && uv run pytest \
    tests/unit/decode/test_cli.py::test_cli_runs_and_exits_zero \
    tests/unit/decode/test_cli.py::test_cli_accepts_resume_flag \
    tests/unit/decode/test_cli.py::test_cli_accepts_named_resume -q
3 passed
$ ls .decode/sessions/ | wc -l
3            # <-- polluted

$ # AFTER the fix — same 3 tests, repo .decode/ never created:
$ rm -rf .decode && uv run pytest <same 3 tests> -q
3 passed
$ [ -e .decode ] && echo PRESENT || echo ABSENT
ABSENT       # <-- GOOD

$ # FULL suite from a clean repo — green AND no .decode/ created:
$ rm -rf .decode && uv run pytest tests/unit -q
344 passed in 6.11s
$ [ -e .decode ] && echo PRESENT || echo ABSENT
ABSENT       # <-- GOOD

$ rm -rf .decode && make pre-commit
============================= 344 passed in 6.09s ==============================
$ [ -e .decode ] && echo PRESENT || echo ABSENT
ABSENT       # <-- GOOD

$ make format-check && make lint-check
77 files already formatted · All checks passed!
$ uv lock --check
Resolved 166 packages in 2ms
```

**Notes**
- All 344 tests stay green; `make pre-commit` exit 0; `uv lock --check` clean; no new deps; no network (`filterwarnings=["error"]` would have failed otherwise).
- Cleaned up the stale `.decode/` left in the repo (gitignored — no diff impact).
- NOT committed — handing back to the Tester for re-review.
