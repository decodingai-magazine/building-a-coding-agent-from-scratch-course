---
id: 007-tools-file-write-edit
feature: m1-vanilla-agent
status: done
---

# Tools: file write + edit

## Scope
Mutating file tools (gated). `edit` uses exact-then-fuzzy matching validated against pi.

## Acceptance criteria
- [x] `write` creates/overwrites; gated before touching disk.
- [x] `edit` strips BOM + normalizes CRLF↔LF, matches exact (`indexOf`) then whitespace-normalized fuzzy, requires a UNIQUE match, restores original line endings on write.
- [x] 0 or >1 matches → model-readable `ModelRetry` ("not found" / "ambiguous, N matches" / "empty").
- [x] A denied write/edit leaves the file untouched.

## Out of scope
- Per-realpath mutation queue (unneeded under sequential M1; M3).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Normalization + error messages validated against pi.

### [SWE] 2026-06-19 20:05 — Implementation

**Files modified**
- `src/decode/tools/files.py` — added `write` (create/overwrite, parent-dir creation, atomic temp-file+`os.replace`, gated, cwd-contained) and `edit` (BOM strip + CRLF/CR→LF normalization for matching, exact-then-whitespace-fuzzy UNIQUE match, BOM/EOL restored on write, atomic); plus `WRITE_TOOL_NAME`/`EDIT_TOOL_NAME`/`FILE_TOOLS_MUTATING` and the EOL/BOM/match/atomic-write helpers.
- `src/decode/tools/registry.py` — registered `write`/`edit` in `TOOL_SPECS` as `read_only=False`.
- `tests/unit/decode/tools/test_files.py` — 26 new unit tests for write/edit + 2 through-the-agent tests.
- `tests/unit/decode/tools/test_registry.py` — extended the expected-tools / read-only-map / registration assertions to include `write`/`edit`.

**Tests**
- Unit: 192 passing, 0 failing (`make unit-tests`); the 28 new tests live in `test_files.py` (+ registry updates).
- Integration: N/A — no infra changes (stdlib `os`/`tempfile` only; `uv lock --check` clean).

**Acceptance criteria**
- [x] `write` creates/overwrites; gated before touching disk — `test_files.py::test_write_creates_a_new_file`, `::test_write_overwrites_an_existing_file`, `::test_write_creates_parent_directories`, `::test_write_requires_approval_when_not_approved`, `::test_denied_write_leaves_an_existing_file_untouched`.
- [x] `edit` strips BOM + normalizes CRLF↔LF, exact-then-whitespace-fuzzy, UNIQUE match, restores original endings — `::test_edit_replaces_an_exact_unique_match`, `::test_edit_whitespace_fuzzy_match_when_no_exact_match`, `::test_edit_preserves_crlf_line_endings`, `::test_edit_preserves_cr_line_endings`, `::test_edit_preserves_utf8_bom`, `::test_edit_preserves_bom_and_crlf_together`.
- [x] 0 / >1 / empty → distinct model-readable `ModelRetry`s — `::test_edit_no_match_returns_model_retry` (`not found`), `::test_edit_ambiguous_match_returns_model_retry_with_count` (`ambiguous` + N), `::test_edit_empty_old_string_returns_model_retry` (`empty`).
- [x] Denied write/edit leaves the file untouched — `::test_denied_write_leaves_an_existing_file_untouched`, `::test_denied_edit_leaves_the_file_untouched` (assert raw bytes unchanged / not created).
- Also covered: path-escape rejection (../, absolute, in-tree symlink) for both `write` and `edit`; through-the-agent `TestModel`/`FunctionModel` exercise forcing write/edit then approving (`::test_write_runs_through_the_agent_when_approved`, `::test_edit_runs_through_the_agent_when_approved`).

