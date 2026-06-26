---
id: 044-auto-compaction-turn-handler
feature: context-compaction
status: pending
---

# Wire the two-tier window-relative cascade into AgentTurnHandler

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §3-7 — the integration. After a turn
persists at `WOULD_STOP`, compute window-relative thresholds and run the **cheapest-first cascade**.
Also exposes `compact()` (for `/compact`, task 045) and a clean `last_input_tokens` read (for the gauge,
task 047).
Depends on: 042, 043 · Blocks: 045, 047, 049

## Scope

In `src/decode/agent/loop.py::AgentTurnHandler`:

- **Capture usage per leg.** In `_run_leg`, after the run: `self._last_input_tokens =
  run.result.usage.input_tokens` (a **property** in pydantic-ai 2.0.0 — NOT `usage()`). Default `0`.
- **Public read for the TUI gauge:** add `@property last_input_tokens(self) -> int` returning
  `self._last_input_tokens` (so the footer reads a clean API, never a private attr — task 047).
- **Inject config.** Add `compaction_model_or_settings: Model | Settings | None = None` (optional seam,
  like `session_log=None`). Read window + reserves + flag from the `settings` singleton. `None` disables
  the whole cascade (headless/test runs behave exactly as today).
- **Window-relative cascade at WOULD_STOP.** Immediately **after** `_persist_turn()`, only when wired:
  - `full = should_compact(usage, window=settings.compaction_context_window_tokens,
    reserve=settings.compaction_reserve_fraction, enabled=settings.compaction_enabled)`
  - `micro = should_compact(usage, window=settings.compaction_context_window_tokens,
    reserve=settings.microcompaction_reserve_fraction, enabled=settings.compaction_enabled)`
  - **`if full → await self.compact()` elif `micro → self._microcompact()`** else no-op.
  Since `microcompaction_reserve_fraction > compaction_reserve_fraction`, the full level
  (`window*0.80`) is higher than the micro level (`window*0.60`), so checking full first is correct.
  (Microcompaction's token reduction shows up on the **next** turn's measurement — the current turn's
  `input_tokens` was already measured.)
- **`async compact(self) -> bool`** (full; also called by `/compact`):
  1. `summarize_for_compaction(self.message_history, model_or_settings=…)` → skeleton.
  2. `split = split_tail(self.message_history, keep_recent_tokens=settings.compaction_keep_recent_tokens)`.
     If summary is `None`, `split == 0`, or history is trivially short → **return `False`** (no-op).
  3. `summary_message = build_summary_message(skeleton)`; `tail = self.message_history[split:]`.
  4. If `session_log` wired → `self._session_log.append_compaction(summary_message, tail)` (swallow
     `OSError` + log).
  5. Reset: `self.message_history = [summary_message, *tail]`; `self._persisted_count =
     len(self.message_history)`.
  6. Emit `ContextCompacted`. Return `True`.
- **`_microcompact(self) -> None`** (auto-only, **in-memory ONLY**):
  - `new_messages, elided = microcompact(self.message_history,
    keep_recent_tokens=settings.compaction_keep_recent_tokens)`.
  - If `elided == 0` → no-op. Else `self.message_history = new_messages`. **Do NOT touch the session log
    and do NOT change `_persisted_count`** (elided messages are below the cursor, already persisted in
    full fidelity; the log keeps full fidelity; resume replays full history and re-microcompacts). Emit
    `ContextMicrocompacted`.
- **Successive full compactions merge for free** — the prior summary is element 0; no merge logic.
- **New events** in `entities/events.py` (frozen, slots) + `render_event` branches in `tui/render.py`,
  dim system lines:
  - `ContextCompacted(before_tokens: int, kept_messages: int)` →
    `Decode - compacted context (~{before_tokens} tokens → summary + {kept_messages} recent messages).`
  - `ContextMicrocompacted(elided_count: int, before_tokens: int)` →
    `Decode - microcompacted context (elided {elided_count} old tool output(s), ~{before_tokens} tokens).`

Wire `run_app` (`tui/app.py`) to pass `compaction_model_or_settings=settings` into the handler.

## Acceptance criteria

- [ ] `_run_leg` records `self._last_input_tokens` from `run.result.usage.input_tokens` (property);
      `last_input_tokens` property returns it; a `TestModel`/`FunctionModel` test asserts a non-zero capture.
- [ ] **Upper tier:** with `compaction_context_window_tokens` patched small so the leg's `input_tokens`
      exceeds `window*(1-compaction_reserve_fraction)`, the turn triggers `compact()`: `message_history ==
      [summary_message, *tail]`, `_persisted_count == len`, a `compaction` line is written, `ContextCompacted`
      emitted (FunctionModel returns the skeleton on the summarizer leg).
- [ ] **Middle tier:** with `input_tokens` between `window*(1-micro_reserve)` and `window*(1-full_reserve)`,
      the turn triggers `_microcompact()`: old tool-output bodies blanked **in memory**, message count
      unchanged, **no `compaction` line written**, `_persisted_count` unchanged, `ContextMicrocompacted` emitted.
- [ ] **Micro is not persisted:** after a microcompaction the on-disk log still holds the original full
      tool outputs (assert JSONL bytes); `load()` replays full history.
- [ ] **No re-persist after full compaction:** the next turn appends only its new messages.
- [ ] **Below both / disabled / 0 tokens:** no compaction of either tier (history unchanged, no event).
- [ ] `compact()` returns `False`/no-op on a trivial/short history or a `None` summary. With
      `compaction_model_or_settings is None` the handler behaves exactly as before (existing tests green).
- [ ] Both events render dim one-line system messages; `render_event` covers each (unit tests).
- [ ] `make ci` green, 0 warnings, no network; `tests/` mirror updated 1:1.

## Out of scope
- The `/compact` command (045), the gauge (047), the e2e capstone (049).
- A manual microcompaction trigger (micro is auto-only).
- Memory-file compression (046).

## Log
