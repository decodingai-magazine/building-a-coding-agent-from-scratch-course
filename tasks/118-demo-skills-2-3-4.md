---
id: 118
feature: evals
status: pending
---

# Demo skills 2–4: bug-hunt, terminal-arcade, data-detective

Depends on: none (no Opik; independent of the harness). Implements ADR-0017 §2 (Track A).

## Scope

Three `.decode/skills/demo-N-<slug>/` dirs, same shape as the existing
`demo-1-implement-substack-summarizer` (SKILL.md frontmatter `name`+`description` + body prompt +
optional `references/`):

- **demo-2-bug-hunt** — `references/buggy_repo/`: a tiny package (e.g. `stats.py` with an
  off-by-one in `median` and a sign bug in `variance`) + `test_stats.py` where 2 tests fail. Body:
  copy `references/buggy_repo/` into `./bug-hunt/`, run `uv run pytest` to see the failures, hunt
  with grep/read (LSP diagnostics will surface on edits), fix, rerun green.
- **demo-3-terminal-arcade** — prompt-only: build a playable Snake in pure-stdlib Python `curses`
  (~100 lines, zero deps) at `snake.py`; iterate by running it (import/syntax checks + short
  headless runs — the human plays the final result).
- **demo-4-data-detective** — `references/messy_sales.csv` (duplicate rows, mixed date formats,
  currency strings, missing values). Body: clean it, analyze, emit `report.md` + matplotlib PNG
  charts under `charts/` (use `uv run --with pandas,matplotlib` so no project deps change).

**Tests** (`tests/unit/…` wherever decode's skills-loader tests live): each new SKILL.md parses
through decode's real skills loader (frontmatter `name` matches convention, non-empty
description/body); fixture files exist where the body references them.

## Acceptance Criteria

- [ ] Three skills appear in the Skills Catalog and load via `/demo-2-bug-hunt` etc. (loader
      unit test + manual spot-run of one demo end-to-end, logged).
- [ ] demo-2's seeded repo genuinely fails 2 tests as committed; demo-4's CSV carries every listed
      mess.
- [ ] No new project dependencies.
- [ ] `make ci` green.

## Out of scope

- demo-1 (exists, untouched). Demos 5–7 (119). Opik anything.

## Log
