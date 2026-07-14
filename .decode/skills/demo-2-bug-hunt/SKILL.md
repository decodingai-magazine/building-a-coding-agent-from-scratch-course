---
name: demo-2-bug-hunt
description: Demo skill that hunts and fixes two seeded bugs in a tiny stats package until its test suite goes green.
---

Hunt down and fix the bugs in a small statistics package until every test passes.

## Setup

1. Copy the seeded project into a working directory you own:
   `cp -r references/buggy_repo/ ./bug-hunt/`
2. Change into it and run the suite to see the failures:
   `cd ./bug-hunt/ && uv run pytest -q`

Exactly two tests fail as committed — one in `median`, one in `variance`. Do NOT edit the tests;
they encode the correct behaviour. Fix `stats.py` instead.

## Hunt

- Read `test_stats.py` to learn the contract each failing test asserts (the expected values are the
  spec).
- Read `stats.py` and locate the defect behind each failure with `grep`/`read`. The LSP diagnostics
  that surface on your edits will help you catch typos and type slips as you go.
- There are two independent bugs:
  - `median` returns the wrong element for odd-length inputs (an indexing off-by-one).
  - `variance` comes back with the wrong sign (it should never be negative).

## Fix and verify

1. Fix the root cause of each bug in `stats.py` — the smallest correct change, not a special case
   that only satisfies the one test input.
2. Re-run `uv run pytest -q` and confirm the whole suite is green.
3. Summarise what each bug was and the one-line fix you applied.
