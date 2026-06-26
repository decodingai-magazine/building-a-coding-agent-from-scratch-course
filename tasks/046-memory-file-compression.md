---
id: 046-memory-file-compression
feature: context-compaction
status: done
---

# On-exit MEMORY.md LLM compression at the 200-line cap (drop-oldest as fallback)

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §8 — the **second level**. Today
`memory/extract.py::append_session_summary` keeps `MEMORY.md` under `settings.memory_max_lines` (**200**)
/ `memory_max_bytes` (25 000) by **dropping the oldest lines**. When the file reaches **200 lines
(`memory_max_lines`)**, replace that lossy drop with **one cheap LLM call** that dedupes/merges the
highest-signal facts and rewrites the file under the caps — keeping drop-oldest as the guaranteed
fallback/ceiling. Reuses the `_resolve_model` pattern so tests use `FunctionModel`/`TestModel`, no network.
Depends on: 041 · Blocks: 048

## Scope

In `src/decode/memory/extract.py` (or a sibling in the `memory` module):

- **`compress_memory_file(cwd, *, model_or_settings) -> bool`** — read `harness_memory_path(cwd)`. If the
  file is missing or `len(lines) < settings.memory_max_lines` (i.e. **fewer than 200 lines**) → return
  `False` (no LLM call; file untouched). Otherwise — the file has **reached the 200-line cap** — make ONE
  LLM call (one-shot `Agent`, model from `_resolve_model`) to compress the dated memory bullets into a
  shorter, **deduped, high-signal** version (merge duplicates, keep durable facts/decisions, drop
  ephemera, preserve dated-bullet form, target well under the cap). On a non-blank result: write back,
  then `clip_lines_to_budget(result_lines, max_lines=…, max_bytes=…, keep="tail")` as the **hard ceiling**;
  return `True`. On failure/blank: leave the file as the drop-oldest clamp already left it; return `False`.
  **Fully non-fatal** (wrapped, never raises), mirroring `extract_on_exit`.
- **Hook in `extract_on_exit`** — after the existing `append_session_summary(...)` (which keeps its
  drop-oldest clamp as the always-on ceiling/fallback): if `settings.memory_compression_enabled`,
  `await compress_memory_file(cwd, model_or_settings=settings)`. Because `append_session_summary` clamps
  to the cap, the file sits at exactly 200 lines precisely when it was full, so the `>= memory_max_lines`
  check fires then and frees headroom. Keep inside `extract_on_exit`'s guard so it never blocks exit.
  When `memory_compression_enabled=False`, behaviour is unchanged (drop-oldest only).

Drop-oldest `clip_lines_to_budget(keep="tail")` thus plays two roles: the always-on ceiling inside
`append_session_summary`, and the fallback when compression fails/returns blank — so the cap is ALWAYS
enforced, even with no/failed model.

## Acceptance criteria

- [x] **At 200 lines:** a `MEMORY.md` with `>= memory_max_lines` (200) lines is rewritten **under** 200
      lines by `compress_memory_file` via a `FunctionModel` (asserted, no network).
- [x] **Failed/blank model call** leaves the file within the caps (drop-oldest fallback) and returns
      `False` without raising.
- [x] **Under 200 lines** triggers **no** LLM call and the file is left byte-for-byte untouched.
- [x] `extract_on_exit` calls `compress_memory_file` after `append_session_summary`, gated by
      `memory_compression_enabled`, and stays fully non-fatal (test patches the call to raise → exit still
      returns cleanly).
- [x] With `memory_compression_enabled=False`, `extract_on_exit` behaves exactly as before (task-013
      memory tests green; no compression call).
- [x] The final file always respects `memory_max_lines` AND `memory_max_bytes` (hard clamp), asserted.
- [x] `tests/unit/decode/memory/test_extract.py` extended 1:1; `make ci` green, 0 warnings, no network.

## Out of scope
- Conversation-level micro/full compaction (042/044).
- New memory cap settings (reuse `memory_max_lines` / `memory_max_bytes`).
- Changing the on-exit one-sentence `summarize_session` write-back (still appends the dated bullet).

## Log

### [SWE] 2026-06-26 23:10 — Implementation

