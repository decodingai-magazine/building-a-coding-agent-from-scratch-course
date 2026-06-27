---
id: 056-lsp-capstone-e2e
feature: lsp-integration
status: done
---

# LSP capstone: active tool + passive enricher through the real stack (e2e)

Tags: `lsp`, `test`
Depends on: #052, #053
Blocks: —

This task implements ADR-0007's end-to-end proof, in the style of
`tests/integration/test_milestone1_capstone.py` and `test_compaction_capstone.py`: drive the **real**
`build_agent()` + `AgentTurnHandler` + `Runner` + `render_event` + `SessionLog` through a scripted
conversation that exercises BOTH LSP channels, swapping only the **LSP subprocess boundary** (the
task-051 service seam) for a fake — no real `ty`, no network. Plus an optional, guarded test that
exercises a REAL `ty server` when available.

## Scope

- Add `tests/integration/test_lsp_capstone.py` that, like the milestone-1 capstone, builds the real
  stack with a scripted `FunctionModel` and swaps **only** the LSP client seam (task 051's patchable
  spawn/service seam) for a **fake** returning canned `definition` + `diagnostics` responses (mirror
  how the M1 capstone swaps the model with `FunctionModel` and `web_fetch`'s transport with
  `MockTransport`). Redirect the session log + `.decode/` under `tmp_path`. No `GEMINI_API_KEY`, no
  network, no real subprocess.
- **Scripted conversation asserts both channels:**
  1. **Active:** the model calls `lsp` with `op=definition` → the tool **auto-allows** (READ_ONLY, no
     permission prompt) and the canned location comes back as a `path:line:column` tool result the
     model sees.
  2. **Passive (errors):** the model `write`s a `.py` file the fake server reports an error for → the
     `write` tool result is the **exact** `Wrote '…' (...).` base string **plus** the appended
     `LSP diagnostics (ty) — fix these:` block (errors-only).
  3. **Passive (clean):** the model `write`s/`edit`s a clean `.py` file (fake reports no errors) → the
     tool result is the base string **unchanged** (silent).
  4. **Non-`.py`:** a `write` to a non-`.py` file → base string unchanged (enricher never runs).
  5. **Unavailable:** with the fake seam reporting "unavailable", a buggy `.py` write still returns
     just the base string and an `lsp` tool call returns a model-readable unavailable `ModelRetry` —
     the turn never crashes.
- Assert the **real renderer** ran on every emitted event without raising (whole render path proven),
  and the session log writes/replays as in the other capstones.
- **Optional real-`ty` test:** a separate test guarded by `@pytest.mark.skipif` (skip when the `ty`
  binary is not importable/available on PATH) that spawns a **real** `ty server` against a tiny
  on-disk `.py` fixture and asserts a real `definition` and a real error diagnostic come back. It runs
  in CI **only if** `ty` is present (the dev group installs it via task 050, so `make ci` exercises
  it; a `ty`-less environment skips, never fails).

## Acceptance criteria

- [x] Runs under `make integration-tests` / `make ci` with **no** `GEMINI_API_KEY` and **no** network;
      the hermetic capstone swaps only the LSP service seam for a fake (model is a `FunctionModel`).
- [x] Active channel asserted: `lsp op=definition` auto-allows (no permission request recorded) and
      returns the canned `path:line:column` to the model.
- [x] Passive-errors asserted: a buggy `.py` write result == exact base `Wrote …` string + the
      appended errors-only diagnostics block.
- [x] Passive-clean asserted: a clean `.py` write/edit result == the base string unchanged.
- [x] Non-`.py` asserted: enricher never runs; base string unchanged.
- [x] Unavailable asserted: buggy `.py` write returns base only; `lsp` tool returns a `ModelRetry`;
      no crash; the turn completes.
- [x] The real renderer runs on every event without raising; the JSONL session log writes and replays.
- [x] The optional real-`ty` test runs when `ty` is available and is **skipped** (not failed) when it
      is not; when it runs it asserts a real definition + a real error diagnostic.
- [x] `make ci` green, 0 warnings.

## User stories

### Story: The capstone proves both channels end-to-end
1. A maintainer runs `make integration-tests`.
2. The LSP capstone drives a scripted conversation through the real agent stack with a fake server,
   proving go-to-definition (active) and post-edit error diagnostics (passive) both reach the model,
   and that clean/non-`.py`/unavailable cases behave.
3. All assertions pass with no API key and no network.

