---
id: 159-benchmark-tasks-hard-v2-and-audit
feature: evals-v2
status: pending
---

# Convert the 6 hard Benchmark Tasks, delete per-task judges, audit sweep over all 19

Tags: `evals`, `benchmark`
Depends on: 158
Blocks: 161

## Scope
Convert 015–020 to the 157 layout, turn the three G-Eval `judges:` into deterministic checks where they encoded a real requirement, and run the audit checklist across all 19 with a machine-checked summary table.

| Task | Category | steps | agent s | verif s | Notes |
|---|---|---|---|---|---|
| 015 secret-scrub | Security | 30 | 1200 | 120 | `minimal_diff` judge → `git diff --numstat <base>..HEAD` (capture commit tolerated): only `service.py` changed, ≤ 8 lines added+deleted; env-var behaviour checks kept |
| 016 implement-from-spec | Software | 35 | 1200 | 120 | hidden `tests/test_intervals.py` (unittest, from `_verify_intervals.py`), all F2P |
| 017 flaky-test-hunt | Software | 35 | 1200 | 180 | seed `test_registry.py` → unittest style; hidden `tests/_verify_suite.py` runs it in several orders in fresh processes |
| 018 git-bisect-revert | Operations | 40 | 1500 | 180 | seeder adopts `setup.sh`'s history; verifier: every seed commit present unchanged, newest non-capture commit subject starts `Revert`, hidden tests pass |
| 019 patch-conflict-resolve | Software | 35 | 1200 | 120 | `resolution_quality` judge → hidden `tests/test_greet.py` (unittest) + no conflict markers + exact expected greeting (capitalised name AND the patch's wording/punctuation) |
| 020 build-small-tool | Software | 40 | 1500 | 120 | `code_quality` judge dropped (not graded); hidden CLI tests from `_verify_wordfreq.py` |

## Acceptance criteria
- [ ] Six folders `015`–`020` on the v2 layout per the table; `judges:` gone from the suite entirely (loader already rejects it — a unit test asserts no `task.yaml`/`judges` remains under `evals/benchmark/tasks/`).
- [ ] Instructions rewritten against the checklist (exact deliverables; self-check command; 017/019 name `python3 -m unittest -v <module>`; 018 names `git revert` and the "Revert" subject; 015 names `os.environ["API_KEY"]`/`DB_PASSWORD` and says "change only service.py").
- [ ] `make ci` green: oracle-sanity over all 19 both directions; canary test; `tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py` ports every case from the deleted `_hard.py` + the judge-replacement checks (015 bloated diff → 0; 019 markers left → 0; 018 amended history → 0).
- [ ] `tests/unit/evals/benchmark/test_suite_shape.py`: exactly 19 tasks load, 7 easy / 6 medium / 6 hard; every task's `max_steps`/timeouts ≤ its tier ceiling; every `category` in the taxonomy; every instruction ≤ 250 words and free of `tests/`, `solution/`, `reward`, `verif`; every test-bearing task (007, 009, 014, 016, 017, 019, 020) declares non-empty `[verifier.tests].fail_to_pass`.
- [ ] Sandbox-image check recorded: the log entry lists `which` results for `make`, `python3`, `git`, `sqlite3`, `pytest` in the sandbox image and any instruction adjusted accordingly.
- [ ] `git diff --stat` shows no change outside `evals/benchmark/tasks/015..020`, `tests/unit/evals/benchmark/`, and `evals/benchmark/tasks/README.md` (audit table appended: one row per task with tier, category, steps, F2P count).

## Out of scope
- Running through `decode run` (160). Re-tiering tasks (tiers are fixed: 7/6/6).

## Log
### [PA] 2026-09-10 — Grooming
A judge audits a verifier, it never grades of record (ADR-0022 §6). Two of the three judges encoded checkable requirements (015 minimality → diff bound; 019 both intentions → exact string); 020's code quality is dropped rather than faked. Hard-tier caps (35–40 steps, 20–25 min) are sized so a 35B model finishes sometimes — pass@k over trials is the sensitivity knob, not the cap.
