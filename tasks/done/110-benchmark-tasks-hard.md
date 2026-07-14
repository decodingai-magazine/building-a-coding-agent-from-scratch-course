---
id: 110
feature: evals
status: done
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

- [x] Six folders pass loader + both oracle-sanity directions in `make ci`.
- [x] Tasks 15, 19, 20 declare G-Eval judges that the runner attaches (unit-verified via loader).
- [x] 017's oracle demonstrably fails on the seeded flake (3-run loop catches it in sanity FAIL
      direction).
- [x] Spot-run one task through the docker runner; result logged.
- [x] `make ci` green.

## Out of scope

- Any runner/judge code change (104/106 own those).

## Log

### [SWE] 2026-07-14 15:20 — Implementation

**Files modified**
- `evals/benchmark/tasks/015-secret-scrub/` — hardcoded API key + DB password moved to env reads;
  `task.yaml` (+ minimal_diff G-Eval judge), `setup/service.py`, `solution/service.py`,
  `verify/verify.sh` (no-literal scan across all `*.py` + env-backed accessor check).
- `evals/benchmark/tasks/016-implement-from-spec/` — `merge_intervals` stub + rich docstring;
  hidden pytest `verify/_verify_intervals.py` run import-and-call (no pytest binary), `verify.sh`,
  `setup/`, `solution/`.
- `evals/benchmark/tasks/017-flaky-test-hunt/` — order-dependent flake via a shared mutable default
  (`bucket=[]`); `verify.sh` runs the HIDDEN suite (`_verify_suite.py`) in sorted order in 3
  consecutive fresh processes + a source-level anti-cheat (independent calls must not accumulate).
- `evals/benchmark/tasks/018-git-bisect-revert/` — `setup.sh` builds a 4-commit history with one
  breaking commit (multiply adds) + `original-head` tag; `solution/make_gold.sh` reverts;
  `verify.sh` asserts tests pass + a `Revert` commit exists + `original-head` still an ancestor +
  all four original subjects present (no rewrite).
- `evals/benchmark/tasks/019-patch-conflict-resolve/` — `setup/feature.patch` conflicts with a tree
  that already capitalizes the name; `verify.sh` requires BOTH intents (capitalization + patch
  wording/punctuation) and no conflict markers; `task.yaml` (+ resolution_quality G-Eval judge).
- `evals/benchmark/tasks/020-build-small-tool/` — word-frequency CLI (top-N, case-insensitive,
  punctuation-stripped, count-desc/word-asc); hidden `verify/_verify_wordfreq.py` drives the CLI via
  subprocess; `task.yaml` (+ code_quality G-Eval judge).
- `tests/unit/evals/benchmark/test_hard_task_judges.py` — loader parses the three judge specs, runner
  attaches the judge on a single-task run and drops per-task judges on a multi-task run.
- `tests/unit/evals/benchmark/test_oracle_edge_cases_hard.py` — attack-your-own-oracles: ≥1
  alternative-correct (PASS) + ≥1 plausible-wrong (FAIL) per task (13 probes).

**Tests**
- Unit: 1683 passing, 0 failing (`make pre-commit` = format-check + lint-check + unit-tests). The
  new hard tasks add 40 oracle-sanity params (20 tasks × 2 directions), 13 edge-case probes, 10
  judge-attachment assertions.
- Integration: 113 passing, 2 key-gated skips, 0 failing (`make integration-tests`, 312s) — includes
  `test_benchmark_docker.py`.
- `uv lock --check`: clean (no deps added).

**Acceptance criteria**
- [x] Six folders pass loader + both oracle-sanity directions — `test_oracle_sanity.py` (both
      directions) + `test_task_loader` scan, all green.
- [x] Tasks 15/19/20 declare G-Eval judges the runner attaches — verified by
      `test_hard_task_judges.py::test_runner_attaches_the_judge_on_a_single_task_run`.
- [x] 017's oracle demonstrably fails on the seeded flake via the 3-run loop —
      `test_oracle_sanity::...[017-flaky-test-hunt]` FAIL direction + determinism documented in
      `verify.sh` (sorted order → test_collect_apple seeds the shared default, test_collect_banana
      sees the leak and fails every run).
- [x] Spot-run one task through the docker runner — 016 through the REAL docker benchmark task_fn
      with a scripted `bash_then_finish` model (no API key): `agent_error=None`, `infra_error=None`,
      first tool `bash`, `verify exit_code=0`, stdout `PASS`.