**Evidence**
```
$ make unit-tests
tests/unit/decode/tools/test_files.py .................................. [ 60%]
......................                                                   [ 71%]
tests/unit/decode/tools/test_registry.py .....                           [ 76%]
============================= 192 passed in 1.84s ==============================

$ make pre-commit   # format-check + lint-check + unit-tests
uv run ruff format --check
53 files already formatted
uv run ruff check
All checks passed!
============================= 192 passed in 1.81s ==============================

$ uv lock --check
Resolved 163 packages in 3ms
```

End-to-end (driven through the real `build_agent()` + `FunctionModel`, approving at the gate; no network):
```
WRITE perms: ['write']
WRITE file contents: 'x = 1\ny = 2\n'         # created src/app.py incl. parent dir
EDIT perms: ['edit']
EDIT file contents: 'x = 1\ny = 99\n'         # unique replacement applied
CRLF+BOM round-trip: b'\xef\xbb\xbfHELLO\r\nEARTH\r\n'   # BOM + CRLF preserved on a real file
DENIED write left file untouched: True        # gate fires before any disk write
```

**Notes**
- Design choices documented in code: `write` creates missing parent dirs (its only dir side effect); both tools write atomically via a sibling temp file + `os.replace` (a crash mid-write leaves the original intact). `edit` reads raw bytes and decodes (not `read_text`) so universal-newline translation doesn't collapse CR/CRLF before EOL detection.
- The fuzzy matcher collapses whitespace runs (`_normalize_ws`) and resolves to a single minimal span; >1 distinct minimal span → `ambiguous` (mirrors the exact-match uniqueness rule).
- No new dependencies (`os`/`tempfile` are stdlib); `.env.example`/settings untouched. ADR-0002 §7 followed; no architectural fork encountered. NOT committed — handing off to the Tester.

### [Tester] 2026-06-19 21:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`53 files already formatted`; `All checks passed!`)
- Unit tests: 192 passed / 0 failed
- Integration tests: N/A — no `tests/integration` suite (no infra changes; stdlib `os`/`tempfile` only)
- Warnings: 0 (re-ran under `pytest -W error`; `filterwarnings=["error"]` already enforces it)
- `uv lock --check`: clean (`Resolved 163 packages`)
- code-review plugin: enabled but command-only (`/code-review`); its lens (secrets, resource leaks, error handling, fd/temp-file cleanup, TOCTOU) applied manually during diff review — no defects.

**E2E adversarial pass** (unit-level RunContext + through real `build_agent()` + `FunctionModel`, approving/denying at the gate; no network)
- Happy path (through agent): write `deep/nested/app.py` (creates parent dirs) → on disk `b"x = 1\ny = 2\n"` exact; edit unique span → applied. (PASS)
- Break 1 (boundary: empty / whitespace / unicode): `write(content="")` → `b""`; unicode `"héllo 世界 😀…"` round-trips byte-for-byte; `write` msg reports char count (4) not byte count for `"abcé"` (5 bytes on disk) — cosmetic, not a defect. (PASS)
- Break 2 (fuzzy correctness): model `old_string` with tab/space mismatch edits the *correct* span; two distinct ws-normalized spans → `ambiguous, 2 matches found (after normalizing whitespace)`; too-long `old_string` → `not found`. (PASS)
- Break 3 (three distinct ModelRetry, ambiguous reports count): empty → `"old_string is empty…"`; 0 → `"old_string not found…"`; >1 exact → `"ambiguous, 3 matches found…"`. (PASS)
- Break 4 (BOM + CRLF/CR round-trip, raw-byte diff): through the agent, `b"\xef\xbb\xbfHELLO\r\nWORLD\r\n"` → `b"\xef\xbb\xbfHELLO\r\nEARTH\r\n"` — only the intended span changed, BOM + CRLF intact; CR-only and BOM-only files preserved; a bare `\n` in `new_string` on a CRLF file is re-emitted as `\r\n`. (PASS)
- Break 5 (path escape, BOTH tools): `../`, deep `a/../../escape.txt`, absolute-outside, and in-tree dir-symlink parent-dir creation all rejected with `ModelRetry`; no out-of-tree file/dir created; absolute path *inside* cwd correctly accepted (not over-rejected); legit in-tree symlink edits its real target. (PASS)
- Break 6 (denial leaves target byte-for-byte): denied write to existing file → unchanged; denied edit → unchanged; denied write to new path → not created (verified both unit-level and through the agent). (PASS)
- Break 7 (atomicity): `os.replace` forced to raise mid-write → original file intact AND no `.decode-write-*` temp file left behind, for both `write` (new path not created) and `edit`. (PASS)
- Break 8 (malformed/binary): editing an undecodable binary file → `ModelRetry`, untouched; through-the-agent ambiguous edit surfaces `ModelRetry`, loop re-prompts and terminates without crashing, file untouched. (PASS)

