---
id: 125
feature: fix-compaction
status: pending
---

# `split_tail` accepts ModelResponse cut points so one long agentic turn can compact

Root cause 1 of the compaction no-op (verified against session log
`.decode/sessions/20260722T181859Z_8f85f3c9….jsonl`: one turn = 1 user prompt + 63 tool
messages, no compaction record ever). `split_tail` (`src/decode/context/compaction.py:127-159`)
snaps the budget cut back to the nearest USER-TURN boundary. A single long agentic turn has
exactly one — index 0 — so the snap collapses to 0 = "everything fits": `compact()` returns
falsy silently AND `microcompact()` elides nothing (it shares `split_tail`). Both tiers no-op
exactly when the context is one long tool-heavy turn.

This task implements ADR-0018 §1. Depends on: none.

## Scope

Redefine the **Compaction Boundary** (valid cut point) in `split_tail`:

- **Valid cut at index i:** `messages[i]` is a `ModelResponse`, OR a `ModelRequest` carrying
  NO `ToolReturnPart`/`RetryPromptPart` (i.e. a user-turn/summary-head request).
- **Never valid:** a `ModelRequest` carrying any `ToolReturnPart` or `RetryPromptPart` — even
  if it also carries other parts. Rationale: a return's matching call sits in the immediately
  preceding `ModelResponse`, so cutting AT a `ModelResponse` keeps every call/result pair
  intact; cutting at the return orphans it.
- **Snap-back goes to the NEAREST valid boundary at or below the raw budget cut** — not to 0.
  It returns 0 only when the nearest valid boundary genuinely is 0. The existing degenerate
  cases keep their contract: empty list → 0; everything fits within budget → 0; nothing
  fits → `len(messages)`.
- Replace/extend `_is_user_turn_boundary` (`compaction.py:254-257`) with the valid-cut
  predicate. Update the `split_tail` docstring and the module docstring line 6 ("snaps back to
  a user-turn boundary") to the new semantics.
- `microcompact` needs NO code change — it inherits the fix through `split_tail`. Its "old"
  region now correctly covers the elidable prefix of a single long turn.

**Regression-test-first (bug-fix discipline):** write the failing tests below BEFORE the fix,
in `tests/unit/decode/context/test_compaction.py` (1:1 mirror).

## Acceptance criteria

- [ ] Regression test (written first, fails on current code): a history of exactly one user
      turn — 1 `ModelRequest(UserPromptPart)` followed by ~20 alternating
      `ModelResponse(ToolCallPart)` / `ModelRequest(ToolReturnPart)` pairs whose tool bodies
      total well over `keep_recent_tokens` — makes `split_tail` return an index > 0.
- [ ] The returned index always lands on a valid boundary: for that history and a spread of
      budgets, `messages[cut]` is a `ModelResponse` or a tool-return-free `ModelRequest`;
      the tail NEVER starts with a `ModelRequest` carrying `ToolReturnPart`/`RetryPromptPart`.
- [ ] Regression test (written first, fails on current code): `microcompact` on the same
      single-turn history returns `elided > 0` and blanks only parts before the boundary.
- [ ] Every tool-call/result pair stays intact across the cut: any `ToolReturnPart` in the
      tail has its matching `ToolCallPart` (same `tool_call_id`) also in the tail.
- [ ] Existing contracts hold: empty list → 0; small history under budget → 0; nothing
      fits → `len(messages)`; user-turn boundaries are still valid cuts (multi-turn histories
      behave as before or cut later/finer, never coarser than today).
- [ ] Docstrings (module header + `split_tail`) describe the new valid-cut semantics and
      reference ADR-0018.
- [ ] `make format-check lint-check unit-tests` green.

## User stories

### Story: A user's single long research turn finally microcompacts
1. User asks decode one question that triggers ~60 tool calls in a single turn, pushing
   input tokens past the micro level (~60% of the window).
2. At would-stop the cascade runs; `split_tail` now finds a `ModelResponse` boundary inside
   the turn.
3. User sees the `ContextMicrocompacted` line ("N tool outputs elided") instead of nothing,
   and the next turn's request is measurably smaller.

### Story: SWE reproduces the bug before fixing it
1. SWE runs the new regression test on unfixed code: `split_tail` returns 0 — test fails red.
2. SWE applies the boundary fix; the same test passes; no other compaction test regresses.

## Out of scope

- The trigger's token source (task 126), outcome reporting (127), the summarizer model (129).
- Any change to `microcompact`'s eliding mechanics or persistence rules (ADR-0006 §3a stands).

## Log
