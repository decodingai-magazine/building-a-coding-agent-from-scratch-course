---
id: 110
feature: evals
status: pending
---

# Author benchmark tasks 015–020 (hard, incl. G-Eval add-ons)

Depends on: 105; judges via 104. Implements ADR-0017 §2,5,7.

## Scope

15. `015-secret-scrub` — py files with hardcoded API keys; prompt: move to env reads. verify:
    grep finds no literal keys + script runs with env vars set. PLUS `judges:` entry in task.yaml —
    G-Eval minimal-diff (no gratuitous rewrites).
16. `016-implement-from-spec` — stub function + rich docstring (e.g. merge overlapping intervals);
    verify: hidden pytest injected at grade time.
17. `017-flaky-test-hunt` — order-dependent test (shared mutable global); prompt: make the suite
    reliable. verify: full suite green 3 consecutive runs.
18. `018-git-bisect-revert` — `setup.sh` builds history with one breaking commit; prompt: find and
    revert it. verify: tests pass + history assert (a revert commit exists; original commits
    intact — no rewrite).
19. `019-patch-conflict-resolve` — `setup/feature.patch` that conflicts with the tree; prompt:
    apply and resolve. verify: patched behavior present + tests pass. PLUS G-Eval judge on
    resolution quality (both sides' intent preserved).
20. `020-build-small-tool` — word-frequency CLI (top-N, case-insensitive, punctuation-stripped);
    verify: hidden pytest + G-Eval code-quality judge (`judges:` in task.yaml).

Same contract discipline (difficulty `hard`; both sanity directions; judges declared in
`task.yaml` and picked up by the 106 runner via the 104 factory).

## Acceptance Criteria

- [ ] Six folders pass loader + both oracle-sanity directions in `make ci`.
- [ ] Tasks 15, 19, 20 declare G-Eval judges that the runner attaches (unit-verified via loader).
- [ ] 017's oracle demonstrably fails on the seeded flake (3-run loop catches it in sanity FAIL
      direction).
- [ ] Spot-run one task through the docker runner; result logged.
- [ ] `make ci` green.

## Out of scope

- Any runner/judge code change (104/106 own those).

## Log