### Story: CI with `ty` installed proves the real wire
1. CI (dev group installed, `ty` present) runs the guarded real-`ty` test.
2. A real `ty server` returns a real definition and a real diagnostic for a fixture `.py` file.
3. The test passes, proving the hand-rolled JSON-RPC framing/handshake work against the actual server.

### Story: A `ty`-less environment still passes
1. A contributor without `ty` runs `make ci`.
2. The hermetic capstone passes (fake seam); the real-`ty` test is skipped.
3. The suite is green — the feature degrades gracefully in test too.

## Out of scope
- Re-testing the units already covered by 051/052/053 (this is the integrated proof).
- A live Gemini run.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
The integrated proof: both LSP channels (active `lsp` tool + passive enricher) through the real
build_agent/handler/runner/render/log stack with a faked LSP service boundary, plus an optional
guarded real-`ty` test.

**Key decisions**
- Swap only the LSP service seam (mirrors the M1 capstone swapping the model + web transport) —
  hermetic, no real subprocess by default.
- A `skipif(ty unavailable)` real-`ty` test proves the wire when the dev binary is present; skips
  (never fails) otherwise.

**Dependencies**
- #052 (the tool), #053 (the enricher) — the surfaces under test.

**User stories**
- 3 stories: hermetic both-channels proof, real-`ty` wire proof, graceful skip without `ty`.

Ready for implementation.

### [SWE] 2026-06-27 14:30 — Implementation

**Files modified**
- `tests/integration/test_lsp_capstone.py` — new: the LSP capstone (3 tests) driving both ADR-0007
  channels through the real `build_agent`/`AgentTurnHandler`/`Runner`/`render_event`/`SessionLog`
  stack with a `FunctionModel`, swapping only the task-051 `_spawn_process` seam for a fake.
- `tests/support/lsp_fakes.py` — `FakeLanguageServer` now accepts a **callable** response value
  `(request_message) -> result` (backward-compatible), so one fake answers `textDocument/diagnostic`
  per file URI (error for `buggy.py`, clean otherwise). Existing static dict/list responses are
  unchanged.

**Tests**
- Unit: 922 passing, 0 failing (full `make ci` suite; LSP units 122/122 after the fake change).
- Integration: 12 passing — `test_lsp_capstone.py` adds 3 (both-channels hermetic, unavailable
  degradation, real-`ty` wire).

**Acceptance criteria**
- [x] No `GEMINI_API_KEY`/no network; swaps only `_spawn_process` — verified by the conftest hermeticity
      guard + `mocker.patch.object(lsp_service, "_spawn_process", ...)`.
- [x] Active `lsp op=definition` auto-allows + returns `path:line:column` —
      `test_lsp_capstone_both_channels_available` (`_DEF_RESULT` in tool returns; `lsp` never in
      `permission_requests`).
- [x] Passive-errors == exact base + errors-only block — same test (`buggy_return == _BUGGY_RESULT`,
      `startswith(_BUGGY_BASE)`).
- [x] Passive-clean (write + edit) == base unchanged — same test (`_CLEAN_WRITE_BASE` / `_CLEAN_EDIT_BASE`).
- [x] Non-`.py` base unchanged + enricher never queried the server — same test (no `textDocument/diagnostic`
      carries the `.md` URI).
- [x] Unavailable: base only + `lsp` `ModelRetry` + no crash —
      `test_lsp_capstone_unavailable_degrades_gracefully`.
- [x] Real renderer ran on every event; JSONL log writes + replays — both hermetic tests
      (`render_event` on every event; `session_log.load(...) == message_history`; header + 5 turns).
- [x] Real-`ty` test runs when `ty` present, skips otherwise; asserts real definition + real error —
      `test_lsp_capstone_real_ty_wire` (RAN here: `ty 0.0.55` on PATH).
- [x] `make ci` green, 0 warnings (`filterwarnings=["error"]`).

**Evidence**
```
$ uv run pytest tests/integration/test_lsp_capstone.py -v -rsx
tests/integration/test_lsp_capstone.py::test_lsp_capstone_both_channels_available PASSED [ 33%]
tests/integration/test_lsp_capstone.py::test_lsp_capstone_unavailable_degrades_gracefully PASSED [ 66%]
tests/integration/test_lsp_capstone.py::test_lsp_capstone_real_ty_wire PASSED [100%]
============================== 3 passed in 1.85s ===============================
  (no skip/xfail lines under -rsx → the real-`ty` test genuinely RAN, not skipped)

$ make ci
============================= 922 passed in 8.85s ==============================
```

