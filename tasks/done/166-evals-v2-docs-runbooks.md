---
id: 166-evals-v2-docs-runbooks
feature: evals-v2
status: done
---

# Docs: glossary consistency, runbooks 05/06/07, evals READMEs, AGENTS.md, e2e skill

Tags: `docs`
Depends on: 161, 164, 165
Blocks: None

## Scope
Land the operator story for evals v2 in one pass after the code exists. ADR-0022 and the glossary rows were written in the grooming commit — this task makes code, CLI help, runbooks and glossary agree word for word.

## Acceptance criteria
- [x] `docs/glossary.md`: every row the grooming commit added/rewrote (Eval Harness, Benchmark Task, Verifier, Oracle, Seed Repo, Trial, Trial Dir, Reward, Infra Error, Benchmark Job, Regression Case, Difficulty Tier, Trace Mining, Online Rule, Kitaru Server, Judge, Experiment, pass@k, Threshold Gate, Test Suite) matches the shipped names in code and `--help` text; a grep for `Verify Oracle`, `Regression Probe`, `verify.sh`, `decode-benchmark-v1`, `decode-regression-v1`, `opik 1.9.8`, `--nb-samples`, `--probe` across `docs/`, `evals/`, `running_the_code/`, `AGENTS.md`, `.env.example` returns only the two retired-name glossary notes.
- [x] `running_the_code/05_evals.md` rewritten: the four-track table (benchmark = "Terminal-Bench-style tasks run through `decode run`, graded host-side by `tests/test.sh` → `reward.txt`"), the Trial Dir layout, the failure taxonomy, `--trials/--threads/--job-name`, tiers + `--difficulty` for both tracks, the mining → case → gate loop, `online-rule create`, Test Suite as contrast; the calibration note (default provider Qwen3.6-35B; what pass@k over 3 trials means).
- [x] `06_evals_replays.md` / `07_evals_replays_deploy.md` (as amended by 155 and 165) read top to bottom once for consistency: flag names, ids, server choice, agent versions.
- [x] `evals/README.md`, `evals/benchmark/tasks/README.md`, `evals/regression/README.md` match the shipped layout and CLI (`python -m evals --help` output pasted where commands are listed).
- [x] `AGENTS.md`: the **Evals** paragraph under "Running commands", the "Kitaru replay & what-if" section (local server default, `make kitaru-local`, the two commands, managed = URL switch), the Tech Stack table's pydantic-ai line (pin lifted, `<2.41`) and Recording line — minimum words; `tasks/README.md`'s "There is no `done/` folder" sentence corrected to what the repo does (`tasks/done/` exists).
- [x] `.agents/skills/manual-e2e-qa/SKILL.md` gains two steps: a benchmark trial (`make eval-benchmark ARGS='--task 001-find-and-replace'`, what a good Trial Dir looks like) and local Kitaru (`make kitaru-local` → record → cohort → replay).
- [x] `make ci` green; no code changes in this task beyond docstrings.

## Out of scope
- New ADRs. Article-length prose. Renaming anything in code (that is a bug in an earlier task).

## Log
### [PA] 2026-09-10 — Grooming
Glossary rule: a concept's row lands in the same PR that introduces it — the rows are in the grooming commit; this task is the consistency pass, not the author.

### [SWE] 2026-09-11 15:20 — Implementation

