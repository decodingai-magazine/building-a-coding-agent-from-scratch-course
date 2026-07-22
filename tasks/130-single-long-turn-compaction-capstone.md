---
id: 130
feature: fix-compaction
status: pending
---

# Capstone regression: the original single-long-turn session shape compacts end to end

The bug was discovered on a real session (1 user prompt + 63 tool messages in ONE turn, no
compaction record ever). Each fix task proved its slice in isolation; this capstone proves
the composed behavior through the real `AgentTurnHandler` at the session shape that exposed
the bug — so the bug class cannot silently return.

Depends on: 125, 126, 127, 128. (129 not required — the capstone uses the Model-instance
seam, no network.)

## Scope

Extend `tests/integration/test_compaction_capstone.py` (or add a sibling test in it) driving
`AgentTurnHandler` with a `FunctionModel`/`TestModel` that reproduces the shape: one turn,
one user prompt, ~15+ tool call/return rounds with fat tool outputs, per-response populated
usage, and a small configured window (monkeypatched settings), summarizer = stub Model.
Three composed assertions:

1. **Auto full compaction fires on one long turn** (root cause 1+2 together): at would-stop
   the cascade triggers off the LAST response's usage, `split_tail` cuts at a
   `ModelResponse` boundary inside the turn, history becomes `[summary, *tail]` with every
   tool pair intact, a `compaction` line is persisted, `ContextCompacted` is emitted.
2. **Gauge lifecycle**: during the turn `last_input_tokens` equals the last response's
   `input_tokens + cache_read_tokens` (not the cumulative sum); immediately post-compaction
   it equals the chars≈/4 estimate of the kept history; a follow-up leg overwrites it with
   the provider number.
3. **Micro tier on the same shape**: with usage tuned between the micro and full levels,
   `ContextMicrocompacted` fires with `elided > 0` and the JSONL log keeps full fidelity
   (no compaction line, cursor unmoved).

## Acceptance criteria

- [ ] The capstone test fails when any ONE of the four fixes (125/126/127/128) is reverted
      (spot-verified by the SWE during development — e.g. `git stash` the split_tail hunk and
      watch it go red; note the check in the task log).
- [ ] Assertion set 1-3 above implemented as described, through the public handler surface
      (no reaching into pydantic-ai internals beyond message construction).
- [ ] Runs offline in `make integration-tests` / `make ci` (Model-instance seam only, no
      keys), consistent with the existing capstone's conventions.
- [ ] `make ci` green.

## User stories

### Story: The original failing session, replayed green
1. Developer runs `make integration-tests`.
2. The capstone reconstructs the 2026-07-22 session shape (one turn, dozens of tool
   messages) and asserts a compaction record NOW appears where the real log had none.

### Story: A future refactor cannot silently regress compaction
1. A future task refactors `split_tail` or the usage plumbing subtly wrong.
2. `make ci` fails on this capstone with a named assertion (boundary / gauge / tier),
   pointing at the exact regressed slice.

## Out of scope

- Manual-QA playbook updates (skill `manual-e2e-qa`) — separate docs surface.
- Any new production code: this task ships tests only (plus trivial test helpers).

## Log