**Notes**
- De-risked the real-`ty` wire before writing assertions: a probe through the real service returned
  `definition → Location(path='sample.py', line=1, column=5)` and a severity-1 diagnostic
  `Name \`not_defined_name\` used when not defined` — the real-`ty` test asserts the def resolves to
  line 1 and an error names the undefined symbol (robust to message-wording churn in pre-1.0 `ty`).
- Confirmed pydantic-ai runs sync tools via `anyio.to_thread.run_sync` (no `disable_threads` in
  decode), so the enricher's real `anyio.from_thread.run` bridge works through the worker thread — the
  capstone is the first place that bridge is proven end-to-end (units patched it out).
- Unavailable is its own conversation/test because a broken spawn is cached **per root** (ADR-0007 §4);
  reusing one root would mask the active/available path. The real-`ty` test calls `shutdown_all()` in a
  `finally` so the real subprocess is reaped (no `ResourceWarning` under `filterwarnings=["error"]`).
- DO NOT COMMIT — handing off to the Tester first.

### [Tester] 2026-06-27 15:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 128 files formatted; `ruff check`: all passed)
- Unit tests: 922 passed / 0 failed (full `make ci` suite) — LSP-area units 122/122
- Integration tests: 3/3 in `test_lsp_capstone.py` (12 integration total in the suite)
- `uv lock --check`: PASS
- Warnings: 0 (`filterwarnings=["error"]` — any warning would fail the run)

**E2E adversarial pass**
- Happy path: `env -u GEMINI_API_KEY uv run pytest tests/integration/test_lsp_capstone.py -v -rsx`
  → 3 passed, no skip/xfail lines (real-`ty` test genuinely RAN against `ty 0.0.55`) (PASS)
- Break path 1 (env/hermeticity — no API key): conftest scrubs `GEMINI_API_KEY` for every test and the
  explicit `env -u GEMINI_API_KEY` run still passes; only `_spawn_process` is patched (no network, no
  real subprocess in the two hermetic tests) → 3 passed (PASS)
