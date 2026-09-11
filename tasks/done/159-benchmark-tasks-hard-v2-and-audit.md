---
id: 159-benchmark-tasks-hard-v2-and-audit
feature: evals-v2
status: done
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
- [x] Six folders `015`–`020` on the v2 layout per the table; `judges:` gone from the suite entirely (loader already rejects it — a unit test asserts no `task.yaml`/`judges` remains under `evals/benchmark/tasks/`).
- [x] Instructions rewritten against the checklist (exact deliverables; self-check command; 017/019 name `python3 -m unittest -v <module>`; 018 names `git revert` and the "Revert" subject; 015 names `os.environ["API_KEY"]`/`DB_PASSWORD` and says "change only service.py").
- [x] `make ci` green: oracle-sanity over all 19 both directions; canary test; `tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py` ports every case from the deleted `_hard.py` + the judge-replacement checks (015 bloated diff → 0; 019 markers left → 0; 018 amended history → 0). QA gap closed: 019's scan now covers all THREE markers, and `test_019_rejects_a_stray_conflict_separator` pins the lone `=======` the Tester found earning 1.0.
- [x] `tests/unit/evals/benchmark/test_suite_shape.py`: exactly 19 tasks load, 7 easy / 6 medium / 6 hard; every task's `max_steps`/timeouts ≤ its tier ceiling; every `category` in the taxonomy; every instruction ≤ 250 words and free of `tests/`, `solution/`, `reward`, `verif`; every test-bearing task (007, 009, 014, 016, 017, 019, 020) declares non-empty `[verifier.tests].fail_to_pass`.
- [x] Sandbox-image check recorded: the log entry lists `which` results for `make`, `python3`, `git`, `sqlite3`, `pytest` in the sandbox image and any instruction adjusted accordingly.
- [x] `git diff --stat` shows no change outside `evals/benchmark/tasks/015..020`, `tests/unit/evals/benchmark/`, and `evals/benchmark/tasks/README.md` (audit table appended: one row per task with tier, category, steps, F2P count).

## Out of scope
- Running through `decode run` (160). Re-tiering tasks (tiers are fixed: 7/6/6).

## Log
### [PA] 2026-09-10 — Grooming
A judge audits a verifier, it never grades of record (ADR-0022 §6). Two of the three judges encoded checkable requirements (015 minimality → diff bound; 019 both intentions → exact string); 020's code quality is dropped rather than faked. Hard-tier caps (35–40 steps, 20–25 min) are sized so a 35B model finishes sometimes — pass@k over trials is the sensitivity knob, not the cap.

### [SWE] 2026-09-11 05:05 — Implementation

**Files modified**
- `evals/benchmark/tasks/015-secret-scrub/` — v2 layout; Verifier now measures the diff bound
  (`git diff --numstat <root>` base→worktree + untracked scan: only `service.py`, ≤ 8 added+deleted)
  ahead of the literal scan / env-set / env-unset checks; `minimal_diff` judge deleted.
- `evals/benchmark/tasks/016-implement-from-spec/` — v2 layout; hidden `tests/test_intervals.py`
  (`unittest`, 8 cases, all F2P) replaces `verify/_verify_intervals.py`.
- `evals/benchmark/tasks/017-flaky-test-hunt/` — v2 layout; seed `test_registry.py` is `unittest`
  style; hidden `tests/test_registry.py` + `tests/_verify_suite.py` run both node ids in BOTH orders,
  twice each, a fresh process per run.
- `evals/benchmark/tasks/018-git-bisect-revert/` — v2 layout; `environment/setup.sh` adopts its own
  history (now `commit.gpgsign false` + a `unittest`-style `test_calc.py`); Verifier = hidden suite +
  newest NON-capture subject starts `Revert` + seeded history present unchanged; `solution/solve.sh`
  does the revert itself, so the `make_gold.sh` hack is deleted.
- `evals/benchmark/tasks/019-patch-conflict-resolve/` — v2 layout; hidden `tests/test_greet.py`
  (`unittest`, 2 F2P) + conflict-marker scan replace the `resolution_quality` judge.
- `evals/benchmark/tasks/020-build-small-tool/` — v2 layout; hidden `tests/test_wordfreq.py`
  (`unittest`, 5 F2P, drives the CLI as a subprocess); `code_quality` judge dropped, not replaced.
- `evals/benchmark/tasks/README.md` — stale "thirteen tasks / 015-020 still legacy" paragraph fixed;
  **Audit table** appended (one row per task: tier, category, steps, agent s, verifier s, F2P count,
  what the Verifier measures) + the note on the three dead judges.
