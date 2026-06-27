---
id: 054-lsp-app-exit-shutdown
feature: lsp-integration
status: done
---

# Shut down the LSP server cleanly on app exit

Tags: `lsp`, `infra`, `tui`
Depends on: #051
Blocks: #055

This task implements ADR-0007. The lazy per-root server (task 051) is a child subprocess; when the
REPL exits it must be shut down cleanly (LSP `shutdown`→`exit`, then terminate) so no orphaned
`ty server` lingers. Wire it into the existing `run_app` exit path next to the other on-exit work.

## Scope

- **Where:** `src/decode/tui/app.py`'s `run_app`, the exit path after the input loop
  (app.py:898-909 — where `decisions.cancel()`, `runner.wait_idle()`, and `extract_on_exit(...)`
  already run). Add a best-effort LSP shutdown call (the task-051 service's shutdown entry) here,
  alongside the memory write-back, so every clean exit (`/quit` or `Ctrl-D`) tears the server down.
- **Best-effort, fully non-fatal** (mirror `extract_on_exit`'s "never raises, cannot block exit"
  contract): if no server was ever spawned (lazy — the common case for a session that used no LSP),
  the call is a cheap no-op; if shutdown fails, it is logged and swallowed — it must NEVER prevent the
  REPL from exiting or mask the `Decode - bye.` line.
- Idempotent: calling shutdown when nothing is running, or twice, is safe.
- Logger, not `print`; type-annotate incl. `-> None`.

## Acceptance criteria

- [x] `run_app` calls the LSP Service shutdown on exit (after the loop, near `extract_on_exit`); the
      `run_app` regression test (piped prompt_toolkit input) still passes and exits cleanly.
- [x] When no server was spawned during the session, the shutdown call is a no-op and exit proceeds
      (unit-tested via the service seam: shutdown invoked, no spawn ever happened, no error).
- [x] When a server WAS spawned (faked), exit invokes its `shutdown`→`exit`→terminate sequence
      exactly once; unit-tested against the fake service.
- [x] A shutdown failure is swallowed (logged) — `run_app` still prints `Decode - bye.` and returns
      normally. Unit-tested (fake shutdown raises).
- [x] No real `ty`/subprocess is started in unit tests; `make ci` green, 0 warnings.

## User stories

### Story: The user quits and no server is left running
1. User runs `decode`, uses the `lsp` tool a few times (a `ty server` child spawns), then types
   `/quit`.
2. On exit, decode sends `shutdown`/`exit` to `ty` and terminates the child; `Decode - bye.` prints.
3. After exit, `pgrep -f "ty server"` (started by this session) shows nothing — no orphan.

### Story: A session that never used LSP exits instantly
1. User runs `decode`, does only plain chat, and presses `Ctrl-D`.
2. No server was ever spawned, so the shutdown step is a no-op; exit is immediate, `Decode - bye.`
   prints. No error, no delay.

### Story: Shutdown hiccup never blocks exit
1. The server is wedged and ignores `shutdown`.
2. The exit path bounds/forces the terminate and swallows any error; the REPL still exits and prints
   `Decode - bye.`.

## Out of scope
- Per-turn server lifecycle (servers are per-root and live for the session — task 051).
- A signal-handler / atexit hook beyond the existing `run_app` exit path.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
Wire a best-effort LSP server shutdown into `run_app`'s existing exit path so no `ty server` orphans;
non-fatal and a no-op when nothing was spawned.

**Key decisions**
- Reuse the `extract_on_exit` exit site (app.py:898-909); same "never raises, never blocks exit"
  contract.
- Idempotent + lazy-aware: no-op when no server spawned.

**Dependencies**
- #051 — the service's shutdown entry + the per-root cache it tears down.

**User stories**
- 3 stories: clean quit leaves no orphan, no-LSP session exits instantly, wedged-server still exits.

Ready for implementation.

### [SWE] 2026-06-27 14:30 — Implementation

**Files modified**
- `src/decode/tui/app.py` — import the task-051 `shutdown_all` (as `shutdown_lsp_servers`) and call it
  best-effort on the `run_app` exit path, right after `extract_on_exit` and before `Decode - bye.`,
  wrapped in a `try/except` that logs+swallows so it can never block exit.
- `tests/unit/decode/tui/test_app_e2e.py` — two new real-`run_app` e2e tests: shutdown fires exactly
  once on exit (clean-exit line still renders), and a raising shutdown is swallowed (still prints bye).

**Tests**
- Unit: 910 passing, 0 failing (`make pre-commit`, includes the 2 new e2e tests). Targeted
  `test_app_e2e.py` + `test_service.py` = 42 passing.
- Integration: N/A for new infra; the full `make ci` (910 unit + 9 integration) = 919 passing.
- The service-seam criteria (no-op when unspawned; spawned → shutdown→exit→terminate once + idempotent)
  are covered by the task-051 service tests `test_shutdown_all_noop_when_nothing_spawned` and
  `test_shutdown_all_terminates_and_clears`, which this task's wiring relies on (re-verified green).

**Acceptance criteria**
- [x] `run_app` calls LSP shutdown on exit; regression tests still pass — `tests/unit/decode/tui/test_app_e2e.py::test_run_app_shuts_down_lsp_servers_on_exit` (+ all existing `run_app` e2e tests green).
- [x] No server spawned → no-op — `tests/unit/decode/services/lsp/test_service.py::test_shutdown_all_noop_when_nothing_spawned`.
- [x] Server spawned (faked) → `shutdown`→`exit`→terminate once — `tests/unit/decode/services/lsp/test_service.py::test_shutdown_all_terminates_and_clears`.
- [x] Shutdown failure swallowed, still prints bye — `tests/unit/decode/tui/test_app_e2e.py::test_run_app_swallows_lsp_shutdown_failure_and_still_exits`.
- [x] No real `ty`/subprocess in unit tests; `make ci` green, 0 warnings.

**Evidence**
```
$ make ci                       # uv lock --check + format-check + lint-check + full suite
All checks passed!
919 passed in 8.30s

$ uv run pytest tests/unit/decode/tui/test_app_e2e.py tests/unit/decode/services/lsp/test_service.py -q
42 passed in 2.28s
```

E2E smoke (real `run_app` exit path, real unpatched `shutdown_all`, only the subprocess + model seams
faked — proves a spawned server is actually torn down):
```
[setup] cached LSP roots before exit: [.../building-a-coding-agent-from-scratch-course]
[exit] received by fake server: ['initialize', 'initialized', 'textDocument/didOpen',
                                 'textDocument/definition', 'shutdown', 'exit']
[exit] fake subprocess returncode: 0
[exit] cached LSP roots after exit: []
[exit] 'Decode - bye.' printed: True
SMOKE OK: real run_app exit path shut the spawned LSP server down and said bye.
$ pgrep -f "ty server"   →  no ty server orphan
```

**Notes**
- TDD: the new run_app tests went red first (`shuts_down_lsp` failed with `count == 0` before the call
  was wired), then green once the call landed.
- The smoke run prints a Gemini `400 API_KEY_INVALID` traceback — that is `extract_on_exit`'s memory
  write-back logging a *swallowed* warning on the dummy key (runs before the LSP teardown); it does NOT
  block exit, which the smoke's passing assertions (server torn down + bye printed) confirm.
- Not committed — handing off to the Tester first per the lifecycle.

### [Tester] 2026-06-27 15:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 127 files clean; `ruff check` clean)
- Unit tests: 910 passed / 0 failed
- Integration tests: 9 passed / 0 failed (`make ci` → 919 passed)
- Warnings: 0 (`filterwarnings=["error"]` enforced)

**E2E adversarial pass** (real `run_app`, piped prompt_toolkit input, all subprocess/model seams faked — no real `ty`)
- Happy path: `/quit` after a chat turn → `Decode - bye.` renders, faked shutdown fires once (PASS)
- Break path 1 (state edge — Ctrl-D/EOF exit, NOT covered by SWE tests): closed the pipe to raise
  `EOFError` instead of `/quit` → shutdown still invoked exactly once, bye still printed (PASS).
  Both `break` sites (app.py:836 EOF, :851 /quit) fall through to the same exit block (:914).
- Break path 2 (no-op when unspawned, REAL unpatched `shutdown_all`): chat-only session → exit ran
  the genuine `shutdown_all`; `_CLIENTS == {}`, no error, bye printed (PASS).
- Break path 3 (failure mode — shutdown RAISES): faked `boom()` → WARNING `lsp shutdown on exit
  failed` logged (logger, not print) AND `Decode - bye.` printed; exit never blocked (PASS).
- Break path 4 (ordering): captured call order `["extract", "shutdown"]` then bye → memory write-back
  still runs and LSP teardown rides next to it without perturbing it (PASS).
- Break path 5 (real cached client torn down via run_app's UNPATCHED exit path): seeded a genuine
  `LspClient` via the spawn seam, drove `run_app` to exit → fake server `received` has `shutdown`+`exit`,
  `returncode == 0`, `_CLIENTS` cleared, bye printed (PASS). Proves `app_mod.shutdown_lsp_servers is
  lsp_service.shutdown_all` (import-identity asserted) and the wiring tears a real client down end-to-end.
- Wedged-server hang (story 3): code-read `LspClient.shutdown` — the `shutdown` request is bounded by
  `asyncio.wait_for(lsp_request_timeout_s)` and `_terminate` force-kills after `_SHUTDOWN_GRACE_S`, so a
  hung server cannot block `run_app` indefinitely (PASS, by inspection).
  (6 throwaway adversarial tests written + run green, then deleted — working tree left clean.)

**Acceptance criteria**
- [x] PASS — `run_app` calls LSP shutdown on exit near `extract_on_exit` — app.py:914-917 (after
      extract_on_exit:908, before the bye line:919); `test_run_app_shuts_down_lsp_servers_on_exit` +
      all 20 existing `run_app` e2e tests green; EOF and /quit both reach it (adversarial break path 1).
- [x] PASS — No server spawned → no-op, exit proceeds — `test_shutdown_all_noop_when_nothing_spawned`
      + adversarial break path 2 (real unpatched `shutdown_all` through `run_app`, `_CLIENTS=={}`).
- [x] PASS — Spawned (faked) → shutdown→exit→terminate once — `test_shutdown_all_terminates_and_clears`
      (received shutdown+exit, returncode 0, cache cleared, idempotent 2nd call) + adversarial break path 5.
- [x] PASS — Failure swallowed (logged), still prints bye — `test_run_app_swallows_lsp_shutdown_failure_and_still_exits`
      + adversarial break path 3 (WARNING logged, bye printed); app.py:916-917 try/except logs+swallows.
- [x] PASS — No real `ty`/subprocess in unit tests; `make ci` green, 0 warnings — spawn seam patched in
      all service tests, `shutdown_lsp_servers` patched in app e2e tests; `make ci` → 919 passed, 0 warnings.

**Evidence**
```
$ make ci
All checks passed!  (uv lock --check + ruff format --check + ruff check)
919 passed in 8.24s

$ uv run pytest tests/unit/decode/tui/test_app_e2e.py tests/unit/decode/services/lsp/test_service.py -q
42 passed
```

**Other issues found**
- None blocking. `await shutdown_lsp_servers()` is correctly placed AFTER `extract_on_exit` and its own
  try/except guards it; note `extract_on_exit` itself is unguarded in `run_app` (relies on its own
  "never raises" contract) — pre-existing, out of scope for this task.
- Diff is limited to the 3 expected files (`src/decode/tui/app.py`, this task md, `test_app_e2e.py`);
  no stray `git add`. Uses `logger.warning`, not `print`; `shutdown_all() -> None` is type-annotated.
- Note: `code-review` plugin is enabled in `.claude/settings.json` but is not invocable from the
  Tester subagent tool surface; the full manual checklist + adversarial pass stood in as the review.

**VERDICT: PASS**
