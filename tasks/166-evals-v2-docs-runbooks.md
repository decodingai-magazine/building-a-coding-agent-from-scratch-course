---
id: 166-evals-v2-docs-runbooks
feature: evals-v2
status: pending
---

# Docs: glossary consistency, runbooks 05/06/07, evals READMEs, AGENTS.md, e2e skill

Tags: `docs`
Depends on: 161, 164, 165
Blocks: None

## Scope
Land the operator story for evals v2 in one pass after the code exists. ADR-0022 and the glossary rows were written in the grooming commit — this task makes code, CLI help, runbooks and glossary agree word for word.

## Acceptance criteria
- [ ] `docs/glossary.md`: every row the grooming commit added/rewrote (Eval Harness, Benchmark Task, Verifier, Oracle, Seed Repo, Trial, Trial Dir, Reward, Infra Error, Benchmark Job, Regression Case, Difficulty Tier, Trace Mining, Online Rule, Kitaru Server, Judge, Experiment, pass@k, Threshold Gate, Test Suite) matches the shipped names in code and `--help` text; a grep for `Verify Oracle`, `Regression Probe`, `verify.sh`, `decode-benchmark-v1`, `decode-regression-v1`, `opik 1.9.8`, `--nb-samples`, `--probe` across `docs/`, `evals/`, `running_the_code/`, `AGENTS.md`, `.env.example` returns only the two retired-name glossary notes.
- [ ] `running_the_code/05_evals.md` rewritten: the four-track table (benchmark = "Terminal-Bench-style tasks run through `decode run`, graded host-side by `tests/test.sh` → `reward.txt`"), the Trial Dir layout, the failure taxonomy, `--trials/--threads/--job-name`, tiers + `--difficulty` for both tracks, the mining → case → gate loop, `online-rule create`, Test Suite as contrast; the calibration note (default provider Qwen3.6-35B; what pass@k over 3 trials means).
- [ ] `06_evals_replays.md` / `07_evals_replays_deploy.md` (as amended by 155 and 165) read top to bottom once for consistency: flag names, ids, server choice, agent versions.
- [ ] `evals/README.md`, `evals/benchmark/tasks/README.md`, `evals/regression/README.md` match the shipped layout and CLI (`python -m evals --help` output pasted where commands are listed).
- [ ] `AGENTS.md`: the **Evals** paragraph under "Running commands", the "Kitaru replay & what-if" section (local server default, `make kitaru-local`, the two commands, managed = URL switch), the Tech Stack table's pydantic-ai line (pin lifted, `<2.41`) and Recording line — minimum words; `tasks/README.md`'s "There is no `done/` folder" sentence corrected to what the repo does (`tasks/done/` exists).
- [ ] `.agents/skills/manual-e2e-qa/SKILL.md` gains two steps: a benchmark trial (`make eval-benchmark ARGS='--task 001-find-and-replace'`, what a good Trial Dir looks like) and local Kitaru (`make kitaru-local` → record → cohort → replay).
- [ ] `make ci` green; no code changes in this task beyond docstrings.

## Out of scope
- New ADRs. Article-length prose. Renaming anything in code (that is a bug in an earlier task).

## Log
### [PA] 2026-09-10 — Grooming
Glossary rule: a concept's row lands in the same PR that introduces it — the rows are in the grooming commit; this task is the consistency pass, not the author.
