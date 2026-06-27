---
id: 054-lsp-app-exit-shutdown
feature: lsp-integration
status: pending
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

- [ ] `run_app` calls the LSP Service shutdown on exit (after the loop, near `extract_on_exit`); the
      `run_app` regression test (piped prompt_toolkit input) still passes and exits cleanly.
- [ ] When no server was spawned during the session, the shutdown call is a no-op and exit proceeds
      (unit-tested via the service seam: shutdown invoked, no spawn ever happened, no error).
- [ ] When a server WAS spawned (faked), exit invokes its `shutdown`→`exit`→terminate sequence
      exactly once; unit-tested against the fake service.
- [ ] A shutdown failure is swallowed (logged) — `run_app` still prints `Decode - bye.` and returns
      normally. Unit-tested (fake shutdown raises).
- [ ] No real `ty`/subprocess is started in unit tests; `make ci` green, 0 warnings.

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