- [x] `make ci` green — `uv lock --check` + format-check + lint-check + unit (1683) + integration
      (113 pass / 2 key-skips) all pass.

**Attack-your-own-oracles probes (lesson 1)** — all pass:
- 015: `os.getenv` variant PASSES; one secret still hardcoded FAILS (literal scan).
- 016: different correct impl (key= sort) PASSES; strict `<` (touching not merged) FAILS.
- 017: `bucket is not None` guard PASSES; returns-a-copy-but-still-accumulates FAILS (3-run loop).
- 018: revert by position (`HEAD~1`) PASSES; direct in-place fix with no revert commit FAILS.
- 019: manual capitalization PASSES; dropped tree intent FAILS; leftover conflict markers FAIL.
- 020: hand-rolled dict counter PASSES; case-sensitive counting FAILS.

**Evidence**
```
$ make pre-commit
... 1683 passed in 97.53s ...
$ make integration-tests
... 113 passed, 2 skipped in 312.48s ...
$ # docker scripted-model spot-run of 016 (real DockerBackend, scripted bash model, no key)
agent_error : None
infra_error : None
tool_calls  : ['bash']
steps       : 2
verify      : {'exit_code': 0, 'stdout': 'PASS\n'}
```

**Notes**
- Contract discipline (self-checked): every `task.yaml` prompt is clean of verify/oracle/solution/
  grade (the 019 grep hit is the judge name `resolution_quality`, i.e. substring "reSOLUTION", in
  grade-time metadata — not the prompt). `git check-ignore` on every committed asset → none ignored.
  No nested committed `.git` and no committed `*.db`: 018's git history is built by `setup.sh` (git
  actions are the solved state, so 018 uses the `make_gold.sh`-in-`solution/` device, mirroring 010).
- verify.sh tool restriction honored (bash/python3/git/sqlite3): hidden pytest for 016/017/020 runs
  by import-and-call, never the `pytest` binary (slim image ships none); "pytest"/"sed"/"make" appear
  only inside `#` comment prose, never as invocations.
- 017 determinism mechanism (lesson 3): sorted test order + fresh process per run makes the
  shared-mutable-default leak deterministic — the seeded suite fails run 1 of 3 every time, never by
  luck; documented in `verify.sh`.
- 018 no-rewrite proof (lesson 4): `original-head` tag must remain an ancestor of HEAD and all four
  original subjects present; a `Revert`-subject commit must exist; tests must pass.
- DID NOT COMMIT — handing off to the Tester first.

### [Tester] 2026-07-14 04:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` both exit 0)
- Unit tests: 1683 passed / 0 failed (`make unit-tests`, 97.9s)
- Integration tests: 113 passed / 2 key-gated skips / 0 failed — verified via full `make ci`
  (1796 passed, 2 skipped, 416.84s / 6:56, exit 0: lockfile check + format-check + lint-check +
  unit + integration all green, docker daemon reachable so `test_benchmark_docker.py` ran for real)
- Warnings: 0 (`filterwarnings=["error"]`; a live run would have failed loudly otherwise)

**E2E adversarial pass**
- Happy path: `bash verify.sh` over each task's `solution/` overlay (host-side oracle-sanity,
  `evals.harness.oracle_sanity.run_oracle`) → all six print `PASS`, exit 0 (PASS)
- Break path 1 (015 — hostile: hardcoded secret literal moved to a NON-`.py` fallback file, read
  through `os.environ.get(...) or <read file>`): `service.py` reads `API_KEY`/`DB_PASSWORD` from env
  first but falls back to two files (`secret_api_key.txt`, `secret_db_password.txt`) that still
  contain the exact two original hardcoded secrets → oracle scan only globs `**/*.py`, never sees
  the `.txt` files → **verify.sh printed `PASS`, exit 0.** Expected FAIL (a hardcoded secret still
  lives in the Workspace, just outside a `.py` file). **FAIL**
- Break path 2 (015 — hostile: `os.environ.get("API_KEY", "sk-fallback-hardcoded-should-not-exist")`
  / same for `DB_PASSWORD` — a BRAND-NEW hardcoded secret literal as the fallback default, not the
  two original strings the scan hardcodes): env vars are set by the oracle so the accessor-return
  check also passes → **verify.sh printed `PASS`, exit 0.** Expected FAIL (the prompt says
  "Hardcoded secrets must never live in code"; a new literal secret baked into a default arg is
  exactly that). **FAIL**
