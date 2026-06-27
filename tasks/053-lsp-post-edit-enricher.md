---
id: 053-lsp-post-edit-enricher
feature: lsp-integration
status: done
---

# Passive post-edit Diagnostics Enricher folded into write/edit

Tags: `lsp`, `agent`, `data`
Depends on: #051
Blocks: #055, #056

This task implements ADR-0007 (the **passive** channel) — "the single best ROI of an LSP
integration." After a SUCCESSFUL `write`/`edit` on a `.py` file, append a compact **errors-only**
diagnostics summary to the tool's return string, so the model sees its mistakes inline and fixes them
(like opencode's "LSP errors detected in this file, please fix:"). It rides the edit's
already-granted approval — **no extra permission gate** — and is silent on clean files and whenever
the server is unavailable.

## Scope

- **Where it folds in** (`src/decode/tools/files.py`): the existing return sites
  `return f"Wrote {path!r} ({len(content)} characters)."` (files.py:292) and
  `return f"Edited {path!r} (replaced 1 occurrence)."` (files.py:347). Keep the existing base string
  **EXACT**; APPEND the diagnostics block (so existing assertions only need the appended-on-error
  case). Suggested shape: `return _enrich(base, ctx.deps.cwd, path)` where `_enrich` returns `base`
  unchanged unless there are errors to report, in which case it returns
  `f"{base}\n\n{summary}"`.
- **The enricher seam (sync, in the task-051 service):** because `write`/`edit` are **sync**
  (files.py:26-29 — sync local file I/O; pydantic-ai runs them in an anyio worker thread) and the LSP
  client is **async**, the enricher calls a **sync** helper exposed by the LSP Service (task 051),
  e.g. `diagnostics_on_edit(cwd: Path, path: str) -> str | None`. That helper bridges sync→async
  internally (e.g. `anyio.from_thread.run`, valid because the tool runs in an anyio worker thread) and
  is **best-effort**: it swallows EVERY failure (no portal, timeout, unavailable server, no errors) and
  returns `None`. files.py stays sync and dead simple; the bridge lives in one place. Unit tests patch
  this sync seam directly, so the real bridge only runs in integration/real use.
- **Behavior (all gated by `settings.lsp_enabled AND settings.lsp_diagnostics_on_edit`):**
  - Runs only after a write/edit **succeeds** (the byte write happened).
  - Runs only when `path` ends with `.py` (case-insensitive) — Python-only.
  - The helper `didOpen`s the just-written on-disk content, pulls diagnostics, filters to
    **errors only** (LSP severity == Error; ignore warnings/info/hints), and formats a compact block.
    Suggested format (server-named, bounded): a header like
    `LSP diagnostics (ty) — fix these:` then up to N (e.g. 10) lines `  line:column  message`, with a
    `(+K more)` tail if truncated.
  - Returns `None` (→ base string unchanged) when: the feature/setting is off, the file is not `.py`,
    the server is unavailable/slow/errors, OR there are **no errors** (silent on clean files).
  - **No extra permission gate** — it rides the write/edit approval already granted. **Best-effort**:
    any exception inside the enricher must NEVER change or break the write/edit return.
- Library code logs (module logger) at debug for "appended N diagnostics" / "enricher skipped
  (unavailable)"; never `print`s. Type-annotate, incl. `-> None`.

## Acceptance criteria

- [x] After a successful `write`/`edit` of a `.py` file that contains an error, the tool's return
      string is `"<exact base>\n\n<diagnostics block>"`; the base substring is byte-for-byte the
      original `Wrote …`/`Edited …` text. Unit-tested with the enricher seam faked to return a block.
- [x] A successful write/edit of a **clean** `.py` file returns the base string **unchanged** (seam
      returns `None`). Unit-tested.
- [x] A write/edit of a **non-`.py`** file never invokes the enricher seam and returns the base
      unchanged. Unit-tested.
- [x] With `lsp_enabled=False` OR `lsp_diagnostics_on_edit=False`, the enricher seam is never invoked
      and the return is the base string. Both unit-tested.
- [x] The enricher only reports **errors** — a `.py` file with only warnings returns the base
      unchanged (warnings filtered out). Unit-tested via the fake seam / a fake diagnostics set.
- [x] **Best-effort:** when the enricher seam raises, the write/edit still returns its exact base
      string (the exception is swallowed) and the file write is unaffected. Unit-tested.
- [x] No extra permission prompt is introduced for write/edit (the gate path is unchanged); a denied
      write still writes nothing and never reaches the enricher. Unit-tested / covered by existing gate
      tests staying green.
- [x] No unit test spawns a real `ty`/subprocess; `make ci` green, 0 warnings.

## User stories

### Story: The model writes Python with a bug and is told inline
1. User asks the build agent to "create `calc.py` with an add function".
2. The model calls `write` for `calc.py` with a body that references an undefined name.
3. The user approves the write (the file is created), and the tool result the model receives is
   `Wrote 'calc.py' (NN characters).` followed by `LSP diagnostics (ty) — fix these:` and the
   `line:column message` for the undefined name.
4. The model immediately issues a follow-up `edit` to fix it — without the user having to point out
   the error.

### Story: A clean edit stays quiet
1. The model `edit`s a `.py` file correctly (no new errors).
2. The tool result is exactly `Edited 'x.py' (replaced 1 occurrence).` — no diagnostics noise.

### Story: Editing a non-Python file
1. The model `write`s `README.md`.
2. The result is exactly `Wrote 'README.md' (NN characters).` — the enricher never runs for non-`.py`.

### Story: The server is down — edits still work
1. `ty` is unavailable (broken spawn cached by task 051).
2. The model `write`s a buggy `.py` file; the result is just the base `Wrote …` string (enricher
   returns `None` silently); the write succeeded. No crash, no hang.

## Out of scope
- Auto-fixing the diagnostics (the model decides what to do with them).
- Reporting warnings/info/hints in the enricher (errors-only; the `lsp` tool's `diagnostics` op is the
  full-severity query surface).
- Diagnostics for languages other than Python.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
The passive channel: errors-only `ty` diagnostics appended to a successful `.py` write/edit return
string, riding the edit's approval, best-effort and silent on clean/unavailable.

**Key decisions**
- Keep the base `Wrote …`/`Edited …` string EXACT; append only when there are errors.
- The sync write/edit reach the async client through a SYNC best-effort seam in the LSP Service
  (bridges via the anyio worker-thread portal); files.py stays sync. Unit tests patch that seam.
- Errors-only; gated by `lsp_enabled` AND `lsp_diagnostics_on_edit`; `.py`-only; no extra gate.

**Dependencies**
- #051 — the LSP Service's sync diagnostics-on-edit helper.

**User stories**
- 4 stories: buggy write surfaces inline errors, clean edit stays quiet, non-`.py` skipped,
  server-down still writes.

**Open questions**
- None — the sync/async bridge is contained in the service seam by design.

Ready for implementation.

### [SWE] 2026-06-27 14:45 — Implementation

**Files modified**
- `src/decode/tools/files.py` — folded the passive Diagnostics Enricher into the `write`/`edit`
  return sites; added `_enrich(base, cwd, path)` (gate + `.py`-only + best-effort wrapper) and
  `_format_lsp_errors(diagnostics)` (errors-only filter + bounded server-named block), plus the
  `_LSP_ERROR_SEVERITY` / `_LSP_DIAGNOSTICS_LIMIT` constants and the `lsp_service` / `Diagnostic`
  imports. The base `Wrote …`/`Edited …` strings are kept byte-for-byte; the block is appended only
  when there are errors.
- `tests/unit/decode/tools/test_files.py` — 15 new enricher tests (the seam patched, no real `ty`):
  error→block append for write+edit, clean→base, non-`.py`→seam-never-called, both gates off→
  seam-never-called, warnings-only→base, mixed→errors-only, bounded `(+K more)` truncation,
  case-insensitive `.PY`, seam-raises→exact-base+file-unaffected (write+edit), and denied-write→
  no-disk + seam-never-reached.

**Design note (contract reconciliation)**
- The task's suggested seam shape was `diagnostics_on_edit(...) -> str | None`, but the committed
  task-051 service already ships `diagnostics_on_edit(cwd, path) -> list[Diagnostic] | None` (locked
  by its own tests). To keep task 051 green and avoid touching a committed/tested contract, the
  errors-only filtering + block formatting live in the task-053 enricher, which calls the committed
  list-returning seam. Behavior matches the task/ADR exactly (errors-only, gated, `.py`-only, no extra
  gate, best-effort). The service (`service.py`) was not modified.

**Tests**
- Unit: 908 passing, 0 failing (`make pre-commit`). `test_files.py`: 70 passing (15 new).
- Integration: 9 passing — both M1 capstones + the compaction capstone stay green (they write only
  `.txt`, so the enricher short-circuits and the exact `Wrote …`/`Edited …` assertions hold).
- `make ci`: 917 passing, 0 warnings (`filterwarnings=["error"]`).

**Acceptance criteria**
- [x] `.py` write/edit with an error → `"<exact base>\n\n<block>"`, base byte-for-byte — verified by
      `test_write_py_with_error_appends_diagnostics_block` / `test_edit_py_with_error_appends_diagnostics_block`.
- [x] Clean `.py` → base unchanged — `test_write_clean_py_returns_base_unchanged` /
      `test_edit_clean_py_returns_base_unchanged`.
- [x] Non-`.py` → seam never invoked, base unchanged — `test_non_py_write_never_invokes_enricher_seam`
      / `test_non_py_edit_never_invokes_enricher_seam`.
- [x] `lsp_enabled=False` OR `lsp_diagnostics_on_edit=False` → seam never invoked —
      `test_lsp_disabled_never_invokes_enricher_seam` /
      `test_lsp_diagnostics_on_edit_disabled_never_invokes_enricher_seam`.
- [x] Errors-only (warnings filtered) — `test_warnings_only_py_returns_base_unchanged` /
      `test_only_error_severity_is_shown_when_mixed`.
- [x] Best-effort: seam raises → exact base + file unaffected —
      `test_enricher_seam_exception_is_swallowed_on_write` /
      `test_enricher_seam_exception_is_swallowed_on_edit`.
- [x] No extra gate; denied write writes nothing, never reaches enricher —
      `test_denied_py_write_never_reaches_enricher_and_writes_nothing` + existing gate tests stay green.
- [x] No unit test spawns real `ty`/subprocess; `make ci` green, 0 warnings.

**Evidence**

`make ci` (tail):
```
tests/integration/test_compaction_capstone.py .                          [ 99%]
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]

============================= 917 passed in 8.86s ==============================
```

Real end-to-end against the installed `ty 0.0.55` (sync `write`/`edit` run in an anyio worker thread,
real sync→async bridge, real `ty` subprocess — no mocks):
```
===== buggy .py write (expect Wrote … + LSP diagnostics block) =====
Wrote 'calc.py' (30 characters).

LSP diagnostics (ty) — fix these:
  2:12  Name `bar` used when not defined

===== clean .py write (expect bare Wrote …) =====
Wrote 'ok.py' (32 characters).

===== non-.py write (expect bare Wrote …) =====
Wrote 'README.md' (10 characters).

===== edit .py introducing an error (expect Edited … + block) =====
Edited 'ok.py' (replaced 1 occurrence).

LSP diagnostics (ty) — fix these:
  2:16  Name `nope` used when not defined
```

**Notes**
- Block format: header `LSP diagnostics (<lsp_server_command>) — fix these:` (server-named, so a
  swapped server shows its own name; default `ty`), then up to 10 `  line:column  message` lines and a
  `  (+K more)` tail when truncated. 1-based positions, consistent with `read`/`grep`.
- No new settings; reuses the task-050 `lsp_enabled` / `lsp_diagnostics_on_edit` gates.
- Not committed — handing off to the Tester first.

### [Tester] 2026-06-27 16:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 127 files clean; `ruff check` clean)
- Unit tests: 908 passed / 0 failed (`make pre-commit`)
- Integration tests: 9 passed / 0 failed (both M1 capstones + compaction + skills capstone green)
- `make ci` equivalent total: 917 passed
- Warnings: 0 (`filterwarnings=["error"]` in effect)

**E2E adversarial pass** (real `write`/`edit` tool funcs; seam patched for unit-level, real `ty 0.0.55`
subprocess for the smoke — 41/41 scripted checks green, 0 fail)
- Happy path: real-ty smoke through the genuine sync→async bridge in an anyio worker thread →
  buggy `.py` write returns `Wrote 'calc.py' (30 characters).\n\nLSP diagnostics (ty) — fix these:\n  2:12  Name \`bar\` used when not defined`; clean + non-`.py` silent; edit-introducing-error surfaces it (PASS)
- Break path 1 (exact-base, byte-for-byte): `.py` write/edit WITH error → `"<base>\n\n<block>"`, base
  substring is the exact `Wrote …`/`Edited …` string, file bytes on disk correct (PASS)
- Break path 2 (silent cases): clean `.py` (seam None) → base; empty diag list → base; non-`.py`
  (`.txt`/`.md`/`Makefile`/`noext`) → seam NEVER invoked; look-alikes (`.pyi`/`.py.txt`/`py`) → seam
  NEVER invoked; case-insensitive `.PY`/`.Py`/`.pY` → seam invoked + block; warnings/info/hints-only →
  base; mixed → only the severity-1 error shown, warning dropped (PASS)
- Break path 3 (both gates, independently): `lsp_enabled=False` → seam NEVER invoked → base;
  `lsp_diagnostics_on_edit=False` → seam NEVER invoked → base (PASS)
- Break path 4 (best-effort, the load-bearing property — probed hard): seam raises
  RuntimeError/ValueError/KeyError/OSError/TypeError → exact base + file bytes intact (write AND edit);
  seam returns CONTRACT-VIOLATING garbage (str / `[object()]` / `[{...}]` / int / `[None]`) →
  formatting raises inside the try, swallowed → exact base unchanged (PASS — exception/garbage never
  changes or breaks the edit return or the on-disk bytes)
- Break path 5 (gate ordering): denied write raises `ApprovalRequired` at the top → seam NEVER
  invoked → nothing on disk (PASS)
- Break path 7 (block formatting): 25 errors → exactly 10 `  line:column  message` lines + `  (+15 more)`
  tail; server-named header `LSP diagnostics (ty) — fix these:`; 1-based positions; 11th truncated;
  exactly-10 case → no tail (PASS)
- Break path 8 (no real subprocess in unit tests): the 15 new tests patch the seam; `make pre-commit`
  spawns no `ty` (PASS). Optional real-ty smoke run separately and green.

**Acceptance criteria** — all verified PASS
- [x] PASS — `.py` write/edit w/ error → `"<exact base>\n\n<block>"`, base byte-for-byte —
      `test_write_py_with_error_appends_diagnostics_block` / `..._edit_...` + adversarial 1a/1b
- [x] PASS — clean `.py` → base unchanged — `test_write_clean_py_returns_base_unchanged` /
      `test_edit_clean_py_returns_base_unchanged` (seam None and empty-list) + adversarial 2a/2b
- [x] PASS — non-`.py` → seam never invoked — `test_non_py_write/edit_never_invokes_enricher_seam`
      (`assert_not_called`) + adversarial 2c/2d
- [x] PASS — `lsp_enabled=False` OR `lsp_diagnostics_on_edit=False` → seam never invoked —
      `test_lsp_disabled_...` / `test_lsp_diagnostics_on_edit_disabled_...` + adversarial 3a/3b
- [x] PASS — errors-only (warnings filtered) — `test_warnings_only_py_returns_base_unchanged` /
      `test_only_error_severity_is_shown_when_mixed` + adversarial 2f/2g
- [x] PASS — best-effort: seam raises → exact base + file unaffected —
      `test_enricher_seam_exception_is_swallowed_on_write/edit` + adversarial 4a/4b/4c (5 exc types +
      5 garbage returns)
- [x] PASS — no extra gate; denied write writes nothing, never reaches enricher —
      `test_denied_py_write_never_reaches_enricher_and_writes_nothing` + adversarial 5a; existing gate
      tests green
- [x] PASS — no unit test spawns real `ty`/subprocess; `make ci` green, 0 warnings — 908 unit + 9
      integration, `filterwarnings=["error"]`

**Evidence**
```
$ make pre-commit   # format-check + lint-check + unit-tests
127 files already formatted
All checks passed!
============================= 908 passed in 8.14s ==============================
$ make integration-tests
============================== 9 passed in 1.82s ===============================
$ uv run python adv053.py   # real write/edit tool funcs, seam patched
TOTAL: 41 checks, 41 pass, 0 FAIL
$ uv run python realty.py   # real ty 0.0.55 via the real sync→async bridge, no mocks
Wrote 'calc.py' (30 characters).

LSP diagnostics (ty) — fix these:
  2:12  Name `bar` used when not defined
```

**Other issues found**
- None blocking. Contract reconciliation confirmed correct: `src/decode/services/lsp/service.py` is NOT
  in this task's diff — the committed task-051 `diagnostics_on_edit(cwd, path) -> list[Diagnostic] | None`
  seam is untouched; the errors-only filter + bounded formatting live in the task-053 enricher. The
  seam ALSO gates internally on `lsp_enabled`/`lsp_diagnostics_on_edit` (defense in depth); the enricher
  gates before calling it, so the AC "seam never invoked" holds at the `_enrich` boundary.
- Note (non-blocking): `.pyi` stub files are not enriched (`endswith(".py")` is False). Consistent with
  the spec's "Python-only `.py`" wording; a future follow-up could include `.pyi` if desired.

**VERDICT: PASS**
