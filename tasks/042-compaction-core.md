---
id: 042-compaction-core
feature: context-compaction
status: pending
---

# Conversation compaction core: window-relative full primitives + microcompaction

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §3-5. A new
`src/decode/context/compaction.py` holding the **pure, network-free** primitives the handler (task 044)
and `/compact` (task 045) orchestrate — both the **full** (LLM) and **micro** (no-LLM) tiers. The full
summarizer is NEW and fuller than `memory/extract.py::summarize_session` (which stays as-is) but reuses
its `_resolve_model` pattern (concrete `Model` for tests / `Settings`→Gemini for prod) and transcript
style, so tests stay network-free with `FunctionModel`/`TestModel`.
Depends on: 041 · Blocks: 043, 044

## Scope

Add `src/decode/context/compaction.py` with these units, each independently unit-tested:

**Window-relative trigger:**

1. **`reserve_threshold(window, reserve) -> int`** — the token level a tier fires at:
   `int(window * (1 - reserve))` (floor), guarding `0 <= reserve <= 1` and `window > 0`.
2. **`should_compact(usage, *, window, reserve, enabled) -> bool`** — returns `True` only when
   `enabled` AND `usage.input_tokens > 0` AND `usage.input_tokens >= reserve_threshold(window, reserve)`.
   **Safe fallback:** `input_tokens == 0` (unpopulated) → `False` (no window math, don't fire). The SAME
   predicate serves both tiers — the handler passes the full reserve for the full tier and the (larger)
   micro reserve for the micro tier.

**Full-compaction (LLM) primitives:**

3. **`summarize_for_compaction(messages, *, model_or_settings) -> str | None`** — one LLM call producing
   the **fixed Markdown skeleton** (ADR-0006 §4): `# Conversation summary` → `## Goal` →
   `## Constraints & Preferences` → `## Progress` (Done / In Progress / Blocked) → `## Key Decisions` →
   `## Next Steps` → `## Critical Context`. Renders `messages` to a plain-text transcript (reuse the
   `memory/extract.py` role-prefixed style + a brief note of tool activity). Returns the filled skeleton,
   or `None` when nothing to summarize or the call fails (swallowed + logged — never raises).
   `model_or_settings` resolves via a `_resolve_model` mirroring `memory/extract.py`.
4. **`build_summary_message(skeleton) -> ModelRequest`** — `ModelRequest(parts=[UserPromptPart(content=...)])`
   (the shape `_append_steering` builds), framed as a summary of the earlier (compacted) conversation.
5. **`split_tail(messages, *, keep_recent_tokens) -> int`** — index where the kept tail begins: the
   largest tail whose **estimated** tokens fit `keep_recent_tokens`, **snapped back to a turn boundary**
   so the tail starts at a user-turn `ModelRequest` (containing a `UserPromptPart`) and never at an
   orphaned `ToolReturnPart`/`RetryPromptPart`. Per-message size uses a coarse, documented `chars≈/4`
   estimate (ADR-0006 §Consequences — **tail sizing only, never the trigger**). Returns `len(messages)`
   when nothing should be kept and `0` when everything fits.

**Microcompaction (no-LLM) primitive:**

6. **`microcompact(messages, *, keep_recent_tokens, placeholder=_MICRO_PLACEHOLDER) -> tuple[list[ModelMessage], int]`**
   — reuse `split_tail` to delimit "old," then for every message older than that boundary, **blank the
   body** of each `ToolReturnPart` / `RetryPromptPart` by rebuilding it with
   `dataclasses.replace(part, content=placeholder)` (`_MICRO_PLACEHOLDER = "[tool output elided by
   microcompaction]"`) and rebuild the enclosing message with `dataclasses.replace(message, parts=[...])`
   — never mutate shared objects. It only blanks **content**, never removes a message/part, so it can
   never orphan a tool-call/result pair. **Idempotent:** already-placeholder parts are skipped/uncounted.
   Returns `(new_messages, elided_count)`; `(messages, 0)` when nothing was elided.

**pydantic-ai 2.0.0 API note (verified):** usage is a **property** — `run.result.usage` returns a
`RunUsage` with `.input_tokens` / `.output_tokens` (+ `.total_tokens`). `run.result.usage()` raises
`TypeError`. `TestModel` populates `input_tokens` (56 for a short prompt). `ToolReturnPart` /
`RetryPromptPart` are **not frozen** and both carry `content`; rebuild via `dataclasses.replace`.

## Acceptance criteria

- [ ] `reserve_threshold(1_000_000, 0.20) == 800_000` and `reserve_threshold(1_000_000, 0.40) == 600_000`
      (floor); guards reject `reserve` outside `[0,1]` / non-positive window.
- [ ] `should_compact` is `True` only when `enabled` and `input_tokens >= window*(1-reserve)`; `False`
      for `enabled=False`, below the level, and `input_tokens == 0` — unit-tested with a built `RunUsage`
      across both reserve fractions (full vs micro).
- [ ] `summarize_for_compaction` returns the filled skeleton with all seven headings via a `FunctionModel`
      (asserted, no network); returns `None` (no raise) on empty conversation and when the call raises.
- [ ] `build_summary_message` returns a `ModelRequest` with one `UserPromptPart` containing the skeleton +
      a "compacted earlier conversation" framing.
- [ ] **No-split-tool-pair:** when the naive cut lands between a `ToolCallPart` and its `ToolReturnPart`,
      `split_tail` snaps to the enclosing user-turn boundary; the kept tail has no orphaned tool result.
      `split_tail` returns `0` when the whole history fits.
- [ ] **Microcompaction blanks old tool outputs only:** old `ToolReturnPart`s get the placeholder, recent
      ones + non-tool parts untouched, no messages added/removed, correct `elided_count` (originals
      unmutated). **Idempotent** (second run → `elided_count == 0`). A pair straddling the boundary keeps
      both parts present.
- [ ] `tests/unit/decode/context/test_compaction.py` mirrors the module 1:1; `make ci` green, 0 warnings,
      no network.

## Out of scope
- Wiring into the handler/TUI (tasks 044, 045, 047) and the JSONL `compaction` line (task 043).
- Persisting microcompaction (in-memory only — task 044 enforces).
- Touching `memory/extract.py::summarize_session`.
- File re-hydration / per-model window tables (ADR-0006 non-goals).

## Log