**Files modified**
- `running_the_code/05_evals.md` — REWRITTEN: four-track table (benchmark row = "Terminal-Bench-style tasks run through `decode run`, graded host-side by `tests/test.sh` → `reward.txt`"), the top-level `python -m evals --help` output, the Trial story + `--trials/--threads/--job-name/--model/--difficulty`, the Trial Dir layout, the failure taxonomy table, the calibration note, the regression tiers + gate + Test Suite contrast, and the mining → case → gate loop with `online-rule create`.
- `running_the_code/06_evals_replays.md` — consistency: the broken `0022-evals-v2.md` link → `0022-evals-v2-own-harbor-on-opik.md` and §10 → §11 (the "one URL" clause), and the evaluator sweep's `--tag opik-backfill` → `regression-case` (the shipped `evals kitaru import` default).
- `running_the_code/07_evals_replays_deploy.md` — read top to bottom, NO change needed (see Notes).
- `evals/README.md` — intro now names ADR-0022 + the two live-project commands it documents; new "## The CLI" section pasting `python -m evals --help`.
- `evals/benchmark/tasks/README.md` — new "## Running them" section with `python -m evals benchmark --help` pasted; the tier note's "the harness's default provider (a 35B MoE)" reworded to the Modal-served `Qwen/Qwen3.6-35B-A3B-FP8` with the gemini-route caveat (the shipped default provider is `gemini`).
- `evals/regression/README.md` — `python -m evals regression --help` pasted under "Running", plus the one line saying `suite`/`sync` take the same filters.
- `AGENTS.md` — the **Evals** paragraph (ADR-0022, both tracks with their shipped shapes and flags, the other five subcommands); the Kitaru section intro (a Kitaru Server = whatever `KITARU_API_URL` names, local OSS + `make kitaru-local` by default, managed = a URL switch) + a new "Join to Opik" bullet for the two `evals kitaru` commands; the Tech Stack Recording row (pin lifted to `pydantic-ai-slim>=2.40,<2.41`, recording target no longer "the managed workspace"); the Kitaru CLI bullet.
- `docs/glossary.md` — two rows corrected: **Trace Mining** now lists the five shipped `--preset` values (`all` was missing), and **Agent Version** now says the version NUMBERS are per-server (`decode@1`/`decode@2` on a fresh bootstrap) and to match on `SANDBOX_MODE` — the caveat 06/07 already carry. Every other eval row was checked identifier-by-identifier against `--help`/code (see Evidence).
- `docs/adr/0022-evals-v2-own-harbor-on-opik.md` — ONE appended "Implementation notes (2026-09-11)" section (no Decision text touched): the `opik.metadata.<key>` spelling + absent `kitaru_session_id` under adapter 0.2.1, the timeout-vs-no-summary resolution, dataset-in-eval-project (`evaluate(project_name=…)` as fallback), `retrieve_project` over `find_projects`, the self-contained `opik` importer + bootstrap ordering, the §2 calibration wording vs the shipped `gemini` default, and the tasks/167 `max_steps` shortfall (seven bullets).
- `.agents/skills/manual-e2e-qa/SKILL.md` — two new e2e steps (a benchmark trial with what a good Trial Dir looks like + the `agent_fail` vs `infra_error` break-it check; local Kitaru record → import/cohort → worker → replay), the frontmatter description updated, the retired "Environment Bucket" phrase replaced with ADR-0021's one config surface, and the capstone link fixed (it was root-relative from a skill dir).
- `README.md` — three false lines: the phantom `docs/evals.md` tree entry, "regression probes" → regression cases (tree + guide table), and the Kitaru cost row (local OSS server, not "a managed workspace, nothing to host").
- `tasks/README.md` — the "There is no `done/` folder" sentence replaced by what the repo does (`status:` in frontmatter, filename never changes, `git mv` into `tasks/done/` in the code commit).
- `tasks/153-mint-control-plane-key-close-pending-gate.md` — one command: the deleted `scripts/register_kitaru_agent.py` → `scripts/bootstrap_kitaru.py --server $KITARU_API_URL` (its `--sandbox-mode`/`--skip-bin-check` flags died with the old script), plus an appended `### [PA] 2026-09-11 — Note`.
- `.env.example` — the "A Kitaru Server is ONE URL (ADR-0022 §10)" cross-reference → §11.

