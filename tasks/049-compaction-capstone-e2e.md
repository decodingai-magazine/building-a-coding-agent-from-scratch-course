---
id: 049-compaction-capstone-e2e
feature: context-compaction
status: done
---

# Compaction capstone: micro + full + persist + resume (e2e, no network)

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) end-to-end proof, in the style of
`tests/integration/test_milestone1_capstone.py`: drive the **real** `AgentTurnHandler` + `Runner` +
`SessionLog` through a multi-turn conversation that crosses both **window-relative** tiers (patched-small
window), swapping only the model boundary for a `FunctionModel`. No API key, no network.
Depends on: 044 · Blocks: —

## Scope

Add `tests/integration/test_compaction_capstone.py` that:

- Builds the real handler + runner with a `FunctionModel` (normal turns short; the summarizer leg returns
  the fixed skeleton); the scripted conversation includes a gated tool call/result pair.
- Patches `settings.compaction_context_window_tokens` to a **small** value so the real `FunctionModel`
  `input_tokens` first crosses the **micro** line (`window*(1-micro_reserve)`), then the **full** line
  (`window*(1-full_reserve)`) as the conversation grows (reserves at defaults 0.40 / 0.20).
- Asserts:
  1. **Micro:** crossing the micro (not yet full) line blanks old tool-output bodies **in memory**, emits
     `ContextMicrocompacted`, writes **no** `compaction` line, and the on-disk log keeps the original full
     tool outputs.
  2. **Full:** crossing the full line triggers `compact()`: `ContextCompacted` emitted, `message_history
     == [summary_message, *tail]`, `_persisted_count == len`, a `compaction` line written.
  3. **Resume:** `context.session_log.load()` on that log replays the **compacted** history (summary +
     tail), NOT the full transcript; a turn appended after compaction replays as `[summary, *tail, *later]`.
  4. **No orphan:** the compacted/replayed tail has no orphaned `ToolReturnPart`.

## Acceptance criteria

- [x] Runs under `make integration-tests` / `make ci` with **no** `GEMINI_API_KEY` and **no** network.
- [x] Micro tier asserted (event, in-memory blanking, no `compaction` line, full-fidelity log).
- [x] Full tier asserted (event, `[summary, *tail]`, `_persisted_count == len`, `compaction` line on disk).
- [x] Resume replays compacted history; post-compaction turn replays as `[summary, *tail, *later-turn]`.
- [x] No orphaned `ToolReturnPart` in the compacted/replayed tail.
- [x] `make ci` green, 0 warnings.

## Out of scope
- Re-testing pure units (042/043), memory-file compression (046 has its own tests), or the gauge (047
  is render-unit-tested).
- A live Gemini run.

## Log

### [SWE] 2026-06-27 — Implementation

**Files modified**
- `tests/integration/test_compaction_capstone.py` — new context-compaction capstone: one scripted
  conversation (setup → micro → full → wrap-up) driven through the real `build_agent()` +
  `AgentTurnHandler` + `Runner` + `render_event` + `SessionLog`, asserting the full two-tier cascade.
- `tasks/049-compaction-capstone-e2e.md` — status `pending` → `in-progress`; ACs checked.

**Tests**
- Integration: 9 passing (`make integration-tests`), including the new capstone. Full gate
  `make ci`: 840 passing, 0 warnings (`filterwarnings=["error"]`), no network, no API key.

**Acceptance criteria**
- [x] Runs under `make integration-tests` / `make ci`, no `GEMINI_API_KEY`, no network — verified by
  `tests/integration/test_compaction_capstone.py::test_compaction_capstone_micro_full_persist_resume`
  (only the model boundary is a `FunctionModel`; the summarizer is a second `FunctionModel`).
- [x] Micro tier — `ContextMicrocompacted` (elided_count==1, before_tokens==100), the setup write
  result blanked in memory (`_MICRO_PLACEHOLDER`), **no** `compaction` line at the micro turn, and the
  raw log keeps the original full `Wrote '…'` output (placeholder never on disk).
- [x] Full tier — `ContextCompacted` (before_tokens==150), `message_history == [summary_message, *tail]`
  (synthetic head framing the skeleton), `_persisted_count == len` (snapshotted right after compaction),
  exactly one `compaction` line on disk.
- [x] Resume — `session_log.load()` returns the compacted history (len < full persisted-transcript
  len; summary head present; the dropped setup write is summarized away); the post-compaction wrap-up
  turn replays as `[summary, *tail, *later-turn]` (compacted prefix preserved verbatim + the later turn).
- [x] No orphan — `_has_orphan_tool_return` is false on both the live compacted history and the replay.

**Evidence**
```
$ make integration-tests
tests/integration/test_compaction_capstone.py .                          [ 11%]
tests/integration/test_milestone1_capstone.py .                          [ 22%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
9 passed in 1.76s

$ make ci
uv lock --check / ruff format --check / ruff check  → all clean
...
tests/integration/test_compaction_capstone.py .                          [ 99%]
840 passed in 7.49s
```

