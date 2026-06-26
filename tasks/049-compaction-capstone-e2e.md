---
id: 049-compaction-capstone-e2e
feature: context-compaction
status: pending
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

- [ ] Runs under `make integration-tests` / `make ci` with **no** `GEMINI_API_KEY` and **no** network.
- [ ] Micro tier asserted (event, in-memory blanking, no `compaction` line, full-fidelity log).
- [ ] Full tier asserted (event, `[summary, *tail]`, `_persisted_count == len`, `compaction` line on disk).
- [ ] Resume replays compacted history; post-compaction turn replays as `[summary, *tail, *later-turn]`.
- [ ] No orphaned `ToolReturnPart` in the compacted/replayed tail.
- [ ] `make ci` green, 0 warnings.

## Out of scope
- Re-testing pure units (042/043), memory-file compression (046 has its own tests), or the gauge (047
  is render-unit-tested).
- A live Gemini run.

## Log
