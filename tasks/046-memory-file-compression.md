---
id: 046-memory-file-compression
feature: context-compaction
status: pending
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

- [ ] **At 200 lines:** a `MEMORY.md` with `>= memory_max_lines` (200) lines is rewritten **under** 200
      lines by `compress_memory_file` via a `FunctionModel` (asserted, no network).
- [ ] **Failed/blank model call** leaves the file within the caps (drop-oldest fallback) and returns
      `False` without raising.
- [ ] **Under 200 lines** triggers **no** LLM call and the file is left byte-for-byte untouched.
- [ ] `extract_on_exit` calls `compress_memory_file` after `append_session_summary`, gated by
      `memory_compression_enabled`, and stays fully non-fatal (test patches the call to raise → exit still
      returns cleanly).
- [ ] With `memory_compression_enabled=False`, `extract_on_exit` behaves exactly as before (task-013
      memory tests green; no compression call).
- [ ] The final file always respects `memory_max_lines` AND `memory_max_bytes` (hard clamp), asserted.
- [ ] `tests/unit/decode/memory/test_extract.py` extended 1:1; `make ci` green, 0 warnings, no network.

## Out of scope
- Conversation-level micro/full compaction (042/044).
- New memory cap settings (reuse `memory_max_lines` / `memory_max_bytes`).
- Changing the on-exit one-sentence `summarize_session` write-back (still appends the dated bullet).

## Log