**Files modified**
- `src/decode/memory/extract.py` — added `compress_memory_file(cwd, *, model_or_settings) -> bool` (second-level on-exit LLM compressor at the 200-line cap, fully non-fatal, hard-clamped by `clip_lines_to_budget(keep="tail")`); added `_COMPRESS_INSTRUCTIONS`; hooked the call into `extract_on_exit` after `append_session_summary`, gated by `settings.memory_compression_enabled`, inside the existing guard; refreshed the module + function docstrings.
- `tests/unit/decode/memory/test_extract.py` — extended 1:1: 3 new `extract_on_exit` hook tests + 8 new `compress_memory_file` tests (FunctionModel/TestModel, no network).

**Tests**
- Unit: 807 passing, 0 failing (`make pre-commit`); the `test_extract.py` module alone is 31 passing.
- Integration: 8 passing (`make ci` → 815 total, capstones included).
- No new deps; no settings added (reused `memory_max_lines` / `memory_max_bytes` / `memory_compression_enabled` from task 041).

**Acceptance criteria**
- [x] At 200 lines → rewritten under cap via FunctionModel — `test_compress_rewrites_an_at_cap_file_under_the_line_cap` (+ `test_compress_feeds_the_existing_bullets_to_the_model`).
- [x] Failed/blank call → within caps, returns False, no raise — `test_compress_falls_back_to_drop_oldest_when_the_call_fails` / `..._is_blank`.
- [x] Under 200 lines → no LLM call, byte-for-byte untouched — `test_compress_is_a_noop_under_the_line_cap_no_call_and_file_untouched` (+ `..._when_the_file_is_missing`).
- [x] `extract_on_exit` calls compress after append, gated, fully non-fatal — `test_extract_on_exit_compresses_after_append_when_enabled`, `..._never_raises_when_compression_blows_up`.
- [x] `memory_compression_enabled=False` → unchanged, no compression call — `test_extract_on_exit_skips_compression_when_disabled` (and all task-013 tests green).
- [x] Final file respects `memory_max_lines` AND `memory_max_bytes` (hard clamp) — `test_compress_hard_clamps_an_oversized_model_result_by_line_cap` / `..._by_byte_cap`.
- [x] Test module extended 1:1; `make ci` green, 0 warnings (`filterwarnings=["error"]`), no network.

**Evidence**
```
$ uv run pytest tests/unit/decode/memory/test_extract.py -q
...............................                                          [100%]
31 passed in 1.35s

$ make ci
... 815 passed in 7.74s

# end-to-end against a real on-disk MEMORY.md (stub FunctionModel, no network):
[over-cap]   before=200 lines  -> changed=True  after=4 lines
             (4 deduped dated bullets written back)
[under-cap]  changed=False  untouched=True
[missing]    changed=False  exists=False
```

**Notes**
- Drop-oldest stays the always-on ceiling/fallback: `compress_memory_file` writes only on a non-blank LLM result and re-clamps with `clip_lines_to_budget(keep="tail")`; on failure/blank/under-cap it leaves the file exactly as `append_session_summary` clamped it.
- The capstone integration test still makes no network call: after append the file is 1 line (< 200), so compression returns early before building any model.
- Not committed — handing off to the Tester per role workflow.

### [Tester] 2026-06-26 23:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` → exit 0)
- Unit tests: 807 passed / 0 failed (`make pre-commit`); `test_extract.py` module = 31 passed
- Integration tests: 8 passed / 0 failed
- Full `make ci` (lock-check + format + lint + full suite): 815 passed, **0 warnings** (`filterwarnings=["error"]`), no network
- Diff scoped to 3 task-046 files (extract.py, test_extract.py, task md); `docs/architecture.png` untouched; no `print()` in library code