- Break path 3 (017 — determinism, ran the seeded-bug `setup/registry.py` unmodified through
  `verify.sh` 5 times in 5 fresh temp Workspaces): every one of the 5 runs printed
  `FAIL: the test suite was not green on run 1 of 3 ...`, exit 1 — the claimed determinism holds,
  never flaked, never accidentally PASSed (PASS)
- Break path 4 (017 — anti-cheat, `bucket.clear()` symptom-mask instead of a root-cause fix):
  `def collect(item, bucket=[]): bucket.clear(); bucket.append(item); return bucket` → anti-cheat
  correctly caught it: `FAIL: collect() still leaks state across calls: ['y'] then ['y']`, exit 1
  (PASS — oracle correctly rejects this cheat)
- Break path 5 (018 — wrong commit reverted: `git revert --no-edit HEAD~2`, the harmless "docs"
  commit, not the breaking "refactor: simplify multiply" one): `test_multiply` still fails →
  `FAIL: test_multiply did not pass (was the breaking commit reverted?)`, exit 1 (PASS — oracle
  correctly rejects reverting the wrong commit)
- Break path 6 (020 — off-by-one top-N, `ordered[: args.top + 1]`) and reverse-alphabetical tie
  break: both correctly FAIL against `test_basic_counts_and_top_n` (PASS)
