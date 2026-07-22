---
id: 125
feature: fix-compaction
status: done
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

- [x] Regression test (written first, fails on current code): a history of exactly one user
      turn — 1 `ModelRequest(UserPromptPart)` followed by ~20 alternating
      `ModelResponse(ToolCallPart)` / `ModelRequest(ToolReturnPart)` pairs whose tool bodies
      total well over `keep_recent_tokens` — makes `split_tail` return an index > 0.
- [x] The returned index always lands on a valid boundary: for that history and a spread of
      budgets, `messages[cut]` is a `ModelResponse` or a tool-return-free `ModelRequest`;
      the tail NEVER starts with a `ModelRequest` carrying `ToolReturnPart`/`RetryPromptPart`.
- [x] Regression test (written first, fails on current code): `microcompact` on the same
      single-turn history returns `elided > 0` and blanks only parts before the boundary.
- [x] Every tool-call/result pair stays intact across the cut: any `ToolReturnPart` in the
      tail has its matching `ToolCallPart` (same `tool_call_id`) also in the tail.
- [x] Existing contracts hold: empty list → 0; small history under budget → 0; nothing
      fits → `len(messages)`; user-turn boundaries are still valid cuts (multi-turn histories
      behave as before or cut later/finer, never coarser than today).
- [x] Docstrings (module header + `split_tail`) describe the new valid-cut semantics and
      reference ADR-0018.
- [x] `make format-check lint-check unit-tests` green.

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

### [SWE] 2026-07-22 19:20 — Implementation

**Files modified**
- `src/decode/context/compaction.py` — redefined the Compaction Boundary: replaced
  `_is_user_turn_boundary` with `_is_compaction_boundary` (ModelResponse OR tool-return-free
  ModelRequest); `split_tail` now snaps to the nearest valid boundary (not 0); updated module +
  `split_tail` docstrings to the ADR-0018 §1 semantics.
- `tests/unit/decode/context/test_compaction.py` — 5 regression/coverage tests (single long
  agentic turn → `split_tail > 0`; valid-boundary + intact-pair across a budget spread;
  `microcompact` elides inside one turn) plus rewrote the snap-back test to the ModelResponse
  boundary (now index 5) and added a user-turn-nearest snap test.

**Tests**
- Unit: 2198 passing, 0 failing (`make unit-tests`); compaction module 43 passing.
- Integration: N/A — pure in-memory primitive, no infra changes.

**Regression-test-first (bug-fix discipline)**
- Wrote the failing tests first; confirmed red on unfixed code:
  `test_split_tail_returns_positive_index_for_a_single_long_agentic_turn` (returned 0),
  `test_microcompact_elides_inside_a_single_long_agentic_turn` (elided 0),
  `test_split_tail_snaps_back_to_a_model_response_boundary_no_orphan` (returned 4, want 5).
  Applied the boundary fix → all green.

**Acceptance criteria**
- [x] `split_tail` returns > 0 on a single long agentic turn — `test_split_tail_returns_positive_index_for_a_single_long_agentic_turn`.
- [x] Returned index lands on a valid boundary across a budget spread — `test_split_tail_cut_lands_on_a_valid_boundary_never_an_orphan`.
- [x] `microcompact` elides > 0 on the same history, only before the boundary — `test_microcompact_elides_inside_a_single_long_agentic_turn`.
- [x] Every tool-call/result pair intact across the cut — `test_split_tail_keeps_every_tool_pair_intact_across_the_cut`.
- [x] Existing contracts hold (empty→0, under-budget→0, nothing-fits→len, user-turn still valid) — `test_split_tail_returns_zero_when_everything_fits`, `test_split_tail_returns_len_when_nothing_fits`, `test_split_tail_still_snaps_to_a_user_turn_boundary_when_that_is_nearest`.
- [x] Docstrings (module header + `split_tail`) describe the new semantics, reference ADR-0018.
- [x] `make format-check lint-check unit-tests` green.

**Evidence**
```
$ uv run pytest tests/unit/decode/context/test_compaction.py -q
43 passed in 1.01s

$ make unit-tests
======================= 2198 passed in 118.95s (0:01:58) =======================

$ # end-to-end: the exact failing session shape (1 prompt + 62 tool messages)
history length: 63 (1 prompt + 62 tool messages)
split_tail cut index: 53 -> tail keeps 10 messages
tail head is ModelResponse: True
microcompact elided: 26 tool outputs
OK: single long agentic turn now compacts (was no-op before ADR-0018 fix)
```

