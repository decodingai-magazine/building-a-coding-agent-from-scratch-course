---
id: 120
feature: evals
status: pending
---

# Makefile targets, .env.example polish, evals docs

Depends on: 106, 107, 115 (targets wrap them); 116, 117 (documented). Implements ADR-0017 §9.

## Scope

**Makefile** (+ `.PHONY`, `## help` lines):

- `eval-benchmark`: `uv run python -m evals benchmark $(ARGS)` — needs `GEMINI_API_KEY` (or the
  active provider's key) + `OPIK_API_KEY`; fail fast with one friendly line when absent.
- `eval-regression`: `uv run python -m evals sync --regression && uv run pytest
  evals/regression/test_thresholds.py` — the manual pre-merge ritual.
- NOTHING added to `ci` / `test` / `pre-commit` (the deliberate cadence decision).

**Docs** — `docs/evals.md` (linked from README): the four tracks in one page — how to run each
demo, `make eval-benchmark` (incl. `--trials`, `--sandbox modal`, cost aggregates), the regression
ritual + threshold gate + Test Suites contrast, the online-eval story; "CI-pointable later" note
for the threshold module. `.env.example` evals block gets its final wording (judge model, eval
project name, which keys each target needs).

**AGENTS.md**: one short pointer line in Running commands / Testing area naming the two make
targets (keep it to a sentence — AGENTS.md stays lean).

## Acceptance Criteria

- [ ] `make help` lists both targets with honest one-liners; both run (spot-run logged) and fail
      friendly without keys.
- [ ] `make ci` output is byte-identical in behavior (no eval step added) and green.
- [ ] `docs/evals.md` covers all four tracks with copy-pasteable commands.
- [ ] `.env.example` and AGENTS.md updated.

## Out of scope

- CI workflow changes (explicitly later). Leaderboard UI (non-goal).

## Log
