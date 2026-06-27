---
id: 042-compaction-core
feature: context-compaction
status: done
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

- [x] `reserve_threshold(1_000_000, 0.20) == 800_000` and `reserve_threshold(1_000_000, 0.40) == 600_000`
      (floor); guards reject `reserve` outside `[0,1]` / non-positive window.
- [x] `should_compact` is `True` only when `enabled` and `input_tokens >= window*(1-reserve)`; `False`
      for `enabled=False`, below the level, and `input_tokens == 0` — unit-tested with a built `RunUsage`
      across both reserve fractions (full vs micro).
- [x] `summarize_for_compaction` returns the filled skeleton with all seven headings via a `FunctionModel`
      (asserted, no network); returns `None` (no raise) on empty conversation and when the call raises.
- [x] `build_summary_message` returns a `ModelRequest` with one `UserPromptPart` containing the skeleton +
      a "compacted earlier conversation" framing.
- [x] **No-split-tool-pair:** when the naive cut lands between a `ToolCallPart` and its `ToolReturnPart`,
      `split_tail` snaps to the enclosing user-turn boundary; the kept tail has no orphaned tool result.
      `split_tail` returns `0` when the whole history fits.
- [x] **Microcompaction blanks old tool outputs only:** old `ToolReturnPart`s get the placeholder, recent
      ones + non-tool parts untouched, no messages added/removed, correct `elided_count` (originals
      unmutated). **Idempotent** (second run → `elided_count == 0`). A pair straddling the boundary keeps
      both parts present.
- [x] `tests/unit/decode/context/test_compaction.py` mirrors the module 1:1; `make ci` green, 0 warnings,
      no network.

## Out of scope
- Wiring into the handler/TUI (tasks 044, 045, 047) and the JSONL `compaction` line (task 043).
- Persisting microcompaction (in-memory only — task 044 enforces).
- Touching `memory/extract.py::summarize_session`.
- File re-hydration / per-model window tables (ADR-0006 non-goals).

## Log

### [SWE] 2026-06-26 21:05 — Implementation

**Files modified**
- `src/decode/context/compaction.py` — NEW pure, network-free compaction core: `reserve_threshold`,
  `should_compact`, `summarize_for_compaction`, `build_summary_message`, `split_tail`, `microcompact`
  (+ private `_resolve_model` / `_render_transcript` / `_estimate_tokens` / `_is_user_turn_boundary`,
  `_MICRO_PLACEHOLDER`).
- `tests/unit/decode/context/test_compaction.py` — NEW, mirrors the module 1:1 (30 tests).

**Tests**
- Unit: 762 passing, 0 failing (`make pre-commit`); the 30 new tests cover every AC.
- Integration: 8 passing — `make ci` runs unit + integration green (770 total, 0 warnings,
  `uv lock --check` clean, no network — every LLM call driven by `FunctionModel` / `TestModel`).

**Acceptance criteria**
- [x] `reserve_threshold` floor (`800_000` / `600_000`) + guards (`reserve` ∉ `[0,1]`, non-positive
      `window`) — `test_reserve_threshold_*`.
- [x] `should_compact` true only when enabled + at/over level; false when disabled, below, and on the
      `input_tokens == 0` fallback; both reserve fractions — `test_should_compact_*` (built `RunUsage`).
- [x] `summarize_for_compaction` returns the seven-heading skeleton via `FunctionModel`; `None` (no
      raise, logs warning) on empty conversation, raising call, and blank output — `test_summarize_*`.
- [x] `build_summary_message` → `ModelRequest`[1×`UserPromptPart`] with skeleton + "compacted" framing —
      `test_build_summary_message_shape_and_framing`.
- [x] `split_tail` snaps a between-call/return cut back to the user-turn boundary (no orphan), returns
      `0` when all fits / `len` when nothing fits — `test_split_tail_*`.
