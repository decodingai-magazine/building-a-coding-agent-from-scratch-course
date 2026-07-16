---
name: demo-2-bug-hunt
description: Demo skill that hunts two seeded bugs in a tiny stats package until its tests go green, then files a detective-style CASE_FILE.md with the evidence.
---

Play detective on a small statistics package: reproduce the failures, hunt down the bugs, fix
them until every test passes, and file a case report.

## Setup

1. Copy the seeded project into a working directory you own:
   `cp -r references/buggy_repo/ ./bug-hunt/`
2. Change into it and run the suite to see the failures:
   `cd ./bug-hunt/ && uv run pytest -q`
3. **Save the crime scene**: keep the exact failing-test output — the failure names and the
   expected-vs-actual values — you will quote it in the case file at the end.

Exactly two tests fail as committed — one in `median`, one in `variance`. Do NOT edit the tests;
they encode the correct behaviour. Fix `stats.py` instead.

## Hunt

Track the investigation with `todo_write` (reproduce → suspect A → suspect B → verify → case
file) and tick items off as you close them.

- Read `test_stats.py` to learn the contract each failing test asserts (the expected values are
  the spec).
- Read `stats.py` and locate the defect behind each failure with `grep`/`read`. The LSP
  diagnostics that surface on your edits will help you catch typos and type slips as you go.
- There are two independent bugs:
  - `median` returns the wrong element for odd-length inputs (an indexing off-by-one).
  - `variance` comes back with the wrong sign (it should never be negative).

## Fix and verify

1. Fix the root cause of each bug in `stats.py` — the smallest correct change, not a special case
   that only satisfies the one test input.
2. Re-run `uv run pytest -q` and confirm the whole suite is green. Keep that green one-liner too.

## File the case report

Write `bug-hunt/CASE_FILE.md` — short, punchy, detective-flavoured:

- **The symptoms** — the failing test names and the quoted expected-vs-actual output from setup
  step 3.
- **The culprits** — one section per bug: the guilty line (`stats.py:<line>`), the root cause in
  one sentence, and the before/after of the fixed line.
- **Case closed** — the quoted green `pytest -q` summary line proving the suite passes.

Report the two root causes, the one-line fix for each, and point the human at `CASE_FILE.md`.
