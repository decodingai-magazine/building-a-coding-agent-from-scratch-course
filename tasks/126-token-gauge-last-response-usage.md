---
id: 126
feature: fix-compaction
status: pending
---

# Per-leg token gauge reads the last ModelResponse's RequestUsage, not cumulative RunUsage

Root cause 2 of the compaction misfire. `src/decode/agent/loop.py:367` stores
`run.usage().input_tokens` — in pydantic-ai 1.95.1 `RunUsage` is CUMULATIVE across every
request in the leg (verified: `usage.py:243` accumulates with `+=`; one request per tool
round), so the Context Gauge and both compaction triggers overcount ~N× for N tool rounds.
The true context size is the LAST response's own `RequestUsage`
(`ModelResponse.usage`, verified at `pydantic_ai/messages.py:2062`).

This task implements ADR-0018 §2. Depends on: none. (Task 123, in-progress, owns the window
DENOMINATOR; this task fixes the NUMERATOR — no overlap beyond file adjacency.)

## Scope

- In `AgentTurnHandler._run_leg`'s `finally` block (`loop.py:360-367`), replace
  `self._last_input_tokens = run.usage().input_tokens` with a small helper (suggested:
  module-level `_leg_input_tokens(messages: list[ModelMessage]) -> int`):
  - Walk `run.all_messages()` BACKWARDS; the first `ModelResponse` whose
    `usage.input_tokens > 0` is authoritative.
  - Value = `usage.input_tokens + usage.cache_read_tokens` (cached prompt tokens are still
    context occupancy).
  - No populated response → return 0 (`should_compact` already treats 0 as "don't fire",
    ADR-0006 §3 safe fallback — unchanged).
- Update the `last_input_tokens` property docstring (`loop.py:104-109`) and the inline
  comment at the assignment: it is now "the last response's provider-reported request usage",
  not "the leg's usage". Reference ADR-0018 §2.
- `_maybe_auto_compact`'s `RunUsage(input_tokens=self._last_input_tokens)` shim
  (`loop.py:252`) keeps working unchanged — `should_compact` only reads `input_tokens`.

**Regression-test-first:** failing test before the fix, in
`tests/unit/decode/agent/test_loop.py` (mirror), plus a direct unit test of the helper.

## Acceptance criteria

- [ ] Regression test (written first, fails on current code): drive one leg whose run
      produces 3 `ModelResponse`s with per-response usage e.g. 100 / 220 / 350 input tokens
      (follow the existing test_loop patterns — FunctionModel/TestModel or a stubbed run);
      `handler.last_input_tokens == 350` (+ its cache_read), NOT 670.
- [ ] Helper unit test: last populated response wins even when LATER responses carry
      unpopulated (default) usage — e.g. usages [100, 350, 0-default] → 350.
- [ ] Helper unit test: `cache_read_tokens` is added — last response usage
      `input_tokens=300, cache_read_tokens=50` → 350.
- [ ] Helper unit test: no `ModelResponse` with populated usage anywhere → 0, and
      `_maybe_auto_compact` fires nothing (existing don't-fire-at-0 behavior re-asserted).
- [ ] The TUI footer Context Gauge (which reads `last_input_tokens`) needs no code change —
      confirmed by existing gauge tests still passing.
- [ ] `make format-check lint-check unit-tests` green.

## User stories

### Story: The gauge stops crying wolf on a tool-heavy turn
1. User runs a turn with 10 tool rounds on a ~40k-token context.
2. Before: footer gauge jumps toward red (~400k counted); after: it shows ~40k — matching
   what the provider actually billed for the last request.
3. Auto-compaction no longer fires at a fraction of real window occupancy.

### Story: Trigger fires at the true 80% line
1. A genuinely long session crosses `window * (1 - reserve)` in REAL last-request tokens.
2. The full tier fires exactly then — not many turns early (overcount) and not never.

## Out of scope

- The window denominator / `--model` resolution (task 123 owns it).
- Post-compaction gauge estimate (task 128).

## Log
