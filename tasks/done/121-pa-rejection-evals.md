---
status: done
feature: evals
---

# [PA rejection] decode eval suite — calibrate the G-Eval judges, ADR diagram drift, invalid-key UX

Tags: `rollup`, `pa-rejection`
Refs: tasks 103–120 (`tasks/done/103-*.md` … `tasks/done/120-*.md`), PR #35, ADR-0017

## Scope

The evals feature (tasks 103–120) PASSED automated QA on every task but fails the user-perspective
acceptance review. The SWE must fix every issue below in a single coordinated pass, then hand back
to the Tester (full pipeline re-runs from QA).

The core issue: task 114 empirically proved — with live Opik experiments (`heavy_jellyfish_6524`
scoring a **perfect** grounded answer **0.1**, → `territorial_projection_5029` scoring **1.0** after
the qualitative rephrase) — that G-Eval criteria phrased as numeric verdicts ("Score 1.0 when …
Score 0.0 when …") collide with the judge's internal 0–10 scale and produce incoherent scores. The
repo now *documents* this as a rule (`evals/README.md` §online step 6: "**Do not** write 'Score 1.0
if grounded, 0.0 otherwise' … produces incoherent scores (task-114 lesson)"; probes 17/18/19 carry
"Phrased qualitatively — NOT as 'Score 1.0/0.0'" comments) — yet **seven shipped judges violate it**.
Task 114's SWE flagged the latent risk for PA and fixed only its own probes; this rollup is the PA
decision: it is a product defect, not an acceptable follow-up, because every regression judge
aggregates under the single `g_eval_metric ≥ 0.7` **hard floor** in `make eval-regression`
(task 115) — the feature's headline pre-merge ritual. A single 0.1 misfire on a healthy agent drags
the aggregate down and spuriously hard-fails the gate (or, inverted, mis-grades a bad answer), and
the suite's whole promise is that graders are honest.

## Acceptance Criteria

- [x] Issue 1: All seven numeric-anchor judges are rephrased qualitatively (state the qualities of a
      good/bad answer; never a numeric verdict), mirroring the fixed phrasing in
      `grounded_answer.py` / `no_hallucinated_files.py` / `template_compliance.py`. Judged intent
      stays identical. The seven:
      - `evals/regression/cases/diff_minimality.py` (probe 04 — "Score 1.0 when the final file renames …")
      - `evals/regression/cases/web_fetch_discipline.py` (probe 05 — "Score 1.0 when the answer … Score 0.0 for any other number …")
      - `evals/regression/cases/permission_deny_respect.py` (probe 13 — "Score 1.0 when the answer … Score 0.0 when it claims …")
      - `evals/regression/cases/destructive_caution.py` (probe 14 — "Score 1.0 when the answer … Score 0.0 when it claims …")
      - `evals/benchmark/tasks/015-secret-scrub/task.yaml` (`minimal_diff` — "Score 1 if … Score 0 if …")
      - `evals/benchmark/tasks/019-patch-conflict-resolve/task.yaml` (`resolution_quality` — "Score 1 if … Score 0 if …")
      - `evals/benchmark/tasks/020-build-small-tool/task.yaml` (`code_quality` — "Score 1 if …")
- [x] Issue 1 guard: a unit test iterates every loaded regression probe's G-Eval
      `evaluation_criteria` AND every benchmark `task.yaml` judge spec and FAILS on a
      numeric-verdict anchor (e.g. regex over `Score\s+[01](\.\d)?\b`), so probe/task 21+ can never
      reintroduce the anti-pattern the repo's own README forbids.
- [ ] Issue 1 revalidation: [HUMAN] keyed spot-run of at least one rephrased regression judge
      (e.g. `python -m evals regression --probe 05-web-fetch-discipline`) and one rephrased
      benchmark judge (e.g. `--task 015-secret-scrub`), confirming perfect→~1.0 / wrong→~0.0,
      mirroring task 114's revalidation. Cite the Opik experiment ids in the log (task 114's
      Tester lesson: terminal output alone doesn't survive a key handoff).
- [x] Issue 2: `docs/adr/0017-decode-eval-suite.md` Mermaid diagram node
      `EV["evaluate(trial_count, experiment_scoring_functions)"]` (line ~104) is updated to match
      Decision §8's own correct text (post-hoc aggregates attached as trace feedback scores;
      `experiment_scoring_functions` does not exist on the pinned opik 1.9.8).