- `tests/unit/evals/benchmark/test_suite_shape.py` — NEW: 19 tasks, 7/6/6, tier ceilings, taxonomy,
  ≤ 250-word instructions free of `tests/` / `solution/` / `reward` / `verif`, F2P declared by the
  seven test-bearing tasks, no `task.yaml`/`judges` anywhere, every folder loads, README rows exist.
- `tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py` — NEW: all 15 v1 `_hard.py` cases
  ported (see the module docstring's mapping) + 015 bloated-diff → 0, 015 unrelated-new-file → 0,
  015 literal-left-in-a-comment → 0, 018 rewritten-history → 0, 018 capture-commit-on-top → 1, and
  the node-id drift guard for 016/017/019/020 (scans the whole hidden `tests/` tree, since 017 hands
  its ids to `_verify_suite.py`).
- `tests/unit/evals/benchmark/conftest.py` — `grade_workspace` gained an optional `post_setup` bash
  snippet, because 018's answer is a git ACTION, not a file to drop in.

**Per-task table (as landed)**

| Task | Tier | Category | steps | agent s | verif s | F2P | Verifier |
|---|---|---|---|---|---|---|---|
| 015-secret-scrub | hard | Security | 30 | 1200 | 120 | 0 | diff bound + literal scan + env set/unset |
| 016-implement-from-spec | hard | Software | 35 | 1200 | 120 | 8 | hidden `unittest` suite |
| 017-flaky-test-hunt | hard | Software | 35 | 1200 | 180 | 2 | hidden suite, both orders × 2, fresh processes |
| 018-git-bisect-revert | hard | Operations | 40 | 1500 | 180 | 0 | hidden suite + revert-on-top + history unchanged |
| 019-patch-conflict-resolve | hard | Software | 35 | 1200 | 120 | 2 | no markers + hidden greeting suite |
| 020-build-small-tool | hard | Software | 40 | 1500 | 120 | 5 | hidden CLI suite |

**Tests**
- Unit: 2630 passed, 1 skipped (`make unit-tests`); benchmark slice 212 — oracle-sanity 19 tasks ×
  2 directions + fixture, canary, easy/medium/hard edge cases, suite shape.
- Integration: 115 passed in 395 s (`make integration-tests`, real docker).
- `uv lock --check`, `make format-check`, `make lint-check` clean — i.e. every leg of `make ci`.

**Acceptance criteria**
- [x] Six folders on the v2 layout; `judges:`/`task.yaml` gone —
      `test_suite_shape.py::test_no_task_yaml_or_judge_survives_in_the_suite`.
- [x] Instructions rewritten (self-check commands proven in the sandbox image, below).
- [x] `make ci` legs green; `test_verifier_edge_cases_hard.py` ports every v1 case + the
      judge-replacement probes.
- [x] `test_suite_shape.py` pins 19 / 7-6-6 / ceilings / taxonomy / ≤ 250 words / forbidden words /
      F2P for 007, 009, 014, 016, 017, 019, 020.
- [x] Sandbox-image `which` results recorded (below).
- [x] Scope: `git status --porcelain` + `git diff --stat` touch only `tasks/015..020`,
      `tests/unit/evals/benchmark/` and the tasks `README.md` (plus this task file).

**Evidence**

```
$ docker run --rm ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
    bash -lc 'for c in make python3 git gh sqlite3 pytest bash; do ...; done'
make     NOT FOUND
python3  /usr/local/bin/python3
git      NOT FOUND      <- base image only; decode's docker backend apt-get installs git + gh in
gh       NOT FOUND         EVERY worker (src/decode/sandbox/docker_backend.py::_install_git,
sqlite3  NOT FOUND         best-effort, ~20 s), which is what 018's `git revert` relies on
pytest   NOT FOUND
bash     /usr/bin/bash
stdlib sqlite3+unittest ok
```

```
$ uv run python - (seed + grade each converted task, both directions)
015-secret-scrub             tier=hard steps=30 f2p=0  oracle=1.0  nop=0.0
016-implement-from-spec      tier=hard steps=35 f2p=8  oracle=1.0  nop=0.0
017-flaky-test-hunt          tier=hard steps=35 f2p=2  oracle=1.0  nop=0.0
018-git-bisect-revert        tier=hard steps=40 f2p=0  oracle=1.0  nop=0.0
019-patch-conflict-resolve   tier=hard steps=35 f2p=2  oracle=1.0  nop=0.0
020-build-small-tool         tier=hard steps=40 f2p=5  oracle=1.0  nop=0.0
```

```
$ # every instruction's self-check command, run in the REAL sandbox image on the gold seed
015: API_KEY=k DB_PASSWORD=p python3 -c "import service; ..."   -> k p
016: python3 -c "from intervals import merge_intervals; ..."    -> [[1, 6], [8, 10]] [[1, 5]] []
017: python3 -m unittest -v <both orders>                       -> OK / OK
018: python3 -m unittest -v test_calc (untouched seed)          -> FAILED (failures=1)
     then host-side git bisect/log + git revert --no-edit       -> OK, 5 commits, newest "Revert ..."
019: python3 -m unittest -v test_greet                          -> OK (2 tests)
020: printf ... > sample.txt && python3 wordfreq.py sample.txt --top 2 -> "the 3" / "cat 2"
```

```
$ make unit-tests            -> 2630 passed, 1 skipped
$ make integration-tests     -> 115 passed in 395.32s
$ make format-check && make lint-check && uv lock --check  -> clean
```

**Notes**
- 015's bound is measured **base → working tree** (`git diff --numstat <root-commit>` + an untracked
  scan that skips `tests/`, `.verifier/`, `.git/`, `__pycache__/`). In the real pristine clone the
  tree is clean, so this is exactly the groomed `git diff --numstat <base>..HEAD`; the two-dot form
  alone would be vacuous wherever the answer is uncommitted (the unit suites) and would silently
  pass a bloated diff. Commits in between — including the Hand-back capture commit — are irrelevant
  to a base→tree diff, which is how the capture commit is tolerated. The gold is 3 added + 4 deleted
  = 7 of 8; the minimal `os.getenv` variant is the same 7, so the bound has one line of slack. The
  diff check runs FIRST, before anything imports `service.py` and drops a `__pycache__` in the tree.