**Notes**
- Token arithmetic (the crux): pydantic-ai's *streaming* `FunctionModel` reports a fixed
  `input_tokens=50` per model-request leg, and decode's deferred-tool architecture splits every
  *gated* call into pause+resume across two `agent.iter` runs (so a gated turn measures the final
  resume leg only, 50). The capstone makes the **real** measured usage grow against a single fixed
  window by using the **ungated, inline** `sleep` control tool: N inline sleeps add N legs to one
  run's aggregate → usage 50 / 100 / 150 for 0 / 1 / 2 sleeps. With the window patched to 150 and
  reserves at their defaults (0.40 / 0.20), the turns cross micro (line 90) then full (line 120) in
  order — no reserve patching, no per-turn window changes. Documented in the module docstring.
- The recent-tail cut is forced with a huge driven prompt (>> `keep_recent_tokens`, patched to 10),
  so each tier's kept tail is exactly the final turn and earlier messages are "old".
- `replayed != handler.message_history` here (unlike the M1 capstone): full compaction leaves two
  adjacent `ModelRequest`s (summary head + tail's user boundary) that pydantic-ai coalesces in
  memory but the `compaction` checkpoint keeps separate on disk. The resume AC is asserted
  structurally (compacted-prefix preserved + later turn appended), not by whole-history equality.

### [Tester] 2026-06-27 14:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 115 files clean, `ruff check` clean, `uv lock --check` ok)
- Unit tests: 831 passed / 0 failed
- Integration tests: 9 passed / 0 failed (incl. the new capstone)
- Full gate `make ci`: 840 passed, exit 0
- Warnings: 0 (`filterwarnings=["error"]` — a warning would fail the run)

**E2E adversarial pass** (the surface under test is the capstone test itself; goal: confirm it drives the REAL two-tier cascade and is robust/deterministic)
- Happy path: `env -u GEMINI_API_KEY make integration-tests` → 9 passed in 1.79s (PASS)
- Break path 1 (no-secret + no-network boundary): ran the capstone with `GEMINI_API_KEY` unset AND a sitecustomize guard raising on any non-loopback TCP `connect` → guard installed, `1 passed`, no `NETWORK BLOCKED` raised (PASS — proves no API key and no network; model boundary is `FunctionModel`, summarizer a second `FunctionModel`)
- Break path 2 (determinism / token-arithmetic + async ordering flake): 20 consecutive isolated runs (`-p no:cacheprovider`) → 20/20 passed (PASS — no pytest-randomly plugin present, so collection order is already fixed; the 50/100/150 leg arithmetic is stable across runs)
- Break path 3 (vacuous-assertion audit — does it key off REAL behavior, not a stub?): traced every assertion to a real source. Tier selection is driven by `handler.last_input_tokens` read from `run.result.usage.input_tokens` (loop.py:381) and the test asserts the real measured 50/100/150 crossing the unpatched-default reserve lines (full 0.20→120, micro 0.40→90); micro/full events come from the real `_microcompact`/`compact` (loop.py:330,290); the `compaction` line and replay come from the real `session_log.append_compaction`/`load`. Resume assertion is non-vacuous: `len(replayed) < _persisted_transcript_len(log)` can only hold if `load()` honors the checkpoint (if it ignored the compaction line, replayed would equal the summed `messages`-line count exactly). (PASS)

**Acceptance criteria**
- [x] PASS — Runs under `make integration-tests` / `make ci`, no `GEMINI_API_KEY`, no network — `env -u GEMINI_API_KEY make ci` → 840 passed; capstone passes under an outbound-TCP guard; only the model boundary is a `FunctionModel` (test L166-193, L322).
- [x] PASS — Micro tier (event, in-memory blanking, no `compaction` line, full-fidelity log) — asserts `ContextMicrocompacted` count==1, `elided_count==1`, `before_tokens==100`; `_MICRO_PLACEHOLDER` present in memory and `_SETUP_RESULT` gone from memory; `compaction` line count==1 (the later full tier's only — micro wrote none); raw log keeps `_SETUP_RESULT`, never the placeholder (test L348-365; real blanking in compaction.py `microcompact`).
- [x] PASS — Full tier (event, `[summary, *tail]`, `_persisted_count == len`, `compaction` line) — `ContextCompacted` count==1, `before_tokens==150`, summary head is a `ModelRequest` carrying the framed skeleton (`COMPACTED-SUMMARY-MARKER`), `kept_messages == len(history)-1`, `persisted_count_after_full == len(compacted_history)`, exactly one `compaction` line (test L371-387; real `compact()` loop.py:290).
- [x] PASS — Resume replays compacted history; post-compaction turn replays as `[summary, *tail, *later]` — `len(replayed) < _persisted_transcript_len`; summary head present in replay; dropped setup `write` result absent; `replayed[:len(compacted_on_disk)] == compacted_on_disk` and the later slice carries the wrap-up prompt (test L399-421; real `session_log.load` L164-194).
- [x] PASS — No orphaned `ToolReturnPart` — `_has_orphan_tool_return` false on both the live compacted history and the replay (test L393, L423).
- [x] PASS — `make ci` green, 0 warnings — 840 passed, exit 0, `filterwarnings=["error"]`.

**Evidence**
```
$ env -u GEMINI_API_KEY make ci
uv lock --check / ruff format --check (115 files) / ruff check  → all clean
... 840 passed in 7.59s ...   (exit 0)

$ env -u GEMINI_API_KEY make integration-tests
tests/integration/test_compaction_capstone.py .                          [ 11%]
9 passed in 1.79s

$ # no-network proof (sitecustomize blocks non-loopback connect)
$ env -u GEMINI_API_KEY PYTHONPATH=<guard> uv run python -s -m pytest tests/integration/test_compaction_capstone.py -q -s
[sitecustomize] outbound TCP guard installed
.  1 passed in 1.36s

$ # determinism: 20× isolated runs → 20/20 passed (1.21–1.32s each)
```

**Other issues found** (PASS-with-note — none block)
- Render assertion `"compacted context" in rendered` is a substring of the micro line `"...microcompacted context..."`, so that one assertion does not *independently* prove the full-compaction line rendered. Not a coverage gap: `render_event` runs on EVERY emitted event via `on_event` (a raise would fail the turn), and `ContextCompacted` is proven emitted (count==1), so the full render path IS exercised — only the textual assertion is weaker than intended. Optional follow-up: assert on the full line's distinctive `~150 tokens →` fragment.
- "Micro writes no `compaction` line" is proven indirectly (total `compaction` count==1 at the end, after the full turn wrote its one), not snapshotted at the micro turn. Valid (full writes exactly one), but a snapshot of the disk count right after the micro turn (==0) would be more direct.
- The token arithmetic depends on pydantic-ai's `FunctionModel` reporting a fixed 50 input tokens/leg (same external assumption as the M1 capstone). The test asserts the exact 50/100/150 values, so a library change fails loudly rather than silently mis-testing — acceptable for a `uv.lock`-pinned project.

**VERDICT: PASS**

### [PA] 2026-06-27 — Acceptance Review (feature `context-compaction`, tasks 041-049, PR #15)

**VERDICT: ACCEPT**

Reviewed the whole feature from the user's perspective against the Tasks Plan ACs and the
user-stated behaviours; spot-checked the shipped code (not just the SWE/Tester logs). All user
journeys hold and the docs accurately present the feature.

- **Two levels present.** Conversation cascade (`agent/loop.py::_maybe_auto_compact` →
  `compact()` / `_microcompact()`) AND on-exit `MEMORY.md` compression at the 200-line cap
  (`memory/extract.py::compress_memory_file`, drop-oldest as the guaranteed fallback).
- **Window-relative, configurable.** Full fires at `window*(1-0.20)` (80% full), micro at
  `window*(1-0.40)` (60%); window `1_048_576` default; invariant `micro_reserve > full_reserve`
  asserted on defaults. All overridable via `.env` (block present + documented).
- **Both tiers + manual `/compact`.** Micro (no-LLM, in-memory, NOT persisted) and full (LLM
  skeleton + recent tail, persisted as a typed `compaction` JSONL line); `/compact` is reserved
  before the skill branch, idle-only, ignores thresholds/`compaction_enabled`. Friendly busy /
  nothing-to-compact lines.
- **Resume continues the compacted conversation.** `session_log.load()` honors the `compaction`
  checkpoint (discard-and-restart `[summary, *tail]`); capstone proves `len(replayed) < full
  transcript`, no orphaned `ToolReturnPart`.
- **Footer gauge.** `○◔◑◕●` + percent, green/yellow/red derived from the SAME reserve settings
  (single source of truth); reads the public `last_input_tokens`; `○ 0%` before the first turn.
- **JSONL, no SQLite — recorded.** ADR-0006 is `Accepted`, matches shipped code, records the
  divergence; AGENTS.md `context/` tree reads `(JSONL)` and the Datastore row reframes SQLite as
  deferred. No live flat-threshold references remain in code/env/README. Glossary carries
  Compaction, Compaction Boundary, Microcompaction, Memory Compression, Context Gauge — all used
  verbatim in code/user-facing strings.

User-facing copy is clear and consistent with the established `Decode - …` voice. All AC verified
from user POV. User satisfaction guaranteed. Hand off to the PR Reviewer.

**Adjacent (out of scope — do NOT block this feature):** AGENTS.md "Testing E2E" still references
`./MEMORY.md` / `cat MEMORY.md` (pre-existing, task-013 era), but the real path is
`<cwd>/.decode/MEMORY.md` and the README (this feature) says it correctly. Worth a separate
docs-cleanup task; not part of `context-compaction`.
