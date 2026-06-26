---
id: 044-auto-compaction-turn-handler
feature: context-compaction
status: done
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

- [x] `_run_leg` records `self._last_input_tokens` from `run.result.usage.input_tokens` (property);
      `last_input_tokens` property returns it; a `TestModel`/`FunctionModel` test asserts a non-zero capture.
- [x] **Upper tier:** with `compaction_context_window_tokens` patched small so the leg's `input_tokens`
      exceeds `window*(1-compaction_reserve_fraction)`, the turn triggers `compact()`: `message_history ==
      [summary_message, *tail]`, `_persisted_count == len`, a `compaction` line is written, `ContextCompacted`
      emitted (FunctionModel returns the skeleton on the summarizer leg).
- [x] **Middle tier:** with `input_tokens` between `window*(1-micro_reserve)` and `window*(1-full_reserve)`,
      the turn triggers `_microcompact()`: old tool-output bodies blanked **in memory**, message count
      unchanged, **no `compaction` line written**, `_persisted_count` unchanged, `ContextMicrocompacted` emitted.
- [x] **Micro is not persisted:** after a microcompaction the on-disk log still holds the original full
      tool outputs (assert JSONL bytes); `load()` replays full history.
- [x] **No re-persist after full compaction:** the next turn appends only its new messages.
- [x] **Below both / disabled / 0 tokens:** no compaction of either tier (history unchanged, no event).
- [x] `compact()` returns `False`/no-op on a trivial/short history or a `None` summary. With
      `compaction_model_or_settings is None` the handler behaves exactly as before (existing tests green).
- [x] Both events render dim one-line system messages; `render_event` covers each (unit tests).
- [x] `make ci` green, 0 warnings, no network; `tests/` mirror updated 1:1.

## Out of scope
- The `/compact` command (045), the gauge (047), the e2e capstone (049).
- A manual microcompaction trigger (micro is auto-only).
- Memory-file compression (046).

## Log

### [SWE] 2026-06-26 20:05 — Implementation

**Files modified**
- `src/decode/agent/loop.py` — `AgentTurnHandler`: per-leg `input_tokens` capture + `last_input_tokens`
  property; `compaction_model_or_settings` seam; `_maybe_auto_compact` (window-relative two-tier cascade at
  WOULD_STOP); `compact()` (full, LLM + checkpoint) and `_microcompact()` (no-LLM, in-memory only); a
  persisted-count clamp so pydantic-ai's coalescing of the summary head + tail-boundary `ModelRequest`s can't
  drop the next turn's first new message from the log.
- `src/decode/entities/events.py` — new frozen/slotted `ContextCompacted` / `ContextMicrocompacted` events
  (added to the `Event` union).
- `src/decode/tui/render.py` — `render_event` branches rendering both as dim one-line system messages.
- `src/decode/tui/app.py` — `run_app` wires `compaction_model_or_settings=settings` into the handler (prod cascade live).
- `tests/unit/decode/agent/test_loop.py` — 11 cascade tests (per-leg capture, both tiers, micro-not-persisted,
  no-repersist, below/disabled/zero-tokens, compact no-ops, `None`-seam regression).
- `tests/unit/decode/tui/test_render.py`, `tests/unit/decode/entities/test_events.py` — render + union/discriminant coverage for the two new events.

**Tests**
- Unit: 785 passing, 0 failing (`make pre-commit`); loop module 34 passing.
- Integration: 8 passing (capstones unaffected — default 1M window never crosses a threshold in tests).
- `make ci`: 793 passing, 0 warnings, no network, `uv lock --check` clean.

**Acceptance criteria** — all `[x]`; each verified by a named test, e.g.:
- per-leg capture + property → `test_run_leg_captures_input_tokens_and_property_exposes_it`
- upper tier → `test_full_tier_compacts_through_the_turn`
- middle tier → `test_middle_tier_microcompacts_through_the_turn`
- micro not persisted → `test_microcompaction_keeps_full_fidelity_on_disk`
- no re-persist → `test_no_repersist_after_full_compaction`
- below/disabled/zero → `test_below_both_tiers_is_a_no_op` / `test_disabled_flag_skips_the_cascade` / `test_zero_tokens_never_compacts`
- compact no-ops + `None` seam → `test_compact_returns_false_on_trivial_history` / `..._when_summary_is_none` / `test_none_seam_disables_cascade_even_with_a_tiny_window`
- render → `test_render_context_compacted_is_a_dim_one_liner` / `test_render_context_microcompacted_is_a_dim_one_liner`

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check ...
============================= 793 passed in 9.17s ==============================

