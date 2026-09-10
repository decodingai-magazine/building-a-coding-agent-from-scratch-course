---
id: 158-benchmark-tasks-easy-medium-v2
feature: evals-v2
status: pending
---

# Convert the 7 easy + 6 medium Benchmark Tasks to format v2 (calibrated for Qwen3.6-35B); delete 010

Tags: `evals`, `benchmark`
Depends on: 157
Blocks: 159

## Scope
Convert tasks 001–009 and 011–014 to the 157 layout, applying the audit checklist to each, calibrating `max_steps`/wording to the harness's default provider (`modal` serving `Qwen/Qwen3.6-35B-A3B-FP8`), and porting every edge case the deleted `test_oracle_edge_cases{,_medium}.py` held. **010-git-hygiene is DELETED** (grill decision): its premise — uncommitted files and a branch *name* — is unobservable through Hand-back, which pushes only the session branch. Task numbers are not compacted (the gap at 010 stays).

Conversion table (category · max_steps · agent timeout · verifier timeout · notes):

| Task | Category | steps | agent s | verif s | Notes |
|---|---|---|---|---|---|
| 001 find-and-replace | Operations | 12 | 600 | 120 | `tests/expected.ini`; solve.sh copies sibling `config.ini` |
| 002 regex-extraction | Software | 15 | 600 | 120 | diff vs `tests/expected.txt`, trailing newline tolerant |
| 003 csv-to-json | Software | 15 | 600 | 120 | JSON-equal vs `tests/expected.json` |
| 004 markdown-toc | Software | 15 | 600 | 120 | TOC block vs `tests/expected_toc.txt` |
| 005 encoding-normalize | Software | 15 | 600 | 120 | `environment/setup.sh` writes the legacy files |
| 006 log-forensics | Security | 15 | 600 | 120 | run `ban_ips.py`, set-compare |
| 007 fix-failing-test | Software | 15 | 600 | 120 | SWE-bench shape: seed `test_ranges.py` in `unittest.TestCase` style; hidden copy `tests/test_ranges.py`; `[verifier.tests] fail_to_pass=[the one failing method]`, `pass_to_pass=[the other three]`; the sha256 tamper check is deleted (tests are injected last) |
| 008 dependency-repair | Software | 20 | 900 | 120 | run `python3 main.py`, compare the two lines |
| 009 multi-file-rename | Software | 25 | 900 | 120 | hidden `tests/test_billing.py` imports `calculate_total` (F2P) + `grep -L compute_total` over `*.py` |
| 011 json-schema-migration | Software | 20 | 900 | 120 | structural compare |
| 012 makefile-doctor | Operations | 20 | 900 | 120 | verify `make` exists in the sandbox image; if not, instruction says how to test without it and the AC notes it |
| 013 sqlite-analyst | Science | 20 | 900 | 120 | instruction says the `sqlite3` CLI is absent; use python3's module |
| 014 cli-flag-add | Software | 25 | 900 | 120 | hidden `tests/test_cli_modes.py` → unittest style, F2P |

## Acceptance criteria
- [ ] Thirteen folders (`001`–`009`, `011`–`014`) on the v2 layout (`task.toml`, `instruction.md`, `environment/`, `tests/test.sh`, `solution/solve.sh`), legacy `task.yaml`/`setup/`/`verify/` gone from them; values per the table above; each `task.toml` carries a `description` one-liner. `evals/benchmark/tasks/010-git-hygiene/` deleted together with any test that names it.
- [ ] Every instruction rewritten against the checklist: exact deliverable paths and names; the self-check command that works in the sandbox image; no mention of hidden assets; ≤ 250 words. 007/009/014 test files converted to `unittest.TestCase` so `python3 -m unittest -v <module>` runs in the image; instructions name that command.
- [ ] `make ci` green: oracle-sanity proves all 13 both directions; canary test green; for 007/009/014 the nop direction demonstrates the F2P tests fail on base.
- [ ] `tests/unit/evals/benchmark/test_verifier_edge_cases_easy.py` / `_medium.py`: every edge case from the deleted `test_oracle_edge_cases.py` / `_medium.py` (except 010's) has a counterpart against the new verifier (near-miss workspace → reward 0; tolerant-format workspace → reward 1); file header lists the ported cases.
- [ ] `git diff --stat` shows no change outside `evals/benchmark/tasks/` and `tests/unit/evals/benchmark/`.

## Out of scope
- Hard tasks 015–020 (159). New tasks. Loader changes (157). Reframing 010 (dropped, not reframed).

## Log
### [PA] 2026-09-10 — Grooming
Calibration rule of thumb for a 35B MoE with tools: an easy task should complete in ≤ 6 requests with a 15-step cap (the cap is never the reason it fails); medium in ≤ 12 with 20–25. The `unittest` conversion is what makes "run the tests" a real instruction inside a pytest-less image — the old prompts assumed Claude-level improvisation. 010's premise is unobservable through Hand-back; the human chose to drop it rather than reframe it (6 medium tasks remain).