- Break path 2 (state — `ty` absent / graceful skip): `PATH=/usr/bin:/bin .venv/bin/python -m pytest …
  -rsx` (`shutil.which("ty")` → None) → 2 passed, 1 **skipped** (reason: "the `ty` language server
  binary is not on PATH") — skipped, never failed (PASS)
- Break path 3 (state — repeat ×3 + interleave with `test_service.py` sharing the module-level
  `_CLIENTS` cache): 3 passed each run, 25 passed interleaved, `pgrep "ty server"` → none lingering
  (the `_isolate_lsp_cache` autouse clear + the real-`ty` `shutdown_all()` finally hold) (PASS)
- Break path 4 (feature smoke — real `ty`, ops the capstone does NOT cover + boundary): `references`
  → `[helpers.py:1:5 (decl), :5:10 (call)]`; `hover` → `def helper() -> int…`; clean-file
  `diagnostics` → `[]` (empty list, NOT `UNAVAILABLE` — "answered, nothing" preserved); `definition`
  at an out-of-range column (6:99, past EOL) → `None`, no crash (PASS)

**Acceptance criteria**
- [x] PASS — No `GEMINI_API_KEY`/no network; swaps only the LSP service seam — `env -u GEMINI_API_KEY`
      run green; conftest `_no_real_provider_key` scrubs the key suite-wide; `mocker.patch.object(
      lsp_service, "_spawn_process", …)` is the only boundary (test_lsp_capstone.py:308)
- [x] PASS — Active `lsp op=definition` auto-allows + returns `pkg/helpers.py:3:1` — asserts `_DEF_RESULT
      in returns` and `"lsp" not in permission_requests`, final resolver list == `[write,write,edit,write]`
      (lines 358-399); real client maps 0-based wire `(2,0)` → 1-based `3:1`
- [x] PASS — Passive-errors == exact base + errors-only block — `buggy_return == _BUGGY_RESULT`
      (`Wrote 'buggy.py' (…).` + `LSP diagnostics (ty) — fix these:\n  5:7  undefined name \`bar\``);
      file content on disk == body (gated write ran) (lines 364-368)
- [x] PASS — Passive-clean (write + edit) == base unchanged — `_CLEAN_WRITE_BASE`/`_CLEAN_EDIT_BASE in
      returns`, edit applied to disk (`VALUE = 2`) (lines 371-375)
- [x] PASS — Non-`.py`: enricher never runs — `_DOC_BASE in returns` AND no `textDocument/diagnostic`
      carries the `.md` URI; non-vacuous (buggy.py + clean.py URIs WERE queried, proving the real
      sync→async bridge ran) (lines 379-391)
- [x] PASS — Unavailable: base only + `lsp` ModelRetry + no crash — retry carries "code intelligence is
      unavailable", `_BUGGY_BASE in returns`, `_BUGGY_RESULT not in returns`, write ran, log replays
      (test_lsp_capstone_unavailable_degrades_gracefully)
- [x] PASS — Real renderer on every event + JSONL log writes/replays — `render_event` into a Rich buffer
      on every event (unknown kind would raise); `_DEF_RESULT`/`_ERROR_MESSAGE` render; `session_log.load
      == message_history`, header + 5 turn-appends (lines 402-413)
- [x] PASS — Optional real-`ty` test runs when present, skips otherwise — RAN here (3 passed, no skip);
      hidden-`ty` run → 2 passed/1 skipped (not failed); asserts real def resolves line 1 + real
      severity-1 error names `not_defined_name` (lines 478-503)
- [x] PASS — `make ci` green, 0 warnings — `922 passed in ~8.8s`; lock+format+lint all green

**Evidence**
```
$ env -u GEMINI_API_KEY uv run pytest tests/integration/test_lsp_capstone.py -v -rsx
test_lsp_capstone_both_channels_available PASSED      [ 33%]
test_lsp_capstone_unavailable_degrades_gracefully PASSED [ 66%]
test_lsp_capstone_real_ty_wire PASSED                 [100%]
============================== 3 passed in 2.13s ===============================

$ PATH=/usr/bin:/bin .venv/bin/python -m pytest tests/integration/test_lsp_capstone.py -rsx
SKIPPED [1] …:478: the `ty` language server binary is not on PATH
========================= 2 passed, 1 skipped in 1.68s =========================

$ make ci
uv lock --check  ·  ruff format --check (128 files)  ·  ruff check (all passed)
============================= 922 passed in 8.82s ==============================
```

**Other issues found**
- None blocking. The `lsp_fakes.py` change (callable response value) is minimal and backward-compatible
  — only `test_service.py` and the new capstone consume the fake, all 122 LSP-area units pass.
- Non-blocking follow-up (out of scope for this test-only task; pre-existing task-051 service behavior):
  `service._get_client` has no lock around the lazy first spawn, so concurrent first-calls on a fresh
  root could double-spawn. The capstone is sequential so it never trips; flagging only for awareness.

**VERDICT: PASS**

### [PA] 2026-06-27 — Acceptance Review (feature `lsp-integration`, tasks 050-056, PR #16)

**VERDICT: ACCEPT**

Reviewed the shipped code (not just task logs) from the user's perspective against the Tasks Plan
ACs and the locked design (ADR-0007). All user-facing behaviors hold:

- **Active `lsp` tool** — `ToolKind.READ_ONLY` (`registry.py:107`); auto-allows under default mode —
  the capstone proves it never reaches the human resolver (`permission_requests == [write, write,
  edit, write]`, no `lsp`). Four ops (`definition`/`references`/`hover`/`diagnostics`); returns
  1-based `path:line:column` (`tools/lsp.py:_format_location`, client converts 0↔1-based at one
  boundary). `UNAVAILABLE → ModelRetry` ("fall back to `read`/`grep`"), kept distinct from
  `None`/`[]` → plain "no X found"; every recoverable problem is a `ModelRetry`, never a crash.
- **Passive Diagnostics Enricher** — errors-only (`severity == 1`), `.py`-only (case-insensitive),
  base string kept byte-for-byte (`f"{base}\n\n{summary}"`, `files.py:_enrich`), silent on
  clean/non-`.py`/unavailable, rides the edit's approval (folded into the return site, no extra gate),
  swallows every exception. Capstone asserts buggy→block, clean write+edit→base, non-`.py`→base +
  server never queried, unavailable→base only.
- **Best-effort + lifecycle** — service maps spawn-fail/timeout/closed-pipe/malformed→`UNAVAILABLE`,
  caches the broken spawn (no retry storm); `lsp_enabled=False` short-circuits with no spawn;
  `shutdown_all` on the `run_app` exit path (`app.py:914-917`) is a no-op when unspawned, idempotent,
  and its failure is logged+swallowed so it never blocks `Decode - bye.` (no `ty server` orphan
  confirmed).
- **Config + docs** — 5 `LSP_*` settings present/env-overridable/documented (`.env.example:106-121`,
  README:158-174); `ty` in `[dependency-groups] dev`; server swappable via
  `lsp_server_command`/`args`. ADR-0007 Accepted, dated, five Nygard sections + coloured Mermaid,
  self-consistent (~230 statement lines, the stale "~120" already corrected); the four glossary terms
  appear verbatim in `src/`; AGENTS.md tree/Tech-Stack/Testing-E2E rows accurate (`build_agent` at
  `factory.py:68:5` verified).
- **Capstone** — both channels proven through the real `build_agent`/`Runner`/`render_event`/
  `SessionLog` stack (hermetic, no key/network), AND the real-`ty` wire test genuinely RAN (3 passed,
  `-rsx` shows no skip; `ty 0.0.55`). `make ci` → 922 passed, 0 warnings.

**Adjacent / out of scope (non-blocking — not REJECT reasons):**
- `service._get_client` has no lock around the lazy first spawn, so concurrent first-calls on a fresh
  root could double-spawn. Sequential usage (the `lsp` tool + the enricher both call one-at-a-time)
  never trips it; worth a one-line follow-up only if a future caller drives the service concurrently.
- `.pyi` stub files are not enriched (`.py`-only `endswith`) — consistent with the spec's
  "Python-only `.py`" wording.
- The `lsp` tool does not validate `line`/`column` positivity (`0`/negative pass through); the spec
  only requires presence, the client clamps to wire `(0,0)`, and the service is best-effort, so it
  never crashes.

User satisfaction guaranteed. Hand off to the PR Reviewer.

### [PR Reviewer] 2026-06-27 15:42 — Review

**VERDICT: NO BLOCKERS**

Reviewed the full `feat/lsp-integration` diff (PR #16) — 20 production/doc files, ~1.4k src lines
plus tests/tasks. Walked correctness, simplicity/anti-over-engineering, tests, standards (AGENTS.md),
and docs (ADR/glossary).

- Blockers: 0
- Nits: 3 (non-blocking; appended to the PR #16 description)

Notable confirmations:
- Hand-rolled wire (`client.py`) is correct — Content-Length framing, initialize/initialized
  handshake, match-by-id (decoy-skip tested), 0↔1-based conversion, timeout/malformed/closed-pipe all
  resolve to UNAVAILABLE, shutdown bounded by a 2s grace + kill. Exercised end-to-end through the
  fake-process seam in `test_service.py` AND a real-`ty 0.0.55` capstone wire test.
- Enricher (`files._enrich`) preserves the exact `Wrote …`/`Edited …` base string, errors-only,
  `.py`-only (case-insensitive), bounded `(+K more)`, swallows every failure — 15 dedicated tests.
- First `services/` entry introduces NO premature shared abstraction (verified `services/__init__.py`
  + ADR-0007 §Consequences); the per-root cache + spawn seam mirrors bash `_EXECUTOR`/web `_TRANSPORT`.
- Standards clean: `-> None` annotations throughout, logger (no prints), async subprocess I/O,
  `lsp_request_timeout_s` Field(gt=0), new env vars in `.env.example` + settings, no stray artifacts.
- Docs complete: ADR-0007 Accepted (Nygard), glossary +4 terms used verbatim in `src/`, README +
  AGENTS.md rows accurate.

**Nits (also appended to PR description):**
1. [Simplicity/Correctness — latent] `services/lsp/service.py:70 _get_client` — no lock around the
   lazy first spawn; concurrent first-calls on a fresh root would double-spawn (the loser leaks an
   orphan `ty` child `shutdown_all` can't reap). Unreachable today (ADR-0002 §7: tools sequential in
   v1; parallel read-only lands with M3). Add a per-root `asyncio.Lock` when M3 arrives — not before.
2. [Correctness/Robustness — non-hot-path] `services/lsp/client.py:156 _request` — a per-request
   timeout can cancel `_read_result` mid-frame, leaving stdout mid-message; the client stays cached
   (not `_BROKEN`), so that root can stay UNAVAILABLE for the session. No crash (best-effort holds);
   consider evicting/`_BROKEN`-marking the client on timeout so one slow request can't wedge the root.
3. [Standards/Docs — swappable-server caveat] `services/lsp/client.py:171 _read_result` never answers
   server→client requests. `ty` is fine; the advertised `pylsp` drop-in sends `workspace/configuration`
   / `client/registerCapability` — unanswered, each op blocks until `lsp_request_timeout_s` then
   degrades to UNAVAILABLE. Worth a one-line caveat in the `pylsp`-swap note / ADR-0007.

Pipeline may advance to hand-off.