$ uv run python <e2e: production build_agent() + AgentTurnHandler, FunctionModel, no network>
=== FULL COMPACTION — what the user sees ===
Decode - compacted context (~50 tokens → summary + 2 recent messages).
  history after compaction: 3 messages (head = summary)
  on-disk line types: ['session', 'messages', 'compaction']
=== MICROCOMPACTION — what the user sees ===
Decode - microcompacted context (elided 1 old tool output(s), ~50 tokens).
  in-memory tool body blanked: True
  on-disk has NO compaction line: True
```

**Notes**
- Tier-band tests are deterministic without network or fragile token math: a streaming `FunctionModel`
  reports a fixed `input_tokens=50` (pydantic-ai estimates the request from an empty list), so patching the
  window alone selects the tier (60→full, 70→micro, 200→neither). The recent-tail cut is forced with a HUGE
  driven prompt so the kept tail is exactly the last turn.
- **Found + fixed a real defect** while wiring AC #5: the synthetic summary head and the tail's user-turn
  boundary are two adjacent `ModelRequest`s, which pydantic-ai coalesces into one on the *next* leg. That
  shrinks the prior history by one, so the positional `_persisted_count` cursor would slice past — and
  silently drop — the next turn's first new message from the session log. Fixed by clamping the cursor to
  `len(all_messages) - len(run.result.new_messages())` after each leg (a no-op for non-compacted, well-formed
  histories; regression-covered by `test_no_repersist_after_full_compaction`).
- `docs/architecture.png` was regenerated by a hook as a side effect; restored to HEAD (left untouched/unstaged per the task).
- Per role: NOT committed — handing off to the Tester.

### [Tester] 2026-06-26 22:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 114 files clean; `ruff check` all passed)
- Unit tests: 785 passed / 0 failed
- Integration tests: 8 passed / 0 failed (both capstones)
- `make ci`: 793 passed, 0 warnings, `uv lock --check` clean, no network
- Warnings: 0 (`filterwarnings=["error"]` enforced)

**E2E adversarial pass** (own scenarios; real `AgentTurnHandler` loop + `FunctionModel`/`TestModel`, no network; replayed the JSONL session log to prove durability)
- Happy path: full compaction through a driven turn → `message_history == [summary, *tail]`, `compaction` line written, `ContextCompacted` rendered as `Decode - compacted context (...)` (PASS)
- Break path 1 (state edge — cursor/coalescing fix, the headline probe): after a full compaction, drove **3** follow-up turns and replayed the on-disk log → every follow-up prompt **and** its assistant ack present exactly once, the dropped oldest turn absent, summary head present once → nothing dropped, nothing double-written (PASS). Traced the mechanism: post-compaction the synthetic summary head + tail user-boundary are two adjacent `ModelRequest`s; pydantic-ai coalesces them on the next leg, shrinking the prior prefix; the `min(_persisted_count, len(all_messages)-len(new_messages()))` clamp (loop.py:389-390) lowers the cursor to exactly the new-message floor, so `_persist_turn` writes the next turn's first message instead of slicing past it. The clamp only ever lowers the cursor toward the new-message boundary, so it can never re-write an already-persisted message either.
- Break path 2 (boundary inputs — tier selection at exact thresholds, window=100 → full=int(100*0.8)=80, micro=int(100*0.6)=60): input_tokens ∈ {80→full, 81→full, 79→micro, 60→micro, 59→neither, 0→neither} all selected the correct tier with the correct event / log-line / cursor side-effects (PASS, 6 parametrized cases)
- Break path 3 (failure mode — full band but summarizer returns None): in the full band the cascade calls `compact()`, which no-ops on a `None` summary; because the cascade is `if full … elif micro`, micro is **not** attempted as a fallback → that turn does nothing (history untouched, no event, no log line). Spec-conformant (`if full → compact() elif micro → _microcompact()`) and a safe degradation, but flagged below as a follow-up observation (PASS — behaves as specified)
- Break path 4 (state edge — micro idempotence across turns): two consecutive microcompacting turns leave exactly one placeholder body — an already-elided part is never re-counted or re-blanked (PASS)
- Break path 5 (disabled override + 0-token fallback): `compaction_enabled=False` with `input_tokens=10_000_000` fires neither tier; `input_tokens=0` (no leg measured) fires neither (PASS)
- Break path 6 (durability — micro is in-memory only): after a microcompaction the JSONL bytes still hold the original tool output (no placeholder on disk), `_persisted_count` unchanged, `load()` replays full fidelity (PASS)
- Break path 7 (no-op surfaces): `compact()` returns `False` and writes no checkpoint on a trivial history and on a `None` summary; `compact()` with no session log still resets history/cursor and emits; successive full compactions keep a single summary head and replay lands on the **last** checkpoint, never a doubled transcript (PASS)

**Acceptance criteria**
- [x] PASS — `_run_leg` records `run.result.usage.input_tokens`; `last_input_tokens` property exposes it — `test_run_leg_captures_input_tokens_and_property_exposes_it`; loop.py:381 + property loop.py:145-154 (0 before any leg, >0 after a streamed leg)
- [x] PASS — Upper tier triggers `compact()`: `[summary, *tail]`, `_persisted_count == len`, `compaction` line written, `ContextCompacted` emitted — `test_full_tier_compacts_through_the_turn` + adversarial `test_tier_boundaries[80-full]`/`[81-full]`
- [x] PASS — Middle tier triggers `_microcompact()`: bodies blanked in memory, count unchanged, no `compaction` line, `_persisted_count` unchanged, `ContextMicrocompacted` emitted — `test_middle_tier_microcompacts_through_the_turn` + adversarial `[79-micro]`/`[60-micro]`
- [x] PASS — Micro not persisted: on-disk JSONL keeps original full tool outputs; `load()` replays full — `test_microcompaction_keeps_full_fidelity_on_disk` + adversarial `test_micro_is_idempotent_across_turns`
- [x] PASS — No re-persist after full compaction: next turn appends only its new messages — `test_no_repersist_after_full_compaction` + headline adversarial `test_multi_turn_after_full_compaction_log_loses_no_message` (3 follow-ups, log replayed, nothing lost/doubled)
- [x] PASS — Below both / disabled / 0 tokens: no compaction either tier — `test_below_both_tiers_is_a_no_op` / `test_disabled_flag_skips_the_cascade` / `test_zero_tokens_never_compacts` + adversarial `[59-none]`/`[0-none]`/`test_disabled_overrides_huge_tokens`
- [x] PASS — `compact()` False/no-op on trivial/short history or `None` summary; `compaction_model_or_settings is None` behaves exactly as before — `test_compact_returns_false_on_trivial_history` / `..._when_summary_is_none` / `test_none_seam_disables_cascade_even_with_a_tiny_window` (pre-existing loop/session-log/capstone tests all still green)
- [x] PASS — Both events render dim one-line system messages — `test_render_context_compacted_is_a_dim_one_liner` / `test_render_context_microcompacted_is_a_dim_one_liner`; events frozen+slots and in the `Event` union (`test_events.py` parametrized)
- [x] PASS — `make ci` green, 0 warnings, no network, `tests/` mirror updated 1:1 — 793 passed

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check (114 files) + ruff check (all passed) ...
============================= 793 passed in 8.14s ==============================

$ uv run pytest <14 adversarial probes: cursor/coalescing x3-turns, 6 tier boundaries,
   disabled-override, 2 compact no-ops, successive-compaction replay, no-session-log,
   summarizer-None-no-micro-fallback, micro-idempotence>
14 passed in 2.17s
```

**Other issues found** (non-blocking; PASS with note)
- When usage is in the **full** band but the summarizer call returns `None` (transient model failure / blank output), the cascade does **not** fall back to microcompaction — the `elif` means micro runs only when `full` is `False`, and a `False` return from `compact()` is not re-checked. This is exactly per spec (`if full → compact() elif micro → _microcompact()`) and degrades safely (context simply isn't reduced that turn and the trigger re-fires next turn), so it is not a defect. Worth a one-line follow-up consideration for a future task: a micro fallback when full compaction no-ops at high context could give some relief during a summarizer outage. No action required for 044.
- `compact()`/`_microcompact()` emit via `self._deps.emit` while the streaming path uses `ctx.emit`; verified both resolve to the same `_on_event` sink in `run_app` (app.py:671 + Runner on_event app.py:720), so rendering order is correct. Using `deps.emit` is the right choice since `compact()` is reused by `/compact` (task 045) where no per-turn `ctx` exists.

**VERDICT: PASS**