**E2E adversarial pass** (real on-disk MEMORY.md, FunctionModel, no network — 32 checks, 0 failed)
- Happy path: file AT 200-line cap + FunctionModel returning 5 deduped bullets → `changed=True`, file 200→5 lines, one model call (PASS)
- Break 1 (boundary: 199 lines): `compress_memory_file` → no model call, returns False, byte-for-byte untouched (PASS); 200 → one call, rewritten under cap (PASS); 201 → one call (confirms `>=` not `==` semantics) (PASS)
- Break 2 (oversized model result, line cap): model returns 700 lines → hard-clamped to 200 lines / 5400 bytes, both caps held (PASS)
- Break 3 (oversized + multibyte unicode, byte cap): model returns fat `é`-laden lines → clamped to 24068 bytes ≤ 25000 AND ≤ 200 lines, file re-reads as valid UTF-8 (no split multibyte char) (PASS)
- Break 4 (failure: model raises): `RuntimeError` swallowed (logged at warning w/ exc_info), returns False, file untouched & within caps, never raises (PASS)
- Break 5 (blank/whitespace results: spaces / newlines / tabs+nl): each → returns False, file untouched (PASS)
- Break 6 (state: concurrent compressions via `asyncio.gather`): both return True, last-writer-wins, file valid & within caps (PASS)
- Break 7 (`extract_on_exit` non-fatal): patched `compress_memory_file` to raise → exit returns cleanly, already-appended dated bullet survives (PASS)
- Break 8 (gated OFF: `memory_compression_enabled=False`): compress never called, dated bullet still appended (drop-oldest-only path intact) (PASS)
- Break 9 (direct call w/ settings + empty key): `_resolve_model` UserError swallowed, returns False, file untouched (PASS)

**Acceptance criteria**
- [x] PASS — At 200 lines: file with `>= memory_max_lines` rewritten under 200 via FunctionModel — `test_compress_rewrites_an_at_cap_file_under_the_line_cap`; adversarial happy path 200→5 lines
- [x] PASS — Failed/blank call leaves file within caps, returns False, no raise — `test_compress_falls_back_to_drop_oldest_when_the_call_fails` / `..._is_blank`; adversarial breaks 4 & 5
- [x] PASS — Under 200 lines: no LLM call, byte-for-byte untouched — `test_compress_is_a_noop_under_the_line_cap_no_call_and_file_untouched`; adversarial break 1 (199 → 0 calls)
- [x] PASS — `extract_on_exit` calls compress after append, gated, fully non-fatal — `test_extract_on_exit_compresses_after_append_when_enabled` (order asserts `["append","compress"]`), `..._never_raises_when_compression_blows_up`; adversarial break 7. Hook at `extract.py:244-245`
- [x] PASS — `memory_compression_enabled=False` → unchanged, no compression call — `test_extract_on_exit_skips_compression_when_disabled`; adversarial break 8; all task-013 memory tests green (55 passed in `tests/unit/decode/memory/`)
- [x] PASS — Final file respects `memory_max_lines` AND `memory_max_bytes` (hard clamp) — `test_compress_hard_clamps_an_oversized_model_result_by_line_cap` / `..._by_byte_cap`; adversarial breaks 2 & 3 (incl. multibyte UTF-8 safety)
- [x] PASS — Test module extended 1:1; `make ci` green, 0 warnings, no network — 815 passed, 0 warnings

**`summarize_session` unchanged:** confirmed via `git diff` — only the module docstring, `_COMPRESS_INSTRUCTIONS`, `compress_memory_file`, and the `extract_on_exit` hook/docstring changed; `summarize_session`, `_render_transcript`, `_resolve_model` are byte-identical. The one-sentence dated-bullet write-back still runs (verified in adversarial breaks 7 & 8: bullet appended before compress).

**Evidence**
```
$ make ci
... 815 passed in 7.62s   (warnings grep: 0)

$ uv run python scratchpad/adv046.py
settings: memory_max_lines=200 memory_max_bytes=25000 memory_compression_enabled=True
[PASS] HAPPY: full file deduped under cap  (before=200 -> after=5, call=1)
... 32 checks, 0 failed
```

**Other issues found** (non-blocking — PASS with note)
- A misbehaving model that emits an interior whitespace-only line has it written through verbatim (e.g. `'- a\n   \n- b\n'`). The drop-oldest clamp and the `assemble_memory` cap both tolerate it, and it's not in the ACs — cosmetic only. Worth a follow-up only if MEMORY.md prettiness ever matters.
- Trigger counts raw `splitlines()` including blank lines, so 198 bullets + 2 trailing blanks fires at 200. Consistent with the same line-counting used by `append_session_summary` / `clip_lines_to_budget`, so this is internally coherent, not a defect.

**VERDICT: PASS**