**Notes**
- The pre-existing `test_split_tail_snaps_back_to_a_user_turn_boundary_no_orphan` was rewritten
  (renamed `..._to_a_model_response_boundary_...`, expected 4 → 5). Under ADR-0018 the raw cut
  (index 6, an orphaned return) snaps to the nearest valid boundary — the preceding ModelResponse
  at index 5 — which is finer/later than the old index 4, matching the "never coarser" contract.
- `microcompact` unchanged (inherits the fix via `split_tail`), per scope.
- No commit yet — handing off to the Tester first.

### [Tester] 2026-07-22 23:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` 308 files formatted; `make lint-check`
  all checks passed; `make pre-commit` = format-check + lint-check + unit-tests, green)
- Unit tests: 2198 passed / 0 failed (`make unit-tests`, 118.95s); compaction module 43/43
- Integration tests: 105 passed / 0 failed, 16 skipped (docker daemon unreachable in this
  environment — expected, unrelated to this change); `test_compaction_capstone.py` (the real
  `build_agent()` + `Runner` + `AgentTurnHandler` end-to-end cascade) 1/1 passed
- Warnings: 0

**Regression-test-first verification**
- Independently reproduced red→green: checked out the OLD `compaction.py` against the NEW test
  file — exactly the 3 tests the SWE named went red
  (`test_split_tail_returns_positive_index_for_a_single_long_agentic_turn`,
  `test_split_tail_snaps_back_to_a_model_response_boundary_no_orphan` [4 != 5],
  `test_microcompact_elides_inside_a_single_long_agentic_turn`), 40 others stayed green; restoring
  the new implementation turned all 43 green. Confirms the SWE's red→green claim.

**E2E adversarial pass**
- Happy path: single long agentic turn (1 user prompt + 30 read-tool call/return pairs, 61
  messages, `keep_recent_tokens=200`) → `split_tail` cut=59, tail head `ModelResponse`,
  `microcompact` elided=29. Matches SWE's reported session shape (63 msgs → cut 53, elided 26).
  PASS.
- Break path 1 (state edge — `RetryPromptPart`-carrying `ModelRequest` never a tail head):
  history `[user, tool_call(big args), RetryPromptPart, assistant]`, `keep_recent_tokens=10` →
  cut lands on the preceding `ModelResponse`, never on the retry request. PASS.
- Break path 2 (state edge — history ENDING in a `ModelRequest` of tool returns, no trailing
  `ModelResponse`): 11-message history ending on a tool return, `keep_recent_tokens=50` → cut=11
  (`len(messages)`, "nothing fits" contract), zero orphaned returns in the (empty) tail. PASS.
- Break path 3 (state edge — parallel tool calls: one `ModelResponse` issuing 3 `ToolCallPart`s,
  one `ModelRequest` returning all 3): budget spread [10, 50, 100, 300, 1000] → every cut lands
  on a valid boundary, the 3-return request is never split from its response. PASS.
- Break path 4 (malformed input — `ModelRequest` mixing `UserPromptPart` AND `ToolReturnPart`,
  i.e. steering appended mid-resume): `_is_compaction_boundary` correctly returns `False` for the
  mixed request (the `any(ToolReturnPart/RetryPromptPart)` check does not special-case a
  co-occurring `UserPromptPart`); `split_tail` never cuts there. PASS — this is the AC's most
  subtle case and it is handled correctly.
- Break path 5 (boundary — `keep_recent_tokens=0`): realistic-length history → cut = `len`
  ("nothing fits"), `microcompact` elides 0 (boundary at the very end, nothing to elide before
  it). PASS.