**Acceptance criteria**
- [x] PASS — `write` creates/overwrites; gated before touching disk — `test_files.py::test_write_creates_a_new_file`, `::test_write_overwrites_an_existing_file`, `::test_write_creates_parent_directories`, `::test_write_requires_approval_when_not_approved` (+ probe: empty/unicode content exact, denied-new-path-not-created). Gate raises `ApprovalRequired` before `_resolve_in_cwd`/any byte (`files.py:293-295`).
- [x] PASS — `edit` strips BOM + normalizes CRLF↔LF, exact-then-fuzzy, UNIQUE match, restores endings — `::test_edit_replaces_an_exact_unique_match`, `::test_edit_whitespace_fuzzy_match_when_no_exact_match`, `::test_edit_preserves_crlf_line_endings`, `::test_edit_preserves_cr_line_endings`, `::test_edit_preserves_utf8_bom`, `::test_edit_preserves_bom_and_crlf_together`; reads raw bytes + decodes (`files.py:342`) so universal-newline translation can't collapse CR before EOL detect (+ probe: BOM+CRLF agent round-trip, mixed-EOL, no-trailing-newline, new_string-LF→CRLF).
- [x] PASS — 0 / >1 / empty → distinct model-readable `ModelRetry` (ambiguous names count) — `::test_edit_no_match_returns_model_retry`, `::test_edit_ambiguous_match_returns_model_retry_with_count`, `::test_edit_empty_old_string_returns_model_retry`; all three messages captured verbatim and distinct, exact (`"3 matches"`) and fuzzy (`"2 matches"`) both report N.
- [x] PASS — a denied write/edit leaves the file untouched — `::test_denied_write_leaves_an_existing_file_untouched`, `::test_denied_edit_leaves_the_file_untouched`; raw bytes unchanged / not created (+ probe through the agent: denied write not created, denied edit byte-for-byte intact).

**Evidence**
```
$ make pre-commit
uv run ruff format --check → 53 files already formatted
uv run ruff check          → All checks passed!
uv run pytest tests/unit   → 192 passed in 1.81s

$ uv pytest tests/unit -W error -q → 192 passed in 1.81s   (0 warnings)
$ uv lock --check                  → Resolved 163 packages  (clean)

# adversarial probe (23 extra cases, run then removed): 23 passed in 1.08s
```

**Other issues found**
- (note, non-blocking) `write` confirmation message reports *character* count, not byte count (`Wrote 'u.txt' (4 characters).` for `"abcé"` = 5 bytes). Harmless and arguably more model-friendly; flagging only for awareness.
- (note, non-blocking) A non-empty whitespace-only `old_string` (e.g. `"   "`) with exactly one literal match in the file is replaced as an exact match — correct exact-match semantics, not a bug. With no exact match it normalizes to empty and returns `not found`.
- (out of scope, already noted in spec) check-then-act on `is_dir()`/`is_file()` before write is a benign TOCTOU under M1's strictly-sequential tool layer; the per-realpath mutation queue is M3.

**VERDICT: PASS**