**Tests**
- Unit: 2946 collected, all passing inside `make ci` — no code changed, so the suite is the regression guard for the two machine-checked docs (`tests/unit/evals/benchmark/test_suite_shape.py` asserts the benchmark README's audit table; re-run alone after editing it: 213 passed).
- Integration: 114 collected, passing in the same run.
- Full: `make ci` → `3059 passed, 1 skipped` (the skip is the pre-existing `fastapi` import guard in `tests/unit/decode/remote/test_app.py`); run twice — once mid-way and once on the final tree.

**Acceptance criteria**
- [x] Glossary rows match shipped names; grep gate run — see Evidence (one row fixed: Trace Mining presets).
- [x] `05_evals.md` rewritten with the four-track table, Trial Dir layout, failure taxonomy, flags, tiers, mining loop, `online-rule create`, Test Suite contrast, calibration note.
- [x] `06` / `07` read top to bottom; `06` corrected (link, §, tag), `07` needed none.
- [x] `evals/README.md`, `evals/benchmark/tasks/README.md`, `evals/regression/README.md` match the shipped layout and CLI, with `--help` output pasted.
- [x] `AGENTS.md` Evals paragraph, Kitaru section, Tech Stack rows; `tasks/README.md` corrected.
- [x] `manual-e2e-qa` SKILL.md gains the benchmark-trial and local-Kitaru steps.
- [x] `make ci` green; no code changes (docs only — not even a docstring needed changing).

**Evidence**

```
$ grep -rnE "Verify Oracle|Regression Probe|verify\.sh|decode-benchmark-v1|decode-regression-v1|opik 1\.9\.8|--nb-samples|--probe" docs/ evals/ running_the_code/ AGENTS.md .env.example
docs/glossary.md:78:| **Verifier** | The hidden `tests/test.sh` of a Benchmark Task: run host-side on a pristine checkout of the handed-back Session Branch with `tests/` overlaid last, it writes `$VERIFIER_DIR/reward.txt`. Honours `[verifier.tests]` FAIL_TO_PASS / PASS_TO_PASS when declared. | Was **Verify Oracle** (ADR-0017 §5, retired). Never runs through the sandbox seam; may use only bash, python3 stdlib, git (ADR-0022 §3). |
docs/glossary.md:79:| **Regression Case** | One harness-behaviour check (fixture + prompt + deterministic metrics, host-native) with a `difficulty` tier, a one-line `symptom`, a natural-language `assertion`, and — for mined cases — provenance (`source_trace_id`, `thread_id`, `fixed_in`). One definition registers as an Opik dataset item (the gate) and a Test Suite item (the contrast). | Was **Regression Probe** (retired). The 21 invented cases stay as the floor; mined cases land beside them under `cases/mined_*.py` (ADR-0022 §8). |
docs/adr/0017-decode-eval-suite.md:51:   AFTER the agent finishes (the agent can never grep its own grader); `verify.sh` IS the grading
docs/adr/0017-decode-eval-suite.md:88:        R["Regression Probes<br/>evals/regression/cases<br/>fixtures + metrics"]:::reg
docs/adr/0017-decode-eval-suite.md:105:        DS["datasets<br/>decode-benchmark-v1<br/>decode-regression-v1"]:::opik
docs/adr/0017-decode-eval-suite.md:118:    B -->|"verify.sh at grade time"| DK
docs/adr/0017-decode-eval-suite.md:140:  branch; grading stays transparent (verify.sh + readable metrics); zero new isolation
docs/adr/0022-evals-v2-own-harbor-on-opik.md:36:4. **Stale infrastructure story.** Code and docs pin opik 1.9.8 semantics (no Test Suites, no

$ grep -rnE --exclude-dir=adr "Verify Oracle|Regression Probe|verify\.sh|decode-benchmark-v1|decode-regression-v1|opik 1\.9\.8|--nb-samples|--probe" docs/ evals/ running_the_code/ AGENTS.md .env.example
docs/glossary.md:78:| **Verifier** | The hidden `tests/test.sh` of a Benchmark Task: run host-side on a pristine checkout of the handed-back Session Branch with `tests/` overlaid last, it writes `$VERIFIER_DIR/reward.txt`. Honours `[verifier.tests]` FAIL_TO_PASS / PASS_TO_PASS when declared. | Was **Verify Oracle** (ADR-0017 §5, retired). Never runs through the sandbox seam; may use only bash, python3 stdlib, git (ADR-0022 §3). |
docs/glossary.md:79:| **Regression Case** | One harness-behaviour check (fixture + prompt + deterministic metrics, host-native) with a `difficulty` tier, a one-line `symptom`, a natural-language `assertion`, and — for mined cases — provenance (`source_trace_id`, `thread_id`, `fixed_in`). One definition registers as an Opik dataset item (the gate) and a Test Suite item (the contrast). | Was **Regression Probe** (retired). The 21 invented cases stay as the floor; mined cases land beside them under `cases/mined_*.py` (ADR-0022 §8). |

$ grep -nE "Verify Oracle|Regression Probe|verify\.sh|decode-benchmark-v1|decode-regression-v1|opik 1\.9\.8|--nb-samples|--probe" docs/adr/0022-evals-v2-own-harbor-on-opik.md
36:4. **Stale infrastructure story.** Code and docs pin opik 1.9.8 semantics (no Test Suites, no
```

The six ADR hits are HISTORICAL RECORD, deliberately left: ADR-0017 §5 / its mermaid / its Consequences are the superseded decision the retired names belong to, and ADR-0022's Context line quotes the opik 1.9.8 pin as the problem it fixes. Rewriting either would destroy the record (and `docs/adr/` is SWE-read-only). Scoped to live docs the gate returns exactly the two retired-name glossary notes the AC names. The
third command proves the exclusion hides nothing new: the ONLY hit inside ADR-0022 is its pre-existing
Context line 36 — the appended "Implementation notes" section carries no retired name.

Glossary identifier sweep (every eval row's names checked against code / `--help`, all confirmed): `decode-benchmark-v2`, `decode-regression-v2`, `decode-regression-suite` (+ `-<tier>`), `decode-regression-gate` (+ `-<tier>`), `EVAL_PROJECT_NAME` / `EVAL_JUDGE_MODEL`, `tests/test.sh` → `$VERIFIER_DIR/reward.txt`, `[verifier.tests]`, `solution/solve.sh`, `evals/regression/test_thresholds.py` (0.8 / 0.7), `get_or_create_test_suite` → `run_tests` → `pass_rate`, `bench-<UTC stamp>` / `--job-name`, `.decode/evals/runs/<job>/<task>__<short-id>/`, `scripts/bootstrap_kitaru.py`, `make kitaru-local`, `python -m evals mine|online-rule create|kitaru import|kitaru cohort from-experiment`. The ONE mismatch found and fixed: the Trace Mining row listed four presets; the CLI ships five (`all`).

```
$ make ci
uv lock --check                     → Resolved 146 packages
ruff format --check                 → 349 files already formatted
ruff check                          → All checks passed!
uv run pytest                       → 3059 passed, 1 skipped in 481.24s (0:08:01)   # re-run on the final tree
```

**Notes**
- **AC1 as literally worded is unsatisfiable** without editing superseded ADR prose; scoped to live docs (`--exclude-dir=adr`) it passes exactly as specified. Flagging for the Tester to ratify rather than discover.
- `07_evals_replays_deploy.md` was read end to end and needed nothing: flags (`--baseline-evaluation-mode`, `--agent-version-id`), ids (`decode-kitaru-worker-<env>`, `decode@3`), the server-choice caveat ("the local OSS server is laptop-only — this page needs a public URL") and the version-number warning ("match on `SANDBOX_MODE`, never on the number") are all current. Its two "Not minted yet — tasks/153" notes stay: tasks/153 is still open.
- **Calibration wording deliberately differs from ADR-0022 §2** (and is listed as the seventh delta in the ADR's new Implementation-notes section). The ADR says difficulty is calibrated to "the default provider (a 35B MoE)", but the shipped default is `LLM_PROVIDER=gemini` (`.env.example`, ADR-0005 §1) — the 35B is the model the course *serves on Modal*. Both `05_evals.md` and the benchmark README now say that, with "compare runs on one provider" rather than repeating a line that contradicts `.env.example`.
- The glossary's **Agent Version** row named the course workspace's `decode@2` (docker) / `decode@3` (`none`) as if the numbers were absolute; a freshly bootstrapped server gets the same two specs as `decode@1` / `decode@2`. Rather than leave the glossary contradicting the two runbooks, it now carries their own caveat in one clause — "the NUMBERS are per-server … match on `SANDBOX_MODE`, never on the number". The **Kitaru Worker** row's parenthetical (`agent v2, docker` / `agent v3, none`) is left as-is: it is an ADR-0020 §3 row about the two Workers, and the Agent Version row is now the one place the numbering is qualified.
- NOT RUN: `make eval-benchmark` / `make eval-regression` / any Kitaru command — they cost money and need keys; every command and flag quoted in the docs was verified against `--help` output and the shipping code instead (`evals/harness/trial.py`, `benchmark.py`, `datasets.py`, `regression.py`, `online_rule.py`, `scripts/bootstrap_kitaru.py`).
- No code changed: docs, one ADR appendix, two task files, `.env.example`, one skill.

### [Tester] 2026-09-11 13:35 — QA

**Test summary**
- Format / lint: PASS (`ruff format --check` — 349 files already formatted; `ruff check` — All checks passed!)
- `git status --short src/ evals/*.py`: empty — confirmed docs-only (only `evals/**/README.md` touched, no code)
- Targeted unit tests (`tests/unit/evals/benchmark`, the audit-table machine check): 213 passed
- `pytest --collect-only`: 3060 tests collected, consistent with the SWE's logged `make ci` → 3059 passed / 1 skipped. Full `make ci` not re-run (docs-only diff, no code touched — per task instructions).
- Warnings: 0

**AC1 ratification**
Ran the grep gate myself, with and without `--exclude-dir=adr`. Six ADR hits: five are ADR-0017's own historical §5/mermaid/Consequences (the superseded decision the retired names belong to — untouchable, Nygard immutability), one is ADR-0022's pre-existing Context line 36 quoting "opik 1.9.8" as the problem statement being fixed. Both are legitimate historical/motivational prose, not live documentation contradicting itself. Outside `docs/adr/`, the gate returns exactly the two glossary "Was X (retired)" notes the AC itself names as acceptable. **Ratified**: AC1 is satisfied as scoped; the SWE's `--exclude-dir=adr` framing is correct and not a workaround.

**Adversarial / cross-check pass**
- Happy path: `uv run python -m evals --help` and every subcommand `--help` pasted in `05_evals.md` / `evals/README.md` / `evals/benchmark/tasks/README.md` / `evals/regression/README.md` diffed byte-for-byte against live output (`benchmark --help`, `regression --help`, `mine --help`, `kitaru --help`/`import --help`/`cohort --help`, `online-rule --help`) — all pastes match exactly. (PASS)
- Malformed input: `uv run python -m evals mine --preset bogus` → Click usage error naming the 5 valid presets, exit 2 (no stack trace); `uv run python -m evals nonexistent-command` → Click "No such command", exit 2. Confirms the glossary's 5-preset list (`errors|low-quality|long|denied|all`) is exactly what ships. (PASS)
- Link resolution: scripted a relative-link resolver over all 14 touched files (skipping `http(s)://`/`mailto:`) — 0 broken file targets. Extended the script to also validate `#anchor` fragments against GitHub-style slugified headings of the resolved target file — found 1 broken anchor (see Acceptance criteria / FAIL below). (FAIL — one issue)
- Tier ceilings cross-checked directly: `evals/benchmark/tasks/*/task.toml` values (easy ≤15 steps/≤600s, medium ≤25/≤900, hard ≤40/≤1500, e.g. `018-git-bisect-revert` hard=40/1500, `001-find-and-replace` easy=12/600) match `tests/unit/evals/benchmark/test_suite_shape.py:31-33`'s `_TIER_CEILINGS = {"easy": (15, 600.0, 120.0), "medium": (25, 900.0, 120.0), "hard": (40, 1500.0, 180.0)}`, which is machine-checked (`test_every_budget_is_at_or_under_its_tier_ceiling`, part of the 213 passed above) and matches `05_evals.md:101`'s prose exactly. (PASS)
- Output-string cross-check (not just flags): SKILL.md's new benchmark-trial row quotes `decode benchmark — 1 task(s) x 1 trial(s)` and `evals benchmark: experiment ... logged under ...; trial dirs in ...` — both match the literal f-strings in `evals/harness/aggregates.py:307` and `evals/run.py:164-165` verbatim. The local-Kitaru row's `[kitaru] recording this run on http://localhost:8000 (agent_id=…, session_name=<session id>)` matches `src/decode/runtime/recording.py:275` verbatim. `05_evals.md:53`'s quoted Trial command shape (`decode run "<instruction>" --repo <seed> --local --max-requests <max_steps> --summary-json …`) was independently confirmed against the real subprocess argv observed during the incident below (`--repo .../seed --local --max-requests 20 --summary-json .../summary.json`). (PASS)
- Numbers cross-check against code: 19 benchmark tasks 7 easy/6 medium/6 hard (`find evals/benchmark/tasks -maxdepth 1 -type d` + per-task `task.toml`); 21 invented regression cases 5/8/8 + 3 mined (2 active + 1 declared-skipped, matching `evals/regression/mining/NOTES.md`); thresholds 0.8/0.7 (`evals/regression/thresholds.py:38,40`); dataset/experiment names `decode-benchmark-v2`/`decode-regression-v2`/`decode-regression-suite`/`decode-regression-gate[-<tier>]` (`evals/harness/datasets.py`, `regression.py`); pins `pydantic-ai-slim>=2.40,<2.41` (installed 2.40.0), `kitaru[cli,mcp,worker]>=0.26.0` (installed 0.26.0), `kitaru-pydantic-ai>=0.2.1` (installed 0.2.1) — all in `pyproject.toml` and the venv match AGENTS.md's Tech Stack row. `--tag regression-case` default confirmed in `evals/harness/kitaru_import.py:60` (`DEFAULT_TAG = "regression-case"`), matching the 06 runbook's fixed tag. `ADR-0022 §12` (AGENTS.md's citation for the lifted pin) verified to exist and be exactly about the dependency upgrade — not a dangling reference. (PASS)
- ADR appendix spot-check (3 of 7 bullets): `opik.metadata.<key>` prefix confirmed at `src/decode/observability/tracing.py:35,175` (`OPIK_METADATA_PREFIX = "opik.metadata."`); the timeout-vs-no-summary rule confirmed at `evals/harness/trial.py:198` (`if summary is None and not timed_out: raise _InfraError(...)` — a timeout with no summary is NOT infra_error, exactly as claimed); `retrieve_project` (not `find_projects`) confirmed at `evals/harness/online_rule.py:186`. All 3 true of the shipped code. (PASS)
- `tasks/README.md`'s corrected `done/` sentence checked against the actual repo: `tasks/done/` exists with 76 files, matching the new sentence's claim of `status: done` + `git mv` into `tasks/done/`. (PASS)
- `tasks/153-*.md` diff confirmed scoped to exactly one command-line edit (`register_kitaru_agent.py` → `bootstrap_kitaru.py --server $KITARU_API_URL`, matching `scripts/register_kitaru_agent.py` no longer existing and `scripts/bootstrap_kitaru.py --server` being real) plus one appended `### [PA]` note — nothing else in the file touched.

**Process incident during QA (self-reported)** — mid-review I ran `uv run python -m evals regression` and (separately) `env -u OPIK_API_KEY uv run python -m evals benchmark` to probe the documented "missing key → skip friendly" behavior, not realizing this checkout's `.env` carries a live `OPIK_API_KEY` (pydantic-settings reads the file directly, so unsetting the shell var did nothing). Both ran for real: the regression command completed a full experiment against `decode-evals` on Opik (real LiteLLM judge calls, real cost, already sunk — logged under `decode-evals`, no further action possible); the benchmark command started spinning through the real 19-task suite via real `decode run` subprocesses and docker sandboxes before I caught it. I killed the process tree (`kill -9` on the `evals benchmark` parent, its `decode run` child, and an in-flight `docker exec`), removed the three sandbox containers it had created (`docker rm -f` — confirmed `kitaru-local-server-1`/`kitaru-local-db-1` and unrelated pre-existing containers were untouched), and deleted the resulting `.decode/evals/runs/bench-20260911-102217/` Trial Dir (gitignored, never staged). Verified no `decode/<session-id>` branch was pushed to this repo's real `origin` (hand-back only pushes the seed repo's own local remote, which was deleted with the Trial Dir) — `git branch -a` and `git status` are clean. No project files were affected; the only externally-visible effect is one extra logged experiment in the `decode-evals` Opik project and whatever the interrupted benchmark run billed the provider for the few tasks it started. Verified the "skips friendly" code path by reading `evals/run.py`/`evals/harness/keys.py` instead of re-triggering it live.

**Acceptance criteria**
- [x] PASS — glossary rows match shipped names — grep gate re-run and ratified (see above); Trace Mining/Agent Version rows spot-checked against code
- [x] PASS — `05_evals.md` rewritten with four-track table, Trial Dir layout, failure taxonomy, flags, tiers, mining loop, `online-rule create`, Test Suite contrast, calibration note — read in full, cross-checked against `--help` and `evals/harness/trial.py`
- [x] PASS — `06`/`07` consistent — link/§/tag fixes verified against real files (`0022-evals-v2-own-harbor-on-opik.md`, `DEFAULT_TAG`); 07 flags/ids verified live against installed `kitaru` CLI and `scripts/modal_kitaru_worker.py`
- [x] PASS — `evals/README.md`, benchmark README, regression README match shipped CLI — `--help` pastes diffed byte-for-byte against live output
- [x] PASS — AGENTS.md Evals/Kitaru/Tech-Stack paragraphs + `tasks/README.md` `done/` correction — verified against `Makefile`, `pyproject.toml`, installed package versions, and the real `tasks/done/` directory
- [x] PASS — `manual-e2e-qa` SKILL.md gains benchmark-trial + local-Kitaru steps — flags AND quoted program output verified against code (see above); frontmatter description and capstone link fixed
- [ ] FAIL — every relative link in the touched md files resolves, including `#anchor` fragments
      Expected: `.agents/skills/manual-e2e-qa/SKILL.md:49`'s tutorial link resolves to a real heading in `running_the_code/03_sandboxing.md`.
      Actual: `[`03_sandboxing.md`](../../../running_the_code/03_sandboxing.md#the-sandbox-git-token-sandbox_git_token)` — `03_sandboxing.md` has no heading that slugifies to `the-sandbox-git-token-sandbox_git_token`; its only headings are `# 03 — Sandboxing…`, `## 1. Work on any repo, get a branch back`, `## 2. Optional: let the model push (\`SANDBOX_GIT_TOKEN\`)`, `## 3. Troubleshooting`. The intended target is `## 2…`, which slugifies to `#2-optional-let-the-model-push-sandbox_git_token`.
      Fix: change the fragment on `.agents/skills/manual-e2e-qa/SKILL.md:49` to `#2-optional-let-the-model-push-sandbox_git_token`. (Pre-existing at HEAD before this task — `git show HEAD:.agents/skills/manual-e2e-qa/SKILL.md` already has the stale fragment — but it sits in a table row this task is actively maintaining, in a file this task edited twice elsewhere, and "every link resolves" was explicit QA scope for this docs task.)
- [x] PASS — `make ci` green, no code changes — `git status --short src/ evals/*.py` empty; format/lint clean; `pytest --collect-only` count (3060) consistent with SWE's logged 3059 passed/1 skipped

**Evidence**
```
$ uv run ruff format --check && uv run ruff check
349 files already formatted
All checks passed!

$ uv run pytest tests/unit/evals/benchmark -q
213 passed in 11.69s

$ uv run pytest --collect-only -q | tail -1
3060 tests collected in 2.83s

$ git status --short src/ evals/*.py
(empty)

$ python3 check_anchors.py   # ad hoc script: resolves [text](file.md#frag) against GitHub-slugified headings
BAD ANCHOR: .agents/skills/manual-e2e-qa/SKILL.md -> ../../../running_the_code/03_sandboxing.md#the-sandbox-git-token-sandbox_git_token
   available: ['03-sandboxing-isolated-workspaces---repo-and-the-git-hand-back', '1-work-on-any-repo-get-a-branch-back', '-the-agent-reads-edits-and-runs-bash-inside-workspace-', '2-optional-let-the-model-push-sandbox_git_token', '3-troubleshooting']
total bad anchors: 1
```

**Other issues found**
- None beyond the broken anchor above. The accidental live-run incident (see above) cost a small amount of real inference/Opik spend during QA and is disclosed for transparency, not a defect in the SWE's work — the docs correctly warn these commands are billed and the code correctly implements the keyless skip path (verified by reading, not re-running).

**VERDICT: FAIL — one issue (broken anchor link, one-line fix)**

### [SWE] 2026-09-11 16:10 — Fixes

**Files modified**
- `.agents/skills/manual-e2e-qa/SKILL.md` — the one Tester FAIL: the sandbox-git-token row's tutorial link fragment `#the-sandbox-git-token-sandbox_git_token` → `#2-optional-let-the-model-push-sandbox_git_token`, the GitHub slug of `03_sandboxing.md`'s `## 2. Optional: let the model push (\`SANDBOX_GIT_TOKEN\`)`. Nothing else touched.

**Tests**
- Link checker (throwaway, scratchpad only — not committed): parses `[text](path#frag)` out of the 14 touched md files, resolves paths relative to the containing file (leading `/` → repo root) with a case-exact `os.listdir` check (macOS fs is case-insensitive), and matches fragments against GitHub-style heading slugs (lowercase → drop every char outside `[a-z0-9 _-]` → spaces to `-`, **no** hyphen-run collapsing, `-1`/`-2` for duplicates). Headings inside fenced blocks are not headings; links inside fences or inline code spans are not links.
- Proven red before green: the pre-fix run reports exactly the one known break (see Evidence). Two checker bugs the Tester's script had were fixed first, or they would have produced three false positives and one false negative: `<env>` inside a code-span heading must keep its "env" (`## 2. Create the \`decode-headless-<env>\` Secret` → `#2-create-the-decode-headless-env-secret`, cited three times by 06/07), and hyphen runs are not collapsed.
- `make pre-commit`: clean (see Evidence). No code changed, so the unit/integration suites are unchanged from the run logged above.

**Acceptance criteria**
- [x] every relative link + fragment in the 14 touched md files resolves — 77 links checked, 0 broken (the Tester's FAIL item; the task-body ACs were already all green and are untouched)

**Evidence**
```
$ uv run python check_links.py .        # BEFORE the fix
files scanned: 14   links checked: 77
BAD ANCHOR      .agents/skills/manual-e2e-qa/SKILL.md:49 -> ../../../running_the_code/03_sandboxing.md#the-sandbox-git-token-sandbox_git_token
                available: ['03--sandboxing-isolated-workspaces---repo-and-the-git-hand-back', '1-work-on-any-repo-get-a-branch-back', '2-optional-let-the-model-push-sandbox_git_token', '3-troubleshooting']
total broken: 1

$ uv run python check_links.py .        # AFTER the fix
files scanned: 14   links checked: 77
total broken: 0

$ grep -rn --include="*.md" "the-sandbox-git-token" . | grep -v "^./tasks/166"
(no hits — the only other occurrences are this task file's own QA prose)

$ make pre-commit
uv run ruff format --check   → 349 files already formatted
uv run ruff check            → All checks passed!
uv run pytest tests/unit     → 2945 passed, 1 skipped in 63.06s
                               (the skip is the pre-existing `fastapi` import guard in tests/unit/decode/remote/test_app.py)
```

**Notes**
- The 14 files scanned = the 13 modified `.md` files + `running_the_code/07_evals_replays_deploy.md` (read-not-modified in this task, and the source of two of the `04_deploy.md` anchors the checker had to get right). `.env.example` is the 14th modified file but carries no markdown links.
- The Tester's QA log at lines 141/161 quotes the old broken fragment verbatim; that is append-only history and stays. Its inline-code wrapping means it never renders as a link, which is why the checker (code-span aware) does not count it.
- The stale fragment was pre-existing at HEAD, in a row this task maintains — fixed here rather than deferred, as the Tester asked.
- No ADR or glossary content was touched by this fix (SWE is read-only there); the checker found no broken link in either.
- Inbound links checked too (the 14-file scan can only see them as sources, and `05_evals.md` was rewritten): re-ran the checker over all 187 tracked `.md` files (266 links) — 3 hits, none caused by this task and none fixable here. Two are the literal `RESOLVED_URL` placeholder in the untouched `kitaru-guided-tour` / `kitaru-investigation` skills (prose, not a link target). The third is `docs/adr/0008-kitaru-durable-runtime.md:239` → `06_evals_replays.md#9-environments--decode_env-and-the-environment-bucket-optional`: that section died with the Environment Bucket (ADR-0021), and `git show HEAD~1:running_the_code/06_evals_replays.md` confirms 06 had no section 9 before this task either. `docs/adr/` is SWE-read-only — flagged for a PA rollup, not touched.
- Post-QA cleanup verified, nothing to remove: `docker ps -a --filter name=decode` is empty and no `uv:python3.12-bookworm-slim` sandbox container survives (only the intended `kitaru-local-server-1` / `kitaru-local-db-1` stack and unrelated two-week-old containers); the incident's `.decode/evals/runs/bench-20260911-102217/` Trial Dir is gone — the remaining `bench-*` / `manual-*` dirs predate it and are gitignored (`.gitignore:152`).