- Alternative-correct sanity (my own probes, distinct from the SWE's): 016 `functools.reduce`
  implementation PASSES; 017 tuple-immutable-default (`bucket=()` → `list(bucket)`) PASSES; 020
  hand-rolled `sys.argv` parsing (no `argparse`) PASSES; 019 patch-never-applied-at-all and a
  partial (`<<<<<<<` only, no `>>>>>>>`) leftover marker both correctly FAIL — all as expected

**Root cause (015)**: `evals/benchmark/tasks/015-secret-scrub/verify/verify.sh` only (a) globs
`**/*.py` for the two exact original secret string literals, and (b) checks the accessors return
the *env* value when the env vars are set. Neither check requires that the accessors have NO other
hardcoded fallback, and neither check looks outside `*.py` files. A solution that keeps a hardcoded
secret — either the ORIGINAL one stashed in a non-`.py` file, or a BRAND-NEW fabricated one as an
`os.environ.get(..., "...")` default — satisfies both checks and is graded PASS, even though it
directly violates the task's own stated requirement ("Hardcoded secrets must never live in code").
This is exactly the attack class task 110's own scope calls "prompt: move to env reads" is meant to
verify, and the QA brief for this task named this exact attack shape ("read env with hardcoded
fallback secret") — not a contrived edge case.

**Acceptance criteria**
- [x] PASS — Six folders pass loader + both oracle-sanity directions in `make ci` —
      `uv run pytest tests/unit -q -k "015-secret-scrub or 016-implement-from-spec or
      017-flaky-test-hunt or 018-git-bisect-revert or 019-patch-conflict-resolve or
      020-build-small-tool"` → 21 passed; full `make ci` → 1796 passed, 0 failed.
- [x] PASS — Tasks 15, 19, 20 declare G-Eval judges the runner attaches (unit-verified via loader)
      — `uv run pytest tests/unit/evals/benchmark/test_hard_task_judges.py -v` → 10/10 passed.
- [x] PASS — 017's oracle demonstrably fails on the seeded flake (3-run loop, FAIL direction) —
      `test_oracle_sanity.py::test_oracle_fails_on_the_untouched_setup[017-flaky-test-hunt]` passes;
      manually re-ran the untouched `setup/` 5× in 5 fresh Workspaces, FAILed every time (break
      path 3 above).
- [x] PASS — Spot-run one task through the docker runner; result logged — SWE logged 016 through
      the real `DockerBackend` + scripted `bash_then_finish` model
      (`agent_error=None`, `verify.exit_code=0`, stdout `PASS`); I independently re-ran the SAME
      real-docker path against a DIFFERENT hard task, 017-flaky-test-hunt (a scripted `bash`
      rewrite of `registry.py` to the `bucket=None` fix), through
      `make_benchmark_task_fn({task.id: task}, sandbox="docker")`:
      `agent_error=None, infra_error=None, tool_calls=['bash'], verify={'exit_code': 0, 'stdout':
      'PASS\n'}`. Confirms the injection/grading path is not special-cased to 016.
- [x] PASS — `make ci` green — ran it myself end-to-end (not just trusted the SWE's log): lockfile
      check + format-check + lint-check + full test suite (unit 1683 + integration 113) →
      1796 passed, 2 skipped (both are the pre-existing live-API-key-gated skips, unrelated to this
      task), 0 failed, 416.84s.

**Contract discipline (independently re-verified, not just SWE's self-check)**
- Prompts never name verify/oracle/solution assets: scripted a scan of all six `task.yaml` prompts
  for `verify`, `oracle`, `solution/`, `grade`, `hidden test`, `_verify` — zero hits.
- `verify.sh` tool restriction (bash/python3/git/sqlite3 only): grepped all six `verify.sh` for
  `sed|awk|pytest|make|curl|wget|pip|nc|ssh|npm|node` as invocations — every hit is inside a `#`
  comment, never an actual command.
- `git check-ignore` on every file under the six new task folders → nothing ignored. No nested
  `.git`/`*.git` directories found. No committed `*.db`/`*.sqlite*` files.
- `solution/` never enters the runner path: grepped `evals/harness/benchmark.py` +
  `evals/harness/sandbox.py` for `solution` — zero references; only `oracle_sanity.py` and
  `task_loader.py`'s `solution_dir` property touch it.
- Judges parse via the loader: `test_hard_task_judges.py` (10/10) confirms 015/019/020 attach a
  single `GEval` on a solo run and the multi-task run carries none.

**Evidence**
```
$ make format-check && make lint-check
247 files already formatted
All checks passed!

$ make unit-tests
======================= 1683 passed in 97.88s (0:01:37) ========================

$ make ci
================= 1796 passed, 2 skipped in 416.84s (0:06:56) ==================

$ # Tester's own docker spot-check on 017 (different hard task than the SWE's 016)
agent_error : None
infra_error : None
tool_calls  : ['bash']
verify      : {'exit_code': 0, 'stdout': 'PASS\n'}

$ # 015 break path 1 — literal secret stashed in a non-.py fallback file
exit: 0 stdout: PASS   # EXPECTED FAIL

$ # 015 break path 2 — new hardcoded fallback secret via os.environ.get(key, "new-literal")
exit: 0 stdout: PASS   # EXPECTED FAIL
```

**Other issues found**
- 018 (non-blocking, noted for consideration): `verify.sh` requires the revert commit's *subject*
  to literally `startswith("Revert")` — the default `git revert --no-edit` message, which is what
  any agent following "revert it" will naturally produce. I confirmed a functionally-identical
  revert (same diff, same passing tests, same intact history) with a hand-edited commit message
  ("fix: undo the multiply regression from refactor: simplify multiply") gets FAILed solely for not
  starting with the literal word "Revert". Low real-world risk (the idiomatic path passes), but the
  check is testing commit-message wording rather than the stated requirement ("recorded as a new
  commit" reverting the bug without rewriting history). Worth a looser check (e.g. diff-equivalence
  to `git revert`'s effect, or a case-insensitive "revert" substring) if this bites in practice —
  not blocking this review, distinct from the blocking 015 findings.

**VERDICT: FAIL**

Two confirmed break paths in 015-secret-scrub's oracle
(`evals/benchmark/tasks/015-secret-scrub/verify/verify.sh`): a hardcoded secret relocated to a
non-`.py` file, and a brand-new hardcoded fallback-default secret, both grade PASS when they must
FAIL — exactly the attack class this task exists to defend against. Fix: extend the scan in
`verify.sh` beyond `**/*.py` to the whole Workspace (or explicitly assert
`api_key.__defaults__`/`db_password.__defaults__` are empty and no other file contains a
recognizable-looking secret pattern / either original literal), and reject ANY hardcoded string
default in the two accessors, not just the two known originals. Add regression probes for both
shapes to `tests/unit/evals/benchmark/test_oracle_edge_cases_hard.py`. Everything else (016-020,
determinism, contract discipline, `make ci`) is solid and does not need rework.

### [SWE] 2026-07-14 17:05 — Fixes (QA round 1)

**Blocking: 015-secret-scrub oracle hardened**
- `evals/benchmark/tasks/015-secret-scrub/verify/verify.sh` — three checks now:
  - (a) literal scan over **ALL text files** in the Workspace (was `**/*.py` only), so a literal
    relocated to a `.txt`/`.env`/config file is caught. The two literals are stored base64-encoded in
    the script so scanning `verify.sh` itself (a Workspace file at grade time) never false-positives.
  - (b) env-SET accessor check (unchanged intent): with the vars set, accessors return the env values.
  - (c) NEW env-UNSET check: with `API_KEY`/`DB_PASSWORD` unset, each accessor must raise or return
    an empty value. A non-empty return proves a hardcoded fallback secret exists — this rejects a
    fabricated `os.environ.get(name, "sk-...")` fallback AND a file-read default generically, with no
    secret-shape regex-guessing.
  - Only bash + python3 used; env is set/unset inside python (no `env` coreutil).
- `tests/unit/evals/benchmark/test_oracle_edge_cases_hard.py` — two regression probes added, both now
  FAIL: `test_015_fails_when_the_secret_is_relocated_to_a_non_py_file` (original literal in a `.txt`
  fallback) and `test_015_fails_when_a_new_hardcoded_fallback_is_fabricated` (new secret-shaped
  `os.environ.get` default). Existing `os.getenv` alternative-correct probe still PASSES (unset →
  `None` → no fallback).

**Non-blocking: 018 prompt clarified**
- `evals/benchmark/tasks/018-git-bisect-revert/task.yaml` — prompt now states the fix must be made
  with `git revert`, recorded as a revert commit whose message begins with "Revert". Documents the
  oracle's requirement so `startswith("Revert")` is fair (idiomatic `git revert` already satisfies it).
  Left the mechanical check as-is rather than diff-reversal detection (keeps the bisect+revert intent).

**Verification**
- 015 oracle-sanity both directions: PASS (gold→PASS, untouched→FAIL).
- Hard edge-case probes: 15 pass (13 prior + 2 new 015 bypass probes); judge tests: 10 pass.
- `make pre-commit`: 1685 passed, 0 failed. `make format-check` / `make lint-check`: clean.

**Notes**
- DID NOT COMMIT — handing back to the Tester for re-review.

### [Tester] 2026-07-14 04:30 — QA re-review (round 2)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` both exit 0)
- Unit tests: 1685 passed / 0 failed (`make unit-tests`, 100.0s) — matches the SWE's claim
  (1683 + 2 new regression probes)
- Integration tests: verified via a full re-run of `make ci`: 1798 passed, 2 skipped (same
  pre-existing key-gated skips), 0 failed, 408.00s — includes `test_benchmark_docker.py` for real
  (docker daemon reachable)
- Warnings: 0

**E2E adversarial pass — round 1 repros re-run against the fixed oracle, plus new bypass attempts**
- Round-1 repro 1 (015 — original secret literal relocated to a `.txt` fallback file, read via
  `os.environ.get(...) or _fallback(...)`): now **FAIL**, exit 1,
  `FAIL: a hardcoded secret literal still lives in secret_api_key.txt` — the all-text-file scan
  catches it (was the missed break path last round). Fixed.
- Round-1 repro 2 (015 — brand-new hardcoded fallback literal via
  `os.environ.get("API_KEY", "sk-fallback-hardcoded-should-not-exist")`): now **FAIL**, exit 1,
  `FAIL: api_key() returned a value with its env var unset — a hardcoded fallback secret still
  exists` — the new env-UNSET behavioral check catches it. Fixed.
- New bypass 1 (015 — original secret SPLIT across two concatenated string-literal halves,
  `_A = 'sk-live-9f8a7b6c5d4e3f21'; _B = 'ABCDEF'; return _A + _B` as the fallback): the split
  defeats the literal-text scan (no single file contains the contiguous string), but the
  env-UNSET behavioral check still catches it — `FAIL: api_key() returned a value with its env var
  unset ...`, exit 1 (PASS — oracle correctly rejects)
- New bypass 2 (015 — original secret ROT13/reverse-obfuscated so the plaintext literal never
  appears verbatim anywhere, `codecs.decode('fx-yvir-...', 'rot_13')` as the fallback): again
  defeats the text scan by construction, but the env-UNSET behavioral check catches it identically
  — exit 1, `FAIL: api_key() returned a value with its env var unset ...` (PASS — oracle correctly
  rejects; confirms check (c) is behavior-based, not pattern/regex-based, so it generalizes past
  every encoding trick I could construct, not just the two originally-reported shapes)
- Alternative-correct sanity (must still PASS): `os.getenv("API_KEY")` (existing SWE probe) PASSES;
  a NEW shape I tried — import-time eager caching (`_API_KEY = os.environ["API_KEY"]` at module
  level, accessor returns the cached constant) — PASSES (module import raises cleanly when the env
  var is unset, which check (c)'s `try: import service / except Exception: sys.exit(0)` correctly
  treats as "no fallback exists", not a false FAIL)
- 018 revert-check re-verified (cheap): `test_018_passes_when_reverting_by_position` and
  `test_018_fails_when_fixed_directly_without_a_revert_commit` both still pass; the prompt now
  documents the `git revert` / "Revert"-prefixed-message requirement explicitly (confirmed clean of
  verify/oracle/solution/grade mentions — the requirement is a stated user-facing contract, not
  something the agent has to guess), which resolves my round-1 fairness note

**Regression probes (SWE's new tests)**
- `uv run pytest tests/unit/evals/benchmark/test_oracle_edge_cases_hard.py -v` → 15/15 passed,
  including the two new ones: `test_015_fails_when_the_secret_is_relocated_to_a_non_py_file` and
  `test_015_fails_when_a_new_hardcoded_fallback_is_fabricated`
- `test_oracle_sanity.py -k 015-secret-scrub` → both directions still pass (gold PASS, untouched
  FAIL)
- `test_hard_task_judges.py` → 10/10 passed (judge attachment unaffected)

**Contract discipline (re-checked, not just trusted)**
- `verify.sh` tool restriction still bash + python3 only across all six tasks (grepped for
  `sed|awk|pytest|make|curl|wget|pip|nc|ssh|npm|node` as invocations — all comment-only hits)
- `git check-ignore` on every file under the six task folders → nothing ignored; no nested `.git`
- `018` prompt scan for verify/oracle/solution/grade mentions → clean

**Acceptance criteria**
- [x] PASS — Six folders pass loader + both oracle-sanity directions in `make ci` — full `make ci`
      → 1798 passed, 0 failed; `test_oracle_sanity.py` all 42 params green.
- [x] PASS — Tasks 15, 19, 20 declare G-Eval judges the runner attaches — `test_hard_task_judges.py`
      10/10.
- [x] PASS — 017's oracle demonstrably fails on the seeded flake (3-run loop) — unchanged from
      round 1, re-confirmed via `test_oracle_sanity.py -k 017-flaky-test-hunt`.
- [x] PASS — Spot-run one task through the docker runner; result logged — SWE's 016 spot-run + my
      round-1 independent 017 spot-run through the real `DockerBackend` both still stand (no code
      touched in the sandbox/runner path this round).
- [x] PASS — `make ci` green — re-ran myself: 1798 passed, 2 skipped (pre-existing key-gated),
      0 failed, 408.00s.

**Evidence**
```
$ make unit-tests
======================= 1685 passed in 100.01s (0:01:40) =======================

$ make ci
================= 1798 passed, 2 skipped in 408.00s (0:06:47) ==================

$ uv run pytest tests/unit/evals/benchmark/test_oracle_edge_cases_hard.py -v
... 15 passed in 3.14s ...

# Round-1 repro 1 (secret relocated to non-.py fallback file)
exit=1 stdout='FAIL: a hardcoded secret literal still lives in secret_api_key.txt'

# Round-1 repro 2 (brand-new hardcoded fallback literal)
exit=1 stdout='FAIL: api_key() returned a value with its env var unset — a hardcoded fallback secret still exists'

# New bypass: secret split across concatenated string halves
exit=1 stdout='FAIL: api_key() returned a value with its env var unset — a hardcoded fallback secret still exists'

# New bypass: rot13-obfuscated fallback reconstructing the original secret at runtime
exit=1 stdout='FAIL: api_key() returned a value with its env var unset — a hardcoded fallback secret still exists'

# New alternative-correct shape: import-time os.environ[] caching into module constants
exit=0 stdout='PASS'
```

**Other issues found**
- None remaining. The round-1 "Other issues found" note on 018's `startswith("Revert")` strictness
  is resolved by the prompt documenting the requirement explicitly.

**VERDICT: PASS**

Hand off to PA for acceptance review.