- 018 declares **no** `[verifier.tests]` (as groomed): its reward is the git shape AND the hidden
  suite, so node ids alone would misstate the README's "reward 1 iff every declared test passes".
  Its history check is layered: subjects + ancestry + the seeded tip still carrying the BUG (catches
  a fix folded into history) and, when the seeded `original-head` tag still resolves, tag ancestry
  (catches an object-identity rewrite). The tag branch is conditional so a clone that fetched no
  tags still grades; `test_018_rejects_a_rewritten_history` is the probe that proves it fires.
- The v1 015 "alternative-correct" fixture rewrote the whole module; under the new diff bound that is
  no longer a correct answer, so it was re-expressed as the same `os.getenv` idea kept minimal. Noted
  in the ported-case mapping.
- 017's F2P declares BOTH node ids: on the seed each one fails whenever it runs second, and the
  suite is graded in every order, so neither passes on base.
- Instruction word counts: 144–189 (ceiling 250). Every task's `max_steps`/timeouts sit at or under
  the hard-tier ceiling (40 / 1500 / 180).
- 015 is the only task whose Verifier counts FILES, and its instruction asks for no scratch file —
  checked against every converted instruction, so no task tells the agent to create something its own
  Verifier then reads as a violation (020's `sample.txt` is fine: 020 has no file-set check).
- The three 015 probes isolate one check each: bloated rewrite → the line bound, `notes.md` → the
  file set, literal-in-a-comment → the all-text literal scan. The v1 "relocated to a .txt" port is
  kept, but under v2 minimality rejects it first, so it no longer isolates the scan — hence the third
  probe (it asserts on the FAIL line, not just the reward).
- NOT RUN — `decode run` over these tasks (task 160's scope, explicitly out of scope here).

### [Tester] 2026-09-11 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit`)
- Unit tests: 2630 passed / 1 skipped (`make pre-commit`'s pytest run; benchmark slice re-run alone: 212 passed)
- Integration tests: not re-run (SWE reported 115 passed in 395s against real docker; no code under `src/` changed by this task, only `evals/` fixtures + `tests/unit/`, so this was not re-verified — flag if PR Reviewer wants it repeated)
- Warnings: 0
- `grep -rn "judges" evals/ tests/unit/evals` — only comments/docs referencing the deletion (ADR-0022 §6); no live `judges:` config anywhere.
- Sandbox image `which`: `docker run --rm ghcr.io/astral-sh/uv:python3.12-bookworm-slim` → `make` NOT FOUND, `python3` OK, `git` NOT FOUND, `gh` NOT FOUND, `sqlite3` NOT FOUND, `pytest` NOT FOUND, `bash` OK — matches the SWE's recorded evidence and the docker backend's own apt-installed git.

**E2E adversarial pass**
- Happy path: ran all six self-check commands from each `instruction.md` against `solution/` inside the real sandbox image (015, 016, 019, 020) and locally (017, 018, which need git) → all matched the instruction's promised output exactly (PASS).
- Break path 1 (015, `[verifier.tests]`/diff-bound boundary): correct-but-reformatted `service.py` (blank lines doubled, reworded docstrings, no logic change) → reward 0 (`FAIL: ... 26 added+deleted lines, more than the 8...`) (PASS). Correct fix with the literal moved into a comment → reward 0, caught by both the diff bound (9 lines) and the literal scan (PASS). Minimal correct fix plus a trailing `decode@localhost` capture commit on top → reward 1 (PASS — capture commit correctly tolerated).
- Break path 2 (018, git-shape hostile inputs): `git reset --hard` onto an orphan branch carrying the same final content and a `Revert`-prefixed subject → reward 0 (seeded commit gone, history rewritten) (PASS). A textbook revert with `git commit --amend` to a non-"Revert" subject → reward 0 (PASS). A `git reset --soft` squash of all four seed commits down to one, re-committed as `Revert: squashed history...` → reward 0 (seeded commits gone) (PASS).
- Break path 3 (017, state edges: hidden-suite overlay): agent deletes its own `test_registry.py` and leaves `registry.py` buggy ("fixes" the flake by removing the test) → reward 0, hidden suite still catches the leak (PASS). Agent deletes its own test AND applies the real `bucket=None` fix → reward 1 (PASS, confirms the overlay doesn't over-punish a legitimate fix). Agent replaces its own test with a no-op stub (asserts nothing) while leaving `registry.py` buggy → reward 0, hidden suite still catches it (PASS).
- Break path 4 (019, malformed/hostile input — conflict-marker scan): a leftover `<<<<<<<`/`>>>>>>>` unresolved conflict → reward 0 (already covered by `test_verifier_edge_cases_hard.py::test_019_rejects_leftover_conflict_markers`). **New probe**: a correct, fully-resolved `greet.py` with a stray, harmless `=======` token left inside a docstring/comment (not part of any real conflict) → **reward 1.0** — **FAIL**. `tests/test.sh`'s marker scan only tests for `"<<<<<<<"` and `">>>>>>>"`; it never checks `"======="`, even though `instruction.md` explicitly promises "Leave no conflict marker (`<<<<<<<`, `=======`, `>>>>>>>`) behind in any `.py` file". This is reachable in practice: a model doing partial manual conflict cleanup could delete the two outer marker lines and forget the middle separator. The bare `=======` must live inside a string/comment to be syntactically valid Python — a `SyntaxError` from a stray token in real code would already fail the greeting suite, so this exact fixture is the only way the gap is reachable, and it is reachable.

**Acceptance criteria**
- [x] PASS — Six folders `015`–`020` on the v2 layout; `judges:`/`task.yaml` gone — `test_no_task_yaml_or_judge_survives_in_the_suite` passes; confirmed via `grep -rn judges evals/` (docs-only hits).
- [x] PASS — Instructions rewritten against the checklist — read all six `instruction.md`; each names exact deliverable paths/functions, states a self-check command that exists in the sandbox image (bash/python3/git only), word counts 130–175 (ceiling 250), and none contain `tests/`, `solution/`, `reward` or `verif` (confirmed by grep and by `test_no_instruction_leaks_the_graders_vocabulary`).
- [ ] FAIL — `make ci` green + `test_verifier_edge_cases_hard.py` ports every judge-replacement check. `make ci` legs are green (confirmed: format-check, lint-check, pre-commit/unit 2630+1, benchmark slice 212). But the "019 markers left → 0" promise is only partially delivered: see Break path 4 above — a partial/stray marker earns 1.0, contradicting `instruction.md`'s own explicit wording.
      Expected: any of `<<<<<<<`, `=======`, `>>>>>>>` left in a `.py` file → reward 0, per the instruction's own parenthetical.
      Actual: a stray `=======` (no other markers) → reward 1.0 (verified live, see fixture in Log below).
      Fix: add `"=======" in text` to the scan in `evals/benchmark/tasks/019-patch-conflict-resolve/tests/test.sh` check (a), OR drop `=======` from the instruction's parenthetical if the scan is meant to stay two-marker; either way re-run `test_oracle_sanity.py -k 019` to confirm the gold direction still earns 1.0, since the scan walks every `.py` under the checkout including `tests/test_greet.py` itself.
- [x] PASS — `tests/unit/evals/benchmark/test_suite_shape.py`: 19 tasks / 7-6-6 / ceilings / taxonomy / ≤250 words / forbidden words / F2P declared for 007,009,014,016,017,019,020 — all pass; live-mutated `015`'s `max_steps` to 999 (killed by `test_every_budget_is_at_or_under_its_tier_ceiling`) and appended "tests/" to its instruction (killed by `test_no_instruction_leaks_the_graders_vocabulary`), then reverted both cleanly (`git status --porcelain` on those two files returned to their original untracked state).
- [x] PASS — Sandbox-image check recorded — `which` results above, matching the SWE's report; git absence explained by the docker backend's own apt-install step.
- [x] PASS — Scope: `git status --porcelain` touches only `evals/benchmark/tasks/015..020`, `tests/unit/evals/benchmark/`, `evals/benchmark/tasks/README.md`, `tests/unit/evals/benchmark/conftest.py`, and this task file — confirmed by filtering the full `git status --porcelain` against that allow-list (empty residual).

**Evidence**
```
$ docker run --rm ghcr.io/astral-sh/uv:python3.12-bookworm-slim bash -lc 'for c in make python3 git gh sqlite3 pytest bash; do ...; done'
make NOT FOUND / python3 OK / git NOT FOUND / gh NOT FOUND / sqlite3 NOT FOUND / pytest NOT FOUND / bash OK

$ (fixture: greet.py with body unchanged/correct, docstring holding "Left over from the merge: =======")
grade_workspace("019-patch-conflict-resolve", files={"greet.py": _GREET_STRAY_SEPARATOR})
-> reward = 1.0   # expected 0.0 per instruction.md's own wording

$ make unit-tests            -> 2630 passed, 1 skipped
$ uv run pytest tests/unit/evals/benchmark -q   -> 212 passed
$ make format-check && make lint-check          -> clean
```

**Other issues found**
- 020's hidden `tests/test_wordfreq.py::_run` asserts `proc.returncode == 0`, but `instruction.md` only ever specifies stdout content/format — it never promises the CLI must exit 0. A correct implementation that prints the exact right lines but exits nonzero (e.g. an off-by-one `sys.exit(1)` left in by habit) would fail with a bare `AssertionError: the CLI exited 1: ...` rather than a graded test failure. Not blocking (it only over-penalizes an odd implementation choice, and the documented "different order/format → 0" contract is correctly enforced by exact-list equality), but worth a follow-up so instruction/verifier promises line up the same way 019's should.
- Not independently re-run: `make integration-tests` (115 passed / 395s per SWE's report) — no `src/` code changed by this task, so low risk, but note it for the record.

**VERDICT: FAIL**

One blocking issue: 019's conflict-marker scan doesn't fully deliver the "no conflict marker (`<<<<<<<`, `=======`, `>>>>>>>`) left behind" promise `instruction.md` makes — a lone `=======` earns reward 1.0 instead of 0.0. SWE to fix `evals/benchmark/tasks/019-patch-conflict-resolve/tests/test.sh` (or the instruction wording) and add a permanent regression case to `tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py` using the fixture above, then re-run `test_oracle_sanity.py -k 019` to confirm the gold direction is unaffected.

### [SWE] 2026-09-11 09:40 — QA fixes

**Files modified**
- `evals/benchmark/tasks/019-patch-conflict-resolve/tests/test.sh` — check (a) now scans for ALL
  THREE markers (`<<<<<<<`, `=======`, `>>>>>>>`) and skips the overlaid top-level `tests/` dir.
- `evals/benchmark/tasks/020-build-small-tool/instruction.md` — the self-check line now states the
  exit-status promise the hidden suite already enforces.
- `evals/benchmark/tasks/README.md` — 019's audit row names the three markers.
- `tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py` — NEW case
  `test_019_rejects_a_stray_conflict_separator` + the Tester's `_GREET_STRAY_SEPARATOR` fixture;
  module docstring gained the "second marker probe, added after QA" paragraph.

**Fix 1 (BLOCKER) — 019's marker scan was two-thirds of its own promise**
Red first: with the Tester's fixture (a correct `greet.py` carrying `Left over from the merge:
=======` inside its docstring) the new case failed on the ASSERTION, not an error —
`AssertionError: expected reward 0.0, got 1.0`, verifier stdout empty, hidden suite `OK`. Then the
scan gained the third marker and went green.

Decision, documented in `test.sh`: the markers are matched as a SUBSTRING anywhere in the file,
exactly as the two outer ones already were — NOT line-anchored. A line-anchored match (`== marker`
or `startswith(marker)`) would wave through the very fixture QA found, since the leftover sits
mid-line after `Left over from the merge: `, and it would not even buy the false-positive
protection it promises (a 7-`=` setext underline IS the marker; a 20-`=` one starts with it). The
residual cost — a long `=` run in a docstring reading as a marker — is what `instruction.md`'s
unqualified "Leave no conflict marker … behind in any `.py` file" already says.

Scope of the scan: every `.py` under the checkout EXCEPT the overlaid top-level `tests/` dir, tested
as `path.parts[0] == "tests"` (not `"tests" in path.parts`) so the SEEDED root `test_greet.py` and
any nested `foo/tests/bar.py` the agent invents stay scanned; only the grader's own overlay is
exempt, so it can never self-trip. `.git` stays skipped. Verified no seeded/hidden/gold `.py` in the
019 tree carries a `=======` (`grep -rn "=======" evals/benchmark/tasks/019-patch-conflict-resolve/`
→ the instruction's own sentence only), so the gold direction is untouched.

**Fix 2 (non-blocking) — 020's instruction now promises what its Verifier measures**
`tests/test_wordfreq.py::_run` asserts `returncode == 0`; the instruction only ever specified stdout.
Added six words to the existing self-check line: "…, and exit with status 0 on success." Instruction
is 189 words (ceiling 250) and still free of `tests/`, `solution/`, `reward`, `verif` — note
`test_no_instruction_leaks_the_graders_vocabulary` matches `verif` as a substring, so the wording
deliberately avoids "verify" and keeps "Check your work".

**Tests**
- Unit: 2631 passed, 1 skipped (`make pre-commit`'s pytest leg) — one more than the 2630 of the
  first hand-off, the new 019 case. Benchmark slice alone: 213 passed (was 212).
- `uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -k 019` — 2 passed (gold → 1.0,
  untouched seed → 0.0), so the new marker is not tripping the Oracle direction.
- `tests/unit/evals/benchmark/test_suite_shape.py` — 88 passed after the 020 instruction edit.
- Integration: 115 passed in 361.53 s (`make integration-tests`, real docker) — re-run this round
  even though nothing under `src/` changed, closing the Tester's "not independently re-run" note.
- `uv lock --check`, `make format-check`, `make lint-check`, `make pre-commit` clean — every leg of
  `make ci`.

**Acceptance criteria**
- [x] `make ci` green + `test_verifier_edge_cases_hard.py` carries the judge-replacement checks —
      the "019 markers left → 0" promise is now whole:
      `test_019_rejects_leftover_conflict_markers` (full conflict) +
      `test_019_rejects_a_stray_conflict_separator` (lone separator, the QA find).
- [x] Every other criterion unchanged from the first hand-off.

**Evidence**

```
$ uv run pytest tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py -k stray -q   # BEFORE the fix
E       AssertionError: expected reward 0.0, got 1.0 (timed_out=False)
1 failed, 24 deselected

$ # AFTER the fix — the Verifier run by hand on the Tester's fixture
FAIL: conflict markers ======= remain in greet.py
reward: 0

$ uv run pytest tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py -k 019 -q
5 passed, 20 deselected
$ uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -k 019 -q
2 passed, 38 deselected
$ uv run pytest tests/unit/evals/benchmark -q
213 passed
$ make pre-commit
All checks passed! ... 2631 passed, 1 skipped in 46.04s
$ make integration-tests
115 passed in 361.53s (0:06:01)
$ uv lock --check && make format-check && make lint-check   -> clean
```

```
$ # e2e in the REAL sandbox image, both touched instructions' self-check commands, on the gold
019: python3 -m unittest -v test_greet          -> Ran 2 tests ... OK   (exit 0)
020: printf 'The cat, the dog. The CAT!\n' > sample.txt && python3 wordfreq.py sample.txt --top 2
     -> "the 3" / "cat 2", exit=0                (the status the instruction now promises)
```

**Notes**
- Nothing under `src/` changed; the diff stays inside `evals/benchmark/tasks/019..020`,
  `evals/benchmark/tasks/README.md`, `tests/unit/evals/benchmark/` and this task file.
- Not committed, per the lifecycle — handing back to the Tester for re-review.

**Addendum — the `tests/` exemption proved by hand**
The unit suite cannot reach it (`grade_workspace` overlays `task.tests_dir` LAST, wiping anything
planted there), and no grader file carries a marker today, so the exemption was verified directly —
seed, gold `greet.py`, overlay, then plant markers and re-run `bash tests/test.sh`:

```
$ # markers planted INSIDE the overlay (tests/scratch.py + a comment appended to tests/test_greet.py)
stdout: (empty)
reward: 1            # the grader's own files never self-trip

$ # "=======" appended to the SEEDED root test_greet.py + "<<<<<<<" in a nested pkg/tests/helper.py
stdout: FAIL: conflict markers <<<<<<< remain in pkg/tests/helper.py
reward: 0            # only the TOP-LEVEL tests/ is exempt (path.parts[0]), nothing else
```

**Note on the other direction of the instruction/Verifier audit**
019's instruction also says "change nothing else in the directory", which its Verifier does not
measure. Left as is, deliberately: an unenforced promise is the BENIGN asymmetry — it can produce
neither a false 1.0 nor a false 0.0, unlike the two this round fixed (the `=======` gap made the
Verifier too lenient, 020's `returncode == 0` assert made it stricter than its instruction). 015 is
the task where minimality IS the graded requirement, and there it is measured.

### [Tester] 2026-09-11 11:15 — QA (round 2)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit`)
- Unit tests: 2631 passed / 1 skipped (`make pre-commit`); benchmark slice alone: 213 passed
  (`uv run pytest tests/unit/evals/benchmark -q`)
- Integration tests: not independently re-run this round (SWE re-ran 115 passed / 361.53s against
  real docker; no file under `src/` is touched by this task — confirmed via `git status --porcelain`
  scope check below — so risk of a docker-only regression from this diff is effectively zero)
- Warnings: 0

**E2E adversarial pass**
- Happy path: rebuilt the round-1 fixture (`greet.py` with a correct `greet()` body and a stray
  `Left over from the merge: =======` inside its docstring) from scratch in a scratch dir, copied the
  real `tests/test.sh` + `tests/test_greet.py`, and ran `bash tests/test.sh` directly (not through the
  pytest harness) → `FAIL: conflict markers ======= remain in greet.py`, `reward: 0` (PASS — the
  round-1 blocker is fixed, reproduced independently of the SWE's own regression test).
- Break path 1 (boundary: exemption scope — grader files must not self-trip): planted `>>>>>>>` in a
  file added to the top-level `tests/` overlay dir → reward stayed `1` (PASS, matches the documented
  "only the grader's own tests/ is exempt" design).
- Break path 2 (boundary: nested directory named `tests` must NOT be exempt): planted `<<<<<<<` in a
  new `pkg/tests/x.py` alongside an otherwise-correct `greet.py` → `FAIL: conflict markers <<<<<<<
  remain in pkg/tests/x.py`, reward `0` (PASS — confirms the fix uses `path.parts[0] == "tests"`, not
  a substring/`"tests" in path.parts` check, so it doesn't blanket-exempt every directory literally
  named `tests` anywhere in the tree).
- Break path 3 (oracle sanity, both directions): `uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -k "019 or 020"` → gold earns 1.0, untouched seed earns 0.0 for both tasks (PASS — the
  marker-scan and exit-code fixes didn't regress the gold direction).
- Break path 4 (020 exit-code promise): re-read `instruction.md` — self-check line now says
  "…, and exit with status 0 on success." (word count 189, ceiling 250, no `tests/`/`solution/`/
  `reward`/`verif` substrings) — matches what `tests/test_wordfreq.py::_run` already asserted
  (`returncode == 0`) (PASS). Proven transitively rather than by a fresh run: `_run` asserts
  `returncode == 0` on every invocation it makes, and
  `test_the_oracle_earns_reward_one[020-build-small-tool]` (in the `test_oracle_sanity.py -q` run
  above) passes, so the gold solution already exits 0 under this exact assertion.

**Acceptance criteria**
- [x] PASS — Six folders `015`–`020` on the v2 layout; `judges:`/`task.yaml` gone —
      `test_no_task_yaml_or_judge_survives_in_the_suite` passes (part of the 88-passed
      `test_suite_shape.py` run).
- [x] PASS — Instructions rewritten against the checklist — 019's instruction still names the two
      outer markers explicitly AND `=======` in its "leave no conflict marker" parenthetical, matched
      against a scan that now actually enforces all three; 020's self-check now states the exit-0
      promise its hidden suite measures.
- [x] PASS — `make ci` green + `test_verifier_edge_cases_hard.py` ports the judge-replacement checks,
      including the round-1 gap: `test_019_rejects_a_stray_conflict_separator` (independently
      reproduced above, outside the pytest fixture, with a hand-built scratch workspace) plus the
      pre-existing `test_019_rejects_leftover_conflict_markers`. `uv run pytest
      tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py -k 019 -q` → 5 passed.
      `uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -q` → 40 passed (19 tasks × 2
      directions + canary/extras).
- [x] PASS — `test_suite_shape.py`: 88 passed (19 tasks / 7-6-6 / ceilings / taxonomy / ≤250 words /
      forbidden words / F2P for 007, 009, 014, 016, 017, 019, 020) — unchanged from round 1, re-run
      clean after the 020 instruction edit.
- [x] PASS — Sandbox-image check recorded (round-1 evidence, unchanged this round — no environment
      dependency was touched by the fix).
- [x] PASS — Scope: `git status --porcelain` filtered against the allow-list (`evals/benchmark/tasks/
      015..020`, `evals/benchmark/tasks/README.md`, `tests/unit/evals/benchmark/`, this task file) →
      empty residual; `git diff --stat` on tracked files shows only `README.md`,
      `tests/unit/evals/benchmark/conftest.py` and this task file changed outside the untracked new
      folders.

**Decision on the unenforced "change nothing else in the directory" promise (019)**
Judged BENIGN, not a blocker. The graded suite is the hidden `tests/test_greet.py`, overlaid onto
`tests/` *after* the agent runs (per the module's own docstring), so anything the agent does to the
seeded `test_greet.py` or `feature.patch` is moot for reward — the reward is fully determined by (a)
no conflict marker outside the top-level `tests/` overlay and (b) the hidden greeting suite passing.
There is no path by which touching an extra file flips reward from a correct-behavior 0 to an
incorrect-behavior 1, or vice versa, so the promise is unenforceable without becoming false-negative
risk-prone (a `[verifier.tests]`-style extra-file diff check would need the same base-commit machinery
015 already carries, and 019 is not the task where minimality is the graded requirement — 015 is, and
that IS measured). Confirmed empirically (hand-built scratch workspace, gold `greet.py` + an unrelated
marker-free `extra.py` + a stray `notes.md` + `feature.patch` removed) → reward stays `1.0`, consistent
with the scan's actual (marker + hidden-suite) contract rather than the instruction's stricter English
— note the claim is narrower than "any extra file is free": an unrelated file that itself carried a
7+ `=` run or a literal `<<<<<<<`/`>>>>>>>` would still be caught, since it enters the same `rglob`.
No fix required this round.

**Evidence**
```
$ # independent reproduction, not the pytest fixture — hand-built scratch workspace
$ VERIFIER_DIR=.verifier bash tests/test.sh   # greet.py correct, docstring carries a stray =======
FAIL: conflict markers ======= remain in greet.py
... OK (hidden suite still passes on its own)
$ cat .verifier/reward.txt
0

$ # nested pkg/tests/x.py must NOT be exempt
FAIL: conflict markers <<<<<<< remain in pkg/tests/x.py
$ cat .verifier/reward.txt
0

$ # grader's own top-level tests/ overlay IS exempt
$ cat .verifier/reward.txt
1

$ uv run pytest tests/unit/evals/benchmark/test_verifier_edge_cases_hard.py -k 019 -q
5 passed, 20 deselected
$ uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -q
40 passed
$ uv run pytest tests/unit/evals/benchmark -q
213 passed
$ make pre-commit
2631 passed, 1 skipped in 45.31s
$ make format-check && make lint-check
clean
$ git status --porcelain | awk '{print $2}' | grep -v -E '^(evals/benchmark/tasks/(015|016|017|018|019|020)|evals/benchmark/tasks/README.md|tests/unit/evals/benchmark/|tasks/159-benchmark-tasks-hard-v2-and-audit.md)'
(empty)
```

**Other issues found**
- PASS with note — the fix widens the marker scan's false-negative surface slightly: `=======` is
  now matched as a bare substring in every `.py` file outside the top-level `tests/` overlay, so a
  7+ `=` docstring/setext underline the agent itself writes into any file would read as a leftover
  conflict marker and score 0, even with no real conflict. Not blocking: the instruction's "leave no
  conflict marker" wording is unqualified, the two outer markers (`<<<<<<<`/`>>>>>>>`) were already
  read the same substring way pre-fix, and the workspace is three small files, so the odds of an
  honest solution tripping it are low. Worth a one-line instruction caveat if this pattern recurs
  in a future task, but not worth blocking this round on.
- Round-1's non-blocking 020 exit-code note is now resolved (see AC above).
- Integration suite not independently re-run this round (see Test summary) — flagged for the record,
  not blocking given zero `src/` diff.

**VERDICT: PASS**

### [PA] 2026-09-11 21:30 — Acceptance Review

**VERDICT: ACCEPT** — feature-level review of evals-v2 (tasks 155–167, PR #68); evidence and the
per-AC-group walk-through are in `tasks/done/167-regression-step-budgets-recalibration.md`'s log.
Hand off to the PR Reviewer.