- [x] `microcompact` blanks only old `ToolReturnPart`/`RetryPromptPart` bodies (placeholder), keeps
      recent + non-tool parts, no parts added/removed, originals unmutated, idempotent (2nd run → 0),
      pair kept present — `test_microcompact_*`.
- [x] Test module mirrors `compaction.py` 1:1; `make ci` green, 0 warnings, no network.

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check (all pass) ...
tests/integration/test_milestone1_capstone.py .
tests/integration/test_milestone3_skills_capstone.py .......
============================= 770 passed in 7.87s ==============================

$ uv run python -c "<exercise the primitives>"
full level : 800000
micro level: 600000
fire full @800k: True
fire @0 tokens : False
summary headings ok: True
head is user request framing: True | compacted in framing: True
tail boundary: 4 (user turn at 4)
elided old tool bodies: 1 | recent kept: RECENT keep me
old body blanked      : True
original NOT mutated  : True
idempotent 2nd pass   : 0
```

**Notes**
- Scope held exactly to task 042: no handler/TUI wiring, no JSONL `compaction` line, no touch to
  `memory/extract.py::summarize_session`. `context/__init__.py` left unchanged (no consumer yet —
  downstream tasks import `decode.context.compaction` directly, matching the `memory/extract` pattern).
- `split_tail` snaps the cut **backward** to the enclosing user-turn boundary (per "snapped back to a
  user-turn boundary" / "the enclosing user-turn boundary"), so it may keep slightly more than the raw
  `chars≈/4` budget — the documented, bounded trade-off for never orphaning a tool result. The estimate
  is tail-sizing only, never the trigger.
- `microcompact` only blanks `ModelRequest`-side parts (`ToolReturnPart`/`RetryPromptPart`); `ToolCallPart`
  lives on the response side and is left intact, so a pair is never orphaned.
- Verified pydantic-ai 2.0.0 facts used as given: `RunUsage(input_tokens=...)`, non-frozen tool parts
  rebuilt via `dataclasses.replace`, `FunctionModel` summarizer + a raising `FunctionModel` for the
  failure path. DO NOT COMMIT — handing off to the Tester.

### [Tester] 2026-06-26 22:12 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 114 files clean; `ruff check` all pass)
- Unit tests: 762 passed / 0 failed (`make pre-commit`); the 30 new compaction tests all pass
- Integration tests: 8 passed / 0 failed (`make integration-tests`)
- Full gate `make ci` (`uv lock --check` + format + lint + unit + integration): 770 passed
- Warnings: 0 (`filterwarnings=["error"]` active; no network — every model call is `FunctionModel`/`TestModel`)

**E2E adversarial pass** (64 Tester-authored checks, independent of the SWE suite — all PASS)
- Happy path: `summarize_for_compaction([user, assistant], FunctionModel(fill))` → filled skeleton with all 7 headings; `microcompact(straddle, keep_recent_tokens=200)` → `(new, 1)` old body blanked, recent verbatim. (PASS)
- Break path 1 (no-split-tool-pair, 5 interleavings): naive cut mid-pair (ToolCall/ToolReturn), multi-pair turn, parallel tool calls (1 response → 2 calls / 1 request → 2 returns), cut landing on a `RetryPromptPart`, and a degenerate history not starting with a user msg → `split_tail` ALWAYS snapped to a `UserPromptPart` boundary or degraded to 0/len; kept tail never started on an orphaned `ToolReturnPart`/`RetryPromptPart`. (PASS)
- Break path 2 (microcompact mutation/idempotency/never-removes): deep-copy compare confirms originals never mutated, changed msgs are fresh objects, unchanged recent msgs keep identity; 2nd pass elides 0 and returns the input list unchanged; straddling pair keeps BOTH parts; old `ToolCallPart` (response side) left intact; mixed old `ModelRequest` (UserPrompt + ToolReturn) blanks only the tool part; non-str/structured tool content blanked without crash; custom placeholder honored; empty list → `([], 0)`. (PASS)
- Break path 3 (boundary/float landmines): `should_compact` fires at `==` threshold (full & micro), not one below, suppressed when disabled even at full window, no-fire on `input_tokens==0` AND negative; `reserve_threshold` floor exact at 0.10/0.20/0.30/0.40 (`1_000_000*(1-0.40)==600000.0`, no float drift), inclusive `[0,1]`, rejects out-of-range reserve and non-positive window. (PASS)
- Break path 4 (summarizer failure modes): `None` (no raise) on empty conv (no model call), all-blank transcript (no model call), raising `FunctionModel` (swallowed + logged at warning with `exc_info`), and blank model output; works on a tool-only history (transcript carries `[tool call/result: name]` notes). (PASS)

**Acceptance criteria**
- [x] PASS — `reserve_threshold` floor (`800_000`/`600_000`) + guards — `test_reserve_threshold_full_and_micro_fractions`, `_floors`, `_accepts_the_inclusive_bounds`, `_rejects_reserve_outside_unit_interval`, `_rejects_non_positive_window` (`test_compaction.py:117-143`) + adversarial float-precision probe.
- [x] PASS — `should_compact` true only when enabled + at/over level; false disabled/below/`==0`; both fractions — `test_should_compact_*` (`test_compaction.py:151-177`, built `RunUsage`) + adversarial `==`/negative/reserve=0 probes.
- [x] PASS — `summarize_for_compaction` 7-heading skeleton via `FunctionModel`, `None` (no raise) on empty/raise/blank — `test_summarize_for_compaction_*` (`test_compaction.py:185-280`).
- [x] PASS — `build_summary_message` → `ModelRequest`[1×`UserPromptPart`] with skeleton + "compacted" framing — `test_build_summary_message_shape_and_framing` (`test_compaction.py:288`).
- [x] PASS — No-split-tool-pair: snaps to user-turn boundary, no orphan; `0` when all fits — `test_split_tail_snaps_back_to_a_user_turn_boundary_no_orphan`, `_returns_zero_when_everything_fits`, `_returns_len_when_nothing_fits` (`test_compaction.py:307-349`) + 5 adversarial interleavings.
- [x] PASS — microcompact blanks old tool outputs only, recent/non-tool untouched, no add/remove, correct `elided_count`, originals unmutated, idempotent, straddling pair kept — `test_microcompact_*` (`test_compaction.py:373-485`) + adversarial mutation/identity/structured-content probes.
- [x] PASS — Test module mirrors `compaction.py` 1:1 (`tests/unit/decode/context/test_compaction.py`); `make ci` green (770 passed), 0 warnings, no network (`FunctionModel`/`TestModel` only).

**Evidence**
```
$ make ci
============================= 770 passed in 8.06s ==============================

$ uv run pytest tests/unit/decode/context/test_compaction.py -v
============================== 30 passed in 1.02s ==============================

$ uv run python scratchpad/adversarial.py
================ SUMMARY ================
total=64 pass=64 fail=0
```

**Other issues found** (non-blocking)
- Commit hygiene: `docs/architecture.png` is modified in the working tree but is NOT part of task 042
  (not in the SWE report; last touched by the `feat: Scaffold` commit). When committing 042, stage
  ONLY `src/decode/context/compaction.py`, `tests/unit/decode/context/test_compaction.py`, and this
  task file — do not `git add -A` the stray PNG.
- Minor (follow-up, not a defect): `should_compact` would raise `ValueError` (via `reserve_threshold`)
  if handed a reserve outside `[0,1]` while `enabled` and `input_tokens>0`; safe in practice because
  reserve comes from validated settings. At a pathological `keep_recent_tokens` smaller than a single
  message, `split_tail` returns `len(messages)`, so `microcompact` would blank even the most-recent
  tool body — unreachable with the default `20_000` budget. Both are documented/bounded; noting only.

**VERDICT: PASS**