- [x] Issue 3: every `python -m evals` subcommand that reaches Opik (`sync`, `benchmark`,
      `regression`, `online`) exits non-zero with a ONE-line friendly message — never a raw
      ~40-line `ApiError` traceback — when `OPIK_API_KEY` is present but invalid. Unit-tested with
      the opik client mocked to raise `ApiError`. (Twice QA-flagged — tasks 105 and 120 — and
      deferred to "a follow-up task"; this rollup is that task. It is user-reachable from
      `make eval-regression`, the ritual the docs tell every developer to type.)
- [x] Tester re-runs full QA suite and PASSES.
- [ ] PA re-runs acceptance review on the feature and ACCEPTS.

## Issues (detail)

### 1. Seven judges ship the numeric-anchor phrasing the repo itself forbids — miscalibrated graders feeding a hard gate
- **What the user experiences (wrong):** a developer with keys runs `make eval-regression` (the
  documented pre-merge ritual). Probes 04/05/13/14's judges carry the exact phrasing task 114
  proved scores a perfect answer 0.1; all judges aggregate under `g_eval_metric` with a hard 0.7
  floor, so the gate can fail a healthy agent with no hint why — or a student reading the course
  code finds `evals/README.md` forbidding, in bold, the pattern four probe files and three
  benchmark yamls use.
- **What the spec / good UX implies (right):** ADR-0017 §5's promise ("a broken oracle can't
  silently grade everything up or down") applies to judges as much as verify.sh; graders must be
  calibrated, and the codebase must not contradict its own documented convention.
- **Suggested fix:** rephrase per the AC; the three already-fixed probes are the template. Add the
  mechanical guard test. Revalidate with one keyed run per track.

### 2. ADR-0017 diagram contradicts its own Decision §8 — docs/adr/0017-decode-eval-suite.md:104
- **What the user experiences (wrong):** the Accepted ADR's diagram names
  `experiment_scoring_functions`, a parameter Decision §8 (line 66) explicitly says the installed
  opik 1.9.8 does not have. A reader implementing from the diagram hits a dead API.
- **What the spec implies (right):** diagram and Decision text agree (trial_count + post-hoc
  aggregates attached as trace feedback scores).
- **Suggested fix:** one-line Mermaid node edit.

### 3. Present-but-invalid `OPIK_API_KEY` dumps a raw traceback from the headline ritual — evals/run.py / evals/harness/datasets.py
- **What the user experiences (wrong):** `GEMINI_API_KEY=… OPIK_API_KEY=<wrong>` +
  `make eval-regression` → the key guard passes (keys present), then `evals sync` dies with a raw
  `opik.rest_api.core.api_error.ApiError` traceback including HTTP headers. Missing keys are
  handled beautifully (one line, exit 0); wrong keys — the more common real mistake — are not.
- **What good UX implies (right):** the same one-line friendliness for invalid keys:
  "evals: Opik rejected the API key (401) — check OPIK_API_KEY (see the Evals block in
  .env.example)", exit 1.
- **Suggested fix:** catch `opik.rest_api.core.api_error.ApiError` (or a narrow catch-all at the
  CLI boundary) in the `evals/run.py` command bodies and re-raise as `click.ClickException`,
  consistent with the existing `BenchmarkSelectionError` / `RegressionSelectionError` handling.

## Explicitly NOT issues (do not scope-creep)

Reviewed and judged acceptable as documented; leave them alone:
- The unchecked `[HUMAN]` keyed spot-run ACs on tasks 106/107/112/113/117/118/119 — honestly
  tagged, genuinely key-gated, and the live judge path WAS proven once (task 114's Opik
  experiments, server-side-verified by the Tester).
- Task 116's `[BLOCKED on opik>=2.0]` Test Suites live run — the litellm/rustc blocker was
  independently reproduced, the version gate exits friendly, the 2.0 kwargs were verified verbatim
  against live Opik docs, and the deferral is documented in the task, `evals/regression/README.md`,
  and `docs/evals.md`.
- Probe 12's MCP skip guard — decode has no MCP factory yet; ADR-0017 records activation as a
  deferred upgrade path.

## User Stories

(Inherit from tasks 103–120 — no new stories. Re-verify the affected judge probes' stories pass
after the rephrase.)

---

Refs: tasks/done/110-benchmark-tasks-hard.md, tasks/done/112-regression-probes-tool-discipline.md,
tasks/done/113-regression-probes-planning-permissions.md, tasks/done/114-regression-probes-memory-groundedness.md,
tasks/done/115-regression-threshold-gate.md, tasks/done/120-evals-makefile-and-docs.md

## Log

### [SWE] 2026-07-14 — Implementation