- Break path 6 (boundary — single-message histories): single user message, single
  `ModelResponse`-only message, single tool-return-only message all resolve to documented
  contracts (0 / `len` per the fits/doesn't-fit rule). One noteworthy finding recorded below.

**Acceptance criteria**
- [x] PASS — regression test for single long agentic turn (`split_tail` > 0), written
      red-first — `test_split_tail_returns_positive_index_for_a_single_long_agentic_turn`;
      independently reproduced red on old code, green on new.
- [x] PASS — returned index always lands on a valid boundary across a budget spread —
      `test_split_tail_cut_lands_on_a_valid_boundary_never_an_orphan` (parametrized 50/100/200/
      400/800) + my own adversarial spread (10/50/100/300/1000) on a parallel-tool-call history.
- [x] PASS — `microcompact` regression test, elides > 0, only before the boundary, written
      red-first — `test_microcompact_elides_inside_a_single_long_agentic_turn`; independently
      reproduced red→green.
- [x] PASS — every tool-call/result pair intact across the cut —
      `test_split_tail_keeps_every_tool_pair_intact_across_the_cut` + my parallel-tool-call and
      trailing-tool-return adversarial histories, zero orphans in every case.
- [x] PASS — existing contracts hold (empty→0, under-budget→0, nothing-fits→len, user-turn still
      valid, never coarser) — `test_split_tail_returns_zero_when_everything_fits`,
      `test_split_tail_returns_len_when_nothing_fits`,
      `test_split_tail_still_snaps_to_a_user_turn_boundary_when_that_is_nearest`.
- [x] PASS — docstrings (module header line 6-8, `split_tail`) describe the new valid-cut
      semantics and reference ADR-0018 §1 — `src/decode/context/compaction.py:6-8,131-144`.
- [x] PASS — `make format-check lint-check unit-tests` green — see Test summary above.

**Evidence**
```
$ uv run pytest tests/unit/decode/context/test_compaction.py -q
43 passed in 0.70s

$ make unit-tests
======================= 2198 passed in 120.37s (0:02:00) =======================

$ uv run pytest tests/integration -q
105 passed, 16 skipped in 389.65s (0:06:29)   # skips = docker daemon unreachable, unrelated

$ uv run pytest tests/integration/test_compaction_capstone.py -q
1 passed in 0.89s
```

**Other issues found**
- `_is_compaction_boundary`'s guarantee is not enforced on the `cut == 0` early-return path in
  `split_tail` (`src/decode/context/compaction.py:159-160`): when the raw budget-fit walk lands
  cut at 0 ("everything fits"), the function returns 0 unconditionally without checking whether
  `messages[0]` is actually a valid boundary. Repro:
  `split_tail([ModelRequest(parts=[ToolReturnPart(tool_name='read', content='small',
  tool_call_id='c0')])], keep_recent_tokens=1000)` → returns `0`, and
  `_is_compaction_boundary(messages[0])` is `False` — a single-message history whose only message
  is a bare tool return "fits" and is returned as a valid tail head, violating the "never valid
  at a ToolReturnPart-carrying request" invariant. Not a regression from this diff (identical
  shortcut exists in the pre-fix code, verified via `git stash`) and not reachable through any
  real caller today (pydantic-ai message lists — and this codebase's own post-compaction
  `[summary, *tail]` — always open index 0 on a real user/summary `ModelRequest`, never a bare
  tool return). Recommend a follow-up hardening task to make the `cut == 0` branch check
  `_is_compaction_boundary(messages[0])` and walk forward if it fails, so the invariant holds
  unconditionally rather than by caller convention. Does not block this PASS.
- `tests/integration/test_compaction_capstone.py:349` still comments "snaps to a user-turn
  boundary, ADR-0006 §5" — stale wording (untouched file, out of this task's scope; the test
  itself still passes and its orphan-check assertion is unaffected).

**VERDICT: PASS**

### [PA] 2026-07-23 — Acceptance Review (feature fix-compaction, PR #50)

**VERDICT: ACCEPT**

Walked the whole feature from the user's perspective against the Tasks Plan (tasks 125-130,
ADR-0018): the original single-long-turn session shape now auto-compacts (capstone 4/4 green,
re-run); the Context Gauge reads the last response's usage and drops to the kept-history
estimate the instant compaction lands (footer reads `handler.last_input_tokens`, app.py:587);
`/compact` gives three honest distinct lines (failure copy names `.decode/logs/decode.log`,
no enum jargon leaked); all three providers summarize via `compaction_model=agent.model`
(wiring test gemini/openrouter/modal green, re-run); glossary terms (Compaction Boundary /
Compaction Outcome / Context Gauge) and ADR-0018 land verbatim-consistent with code.
Non-blocking nit noted for a future cleanup: stale "user-turn boundary, ADR-0006 §5" comment
at tests/integration/test_compaction_capstone.py:413. Hand off to the PR Reviewer.
