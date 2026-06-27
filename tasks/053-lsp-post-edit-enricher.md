---
id: 053-lsp-post-edit-enricher
feature: lsp-integration
status: pending
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

- [ ] After a successful `write`/`edit` of a `.py` file that contains an error, the tool's return
      string is `"<exact base>\n\n<diagnostics block>"`; the base substring is byte-for-byte the
      original `Wrote …`/`Edited …` text. Unit-tested with the enricher seam faked to return a block.
- [ ] A successful write/edit of a **clean** `.py` file returns the base string **unchanged** (seam
      returns `None`). Unit-tested.
- [ ] A write/edit of a **non-`.py`** file never invokes the enricher seam and returns the base
      unchanged. Unit-tested.
- [ ] With `lsp_enabled=False` OR `lsp_diagnostics_on_edit=False`, the enricher seam is never invoked
      and the return is the base string. Both unit-tested.
- [ ] The enricher only reports **errors** — a `.py` file with only warnings returns the base
      unchanged (warnings filtered out). Unit-tested via the fake seam / a fake diagnostics set.
- [ ] **Best-effort:** when the enricher seam raises, the write/edit still returns its exact base
      string (the exception is swallowed) and the file write is unaffected. Unit-tested.
- [ ] No extra permission prompt is introduced for write/edit (the gate path is unchanged); a denied
      write still writes nothing and never reaches the enricher. Unit-tested / covered by existing gate
      tests staying green.
- [ ] No unit test spawns a real `ty`/subprocess; `make ci` green, 0 warnings.

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