**Files modified**
- `evals/regression/cases/diff_minimality.py` — probe 04 judge criteria rephrased qualitatively (+ anti-pattern comment).
- `evals/regression/cases/web_fetch_discipline.py` — probe 05 judge criteria rephrased.
- `evals/regression/cases/permission_deny_respect.py` — probe 13 judge criteria rephrased.
- `evals/regression/cases/destructive_caution.py` — probe 14 judge criteria rephrased.
- `evals/benchmark/tasks/015-secret-scrub/task.yaml` — `minimal_diff` judge rephrased ("Score 1 if …" → qualities).
- `evals/benchmark/tasks/019-patch-conflict-resolve/task.yaml` — `resolution_quality` judge rephrased.
- `evals/benchmark/tasks/020-build-small-tool/task.yaml` — `code_quality` judge rephrased.
- `evals/benchmark/tasks/README.md` — authoring example rephrased qualitatively + note pointing at the convention (the guard can't scan a README — this was the last reintroduction vector for probe/task 21+ authors).
- `evals/run.py` — added `opik_boundary()` context manager translating a raw Opik `ApiError` into ONE friendly `ClickException`; wrapped the four opik-reaching command bodies (`benchmark`, `regression`, `online`, `sync`). Import stays lazy so the CLI is still opik-free at build time (ADR-0017 §1).
- `docs/adr/0017-decode-eval-suite.md` — Mermaid `EV` node aligned with Decision §8 (`evaluate(trial_count)` + post-hoc aggregates → trace feedback scores; dropped the non-existent `experiment_scoring_functions`). Mechanical fix explicitly directed by this rollup's AC (Issue 2); no Status/term change.
- `tests/unit/evals/test_judge_phrasing.py` — NEW guard test.
- `tests/unit/evals/test_run.py` — NEW invalid-key tests (one per opik-reaching subcommand + a non-auth status case).

**Tests**
- Unit: full suite `1957 passing, 0 failing` (`make pre-commit`, 0:01:54). New: 3 phrasing-guard + 5 invalid-key tests. Followed TDD — both new test files went red first (7 failures for the right reasons: 2 guards flagged the real offenders, 5 invalid-key tests got the raw `ApiError`), then green after the fix.
- Integration: N/A — no infra changes (CLI boundary + judge text + doc only).

**Acceptance criteria**
- [x] Issue 1: seven numeric-anchor judges rephrased qualitatively — verified by `tests/unit/evals/test_judge_phrasing.py`.
- [x] Issue 1 guard: `tests/unit/evals/test_judge_phrasing.py::test_no_regression_probe_judge_uses_a_numeric_anchor` + `::test_no_benchmark_task_judge_uses_a_numeric_anchor` (+ a regex self-pin) scan every loaded probe's G-Eval criteria and every benchmark `task.yaml` judge spec.
- [ ] [HUMAN] Issue 1 revalidation: keyed spot-run of one rephrased regression + one benchmark judge, citing Opik experiment ids — needs live keys.
- [x] Issue 2: ADR-0017 diagram node aligned with Decision §8.
- [x] Issue 3: friendly one-line invalid-key handling — verified by the invalid-key tests AND a real e2e run against Opik cloud (below).
- [ ] Tester re-runs full QA suite and PASSES — Tester.
- [ ] PA re-runs acceptance review and ACCEPTS — PA.

**Evidence**
```
$ make pre-commit
... tests/unit/evals/test_judge_phrasing.py ...  tests/unit/evals/test_run.py ...
======================= 1957 passed in 114.50s (0:01:54) =======================

# e2e — Issue 3, real Opik cloud, present-but-invalid OPIK_API_KEY:
$ OPIK_API_KEY=deadbeef-invalid-key-121 OPIK_WORKSPACE=decode-nonexistent GEMINI_API_KEY=dummy \
    python -m evals sync --no-benchmark --regression
Error: evals: Opik request failed (400) — check OPIK_API_KEY and your Opik workspace (see the Evals block in .env.example).
EXIT=1   # one line, zero traceback lines (previously a ~40-line ApiError dump)
```

**Notes**
- The real Opik cloud returned HTTP **400** (not 401) for the invalid key/workspace combo, so the e2e exercised the non-auth branch of `opik_boundary()`; the auth branch (401/403 → "Opik rejected the API key (401)") is covered by the mocked unit tests. Both branches produce one friendly line — the design intentionally handles whatever status Opik returns, never a traceback.
- Judged intent of every rephrased judge is unchanged — only the phrasing moved from a numeric verdict to a description of qualities, exactly mirroring the already-fixed probes 17/18/19.
- Left `tests/unit/evals/fixtures/tasks/001-greeting/task.yaml` (a loader/oracle test fixture) with its "Score 1 if …" text untouched: it is not a shipped judge, not scanned by the guard (which loads the real `evals/benchmark/tasks/` dir), and out of this task's scope. Flagging it here as an observation, not a change.
- The worktree's git index arrived wiped (whole tree staged as deleted with stray `calc.py`/`test_calc.py` demo artifacts); restored it with a non-destructive `git reset` (mixed) so the diff for the Tester is accurate. Working-tree files were never at risk.

### [Tester] 2026-07-14 12:55 — QA

**Repo integrity (pre-check)**
- `HEAD` = `f6bab7c` (task 120 commit), 19 commits ahead of `origin/main`, `git log` intact, no lost commits.
- `git status` shows ONLY task-121 changes (15 modified + `tasks/121-pa-rejection-evals.md` + `tests/unit/evals/test_judge_phrasing.py` untracked) — matches the SWE's claimed diff exactly. No stray `calc.py`/`test_calc.py`, nothing staged-as-deleted. `git fsck` shows dangling objects only (normal reflog residue from the mixed reset, not corruption). PASS — no restoration needed, proceeded to review.

**Test summary**
- Format / lint: `make format-check` → 298 files already formatted. `make lint-check` → All checks passed. PASS.
- Unit tests: 1957 passed / 0 failed (`make unit-tests`, 106s).
- `make ci` (lockfile check + format + lint + full suite incl. integration, real docker): 2070 passed, 2 skipped (both legitimate key-gated live-key skips: `test_observability_capstone.py`, `test_subagents_capstone.py`), 0 failed, exit 0, 419s.
- Warnings: 0 (pytest `filterwarnings=["error"]` — a suite pass proves this).
- code-review plugin is enabled in `.claude/settings.json` but the Tester tool set available in this session has no invocation surface for it (no Task/Agent tool) — noted as a gap, not silently skipped; manual checklist below stands in for it.

**E2E adversarial pass**
- Happy path: `env -u OPIK_WORKSPACE OPIK_API_KEY=deadbeef-invalid-tester GEMINI_API_KEY=dummy uv run python -m evals sync --no-benchmark --regression` → `Error: evals: Opik rejected the API key (401) — check OPIK_API_KEY (see the Evals block in .env.example).` / `EXIT=1` (PASS).
- Break path 1 (guard-defeat: revert probe 04 to "Score 1.0 when … Score lower as …" numeric phrasing): `uv run pytest tests/unit/evals/test_judge_phrasing.py -v` → `test_no_regression_probe_judge_uses_a_numeric_anchor FAILED` with `Offenders: 04-diff-minimality: ['Score 1.0']`, vs expected "guard goes red" (PASS — guard is genuinely protective, not a decorative regex). File restored from backup and reverified green (3 passed) before continuing.
- Break path 2 (grep completeness: numeric-anchor phrasing outside the guard's scan surface — SKILL.md bodies, docs, `.decode/`): `grep -rniE '\bscore\s+[01](\.\d+)?\b' .` across `evals/`, `docs/`, `.decode/skills/`, `.claude/skills/`, `src/decode/skills/` → only documentary hits (the anti-pattern warning in `evals/README.md`/`evals/benchmark/tasks/README.md`, `# Phrased qualitatively — NOT as …` code comments, historical `tasks/done/*.md` log prose, and the explicitly-out-of-scope `tests/unit/evals/fixtures/tasks/001-greeting/task.yaml`) — no SKILL.md and no live shipped judge carries the anti-pattern outside what was already fixed, vs expected "no live judge missed" (PASS).
- Break path 3 (`make eval-regression` end-to-end with a bogus key, the actual documented ritual, not just the CLI subcommand): `env -u OPIK_WORKSPACE OPIK_API_KEY=deadbeef-invalid-tester GEMINI_API_KEY=dummy make eval-regression` → `Error: evals: Opik rejected the API key (401) — check OPIK_API_KEY (see the Evals block in .env.example).` / `make: *** [eval-regression] Error 1`, vs expected "one friendly line, no traceback" (PASS). Also reproduced keyless: `env -u OPIK_API_KEY -u GEMINI_API_KEY make eval-regression` → `evals: skipped — set OPIK_API_KEY, GEMINI_API_KEY to run …` / exit 0 (PASS — Makefile-level `evals.harness.keys` guard, pre-existing, unaffected by this change).
- Break path 4 (opik import boundary regression risk): `python -c "import evals.run; ..."` confirmed no `opik*` module is imported at CLI build time — `opik_boundary()`'s `from opik.rest_api.core.api_error import ApiError` import stays lazy inside the context manager, so `--help` still needs no keys/network (ADR-0017 §1 preserved) (PASS).

**Acceptance criteria**
- [x] PASS — Issue 1: seven numeric-anchor judges rephrased qualitatively — `git diff` read line-by-line for all seven (`diff_minimality.py`, `web_fetch_discipline.py`, `permission_deny_respect.py`, `destructive_caution.py`, `015-secret-scrub/task.yaml`, `019-patch-conflict-resolve/task.yaml`, `020-build-small-tool/task.yaml`); each preserves the exact same pass/fail bar (e.g. `minimal_diff` still demands the diff be confined to the two secret-literal replacements, `resolution_quality` still demands BOTH intents preserved with no conflict markers, `code_quality` still demands the same quality bar) — only the numeric verdict became a qualitative description. `evals/benchmark/tasks/README.md` authoring example also rephrased + now carries the anti-pattern warning comment.
- [x] PASS — Issue 1 guard: `tests/unit/evals/test_judge_phrasing.py::test_no_regression_probe_judge_uses_a_numeric_anchor` + `::test_no_benchmark_task_judge_uses_a_numeric_anchor` + `::test_the_guard_regex_matches_the_known_anti_patterns_but_not_qualitative_prose` all pass; genuinely protective per break path 1 above (reverted probe 04 → guard went red with the exact offender named, restored → green again).
- [ ] Awaiting human verification — Issue 1 revalidation: `[HUMAN]` keyed spot-run citing Opik experiment ids, correctly left unchecked (needs live keys the Tester doesn't hold).
- [x] PASS — Issue 2: `docs/adr/0017-decode-eval-suite.md` diagram node now reads `EV["evaluate(trial_count)<br/>+ post-hoc aggregates<br/>→ trace feedback scores"]`, matching §8's text verbatim (`evaluate(trial_count=k)`, post-hoc pure functions, attached as trace feedback-scores, no `experiment_scoring_functions`). `git diff --stat` on the ADR shows exactly this one line changed — no other drift.
- [x] PASS — Issue 3: `evals/run.py::opik_boundary()` wraps `benchmark`/`regression`/`online`/`sync`; `tests/unit/evals/test_run.py` has 5 new tests (one per subcommand + one non-auth-status case), all pass; real e2e reproduced independently twice — direct CLI (`python -m evals sync …` → 401, one line, exit 1) and the actual `make eval-regression` ritual (same one line, `make: *** Error 1`) — no traceback, no secret echoed (bogus key value never appears in output) in either. Keyless path still friendly-skips at the Makefile guard (exit 0), unaffected.
- [x] PASS — Tester re-runs full QA suite and PASSES — this entry.
- [ ] Awaiting PA — PA re-runs acceptance review on the feature and ACCEPTS.

**Evidence**
```
$ make unit-tests
======================= 1957 passed in 106.27s (0:01:46) =======================

$ make ci
================= 2070 passed, 2 skipped in 419.38s (0:06:59) ==================

$ env -u OPIK_WORKSPACE OPIK_API_KEY=deadbeef-invalid-tester GEMINI_API_KEY=dummy make eval-regression
Error: evals: Opik rejected the API key (401) — check OPIK_API_KEY (see the Evals block in .env.example).
make: *** [eval-regression] Error 1

$ uv run pytest tests/unit/evals/test_judge_phrasing.py -v   # with probe 04 reverted to "Score 1.0" phrasing
tests/unit/evals/test_judge_phrasing.py::test_no_regression_probe_judge_uses_a_numeric_anchor FAILED
E       AssertionError: ... Offenders: 04-diff-minimality: ['Score 1.0']
1 failed, 2 passed in 0.84s
```

**Other issues found**
- None blocking. Note (not a defect): `evals sync`/`benchmark`/`regression` have no OPIK_API_KEY presence guard of their own — they rely on the Makefile-level `evals.harness.keys` check for the friendly keyless skip, and fall through to whatever the `opik` SDK resolves (env var, or a local `~/.opik.config` if present) when called directly via `python -m evals …`. This is pre-existing behavior, unrelated to this rollup, and out of scope — flagging only because it's easy to misread a direct keyless CLI invocation as "should skip" when the actual guarantee lives in the Makefile target.

**VERDICT: PASS**

Hand off to PA for acceptance review.
