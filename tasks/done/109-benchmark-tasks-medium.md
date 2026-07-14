---
id: 109
feature: evals
status: done
---

# Author benchmark tasks 008–014 (medium)

Depends on: 105; sibling of 108. Implements ADR-0017 §2,5.

## Scope

8. `008-dependency-repair` — package with a broken import (renamed module); prompt: make
   `main.py` run. verify: exit 0 + expected stdout.
9. `009-multi-file-rename` — rename a public symbol used across 3 files; verify: old name absent
   (grep), tests pass.
10. `010-git-hygiene` — `setup/setup.sh` builds a git repo with relevant + irrelevant dirty files;
    prompt: new branch, stage ONLY the relevant files, one conventional commit. verify: `git log`
    message format, `git status` still-dirty irrelevant files, branch name assert.
11. `011-json-schema-migration` — v1 records file; prompt: migrate to v2 (rename fields, split
    `name`, add `version: 2`). verify: python validation of every record.
12. `012-makefile-doctor` — Makefile broken (spaces-for-tabs, missing dep); verify: `make build`
    exit 0 + artifact exists.
13. `013-sqlite-analyst` — `setup/setup.sh` builds `orders.db`; prompt: script answering "top
    customer by total revenue" → `answer.txt`. verify: compare answer.
14. `014-cli-flag-add` — argparse CLI; prompt: add `--json` output mode preserving default text
    mode. verify: HIDDEN pytest file injected at grade time exercising both modes.

Same contract discipline as 108 (difficulty `medium`, honest `max_steps`, both sanity directions,
sandbox-compatible oracles; `setup.sh` for git/sqlite state).

## Acceptance Criteria

- [x] Seven folders pass loader + both oracle-sanity directions in `make ci`.
- [x] 010/018-style git asserts run against `setup.sh`-built history (no nested committed `.git`).
- [x] 014's hidden tests never touch the Workspace before grade time.
- [x] Spot-run one task through the docker runner; result logged.
- [x] `make ci` green.

## Out of scope

- Tasks 015–020 (110).

## Log

### [SWE] 2026-07-14 — Implementation

**Files added** (all under `evals/benchmark/tasks/`)
- `008-dependency-repair/` — broken import (module `calc` renamed to `arithmetic`); oracle runs
  `main.py` and checks the two output lines. Outcome-graded → any repair shape (edit import, add a
  shim module, re-export) passes.
- `009-multi-file-rename/` — rename `compute_total` → `calculate_total` across billing/report/cli/
  test; oracle greps `--include='*.py'` (old name gone), imports every caller, runs the seeded tests
  (no pytest dep, task-007 pattern).
- `010-git-hygiene/` — `setup/setup.sh` builds the git history (branch main + dirty relevant/
  irrelevant files); oracle asserts branch `add-search`, one Conventional-Commits commit adding
  EXACTLY the two src/ files, scratch files still untracked. Gold git state ships as
  `solution/make_gold.sh` (a git task's solved state can't be a passive file overlay); verify.sh runs
  it only when the oracle-sanity harness overlays `solution/` — never in a real agent run.
- `011-json-schema-migration/` — v1 `records.json` → v2 object (`version: 2`, `mail`→`email`, split
  `name`); oracle compares parsed content keyed on id (JSON formatting/order irrelevant).
- `012-makefile-doctor/` — `setup/setup.sh` generates the BROKEN Makefile (spaces-for-tabs + missing
  dep) so a repo formatter can't "fix" the fixture; `solution/Makefile` is the tab-correct overlay.
  Sandbox image ships no `make` and verify is tool-restricted, so the oracle emulates a tiny portable
  mini-make in python (rejects space-indented recipes, resolves prereqs, rebuilds from clean).
- `013-sqlite-analyst/` — `setup/setup.sh` builds `orders.db` via python stdlib sqlite3 (no committed
  `.db`, no sqlite3-CLI dependency); oracle computes the top customer live from the DB and compares
  `answer.txt` stripped.
- `014-cli-flag-add/` — add `--json` mode to an argparse CLI; HIDDEN `verify/test_cli_modes.py`
  (pytest-style, subprocess-driven) injected only at grade time, executed by import-and-call so no
  pytest install is needed.
- `tests/unit/evals/benchmark/test_oracle_edge_cases_medium.py` — 14 adversarial probes (one
  WRONG-must-FAIL + one ALTERNATIVE-correct-must-PASS per task).

**Tests**
- Unit: 1647 passing, 0 failing (`make unit-tests`) — includes 30 oracle-sanity params (15 tasks ×
  both directions) + 14 adversarial probes.
- Integration + full `make ci`: 1760 passed, 2 skipped (key-gated live smokes) in 425s — green.

**Acceptance criteria**
- [x] Seven folders pass loader + both oracle-sanity directions — `test_oracle_sanity.py` (30 passed).
- [x] 010 git asserts run against `setup.sh`-built history; no committed `.git` (verified every new
  committed asset is not gitignored; `find` shows only setup.sh/make_gold.sh, no `.git`/`.db`).
- [x] 014 hidden tests live in `verify/`, injected only at grade time (standard oracle mechanism; the
  prompt never names them).
- [x] Spot-run: 008 through the REAL docker runner (scripted bash model, no API key) →
  `agent_error=None`, first tool `bash`, `verify exit_code=0`, stdout `PASS`.
- [x] `make ci` green (1760 passed / 2 key-gated skips).

**Attack-your-own-oracles probes (lesson 1 from 108 QA)** — all 14 pass:
- 008: shim-module repair PASSES; runs-but-wrong-value FAILS.
- 009: cosmetically-different full rename PASSES; leftover `compute_total` in one caller FAILS.
- 010: different conventional type/scope + `git add src` PASSES; `git add -A` (scratch committed) FAILS.
- 011: compact + reordered keys/records PASSES; a record keeping `mail` FAILS.
- 012: data step inlined into `build` (no prereq) PASSES; tabs-fixed-but-dep-missing FAILS.
- 013: `"  Bob\n"` (whitespace/newline) PASSES; `Carol` FAILS.
- 014: pretty/sorted JSON PASSES; JSON missing the `greeting` key FAILS.

**Notes**
- `.gitignore` check (lesson 2): no committed setup asset is ignored — no `git add -f` needed. All
  ignorable artifacts (`orders.db`, the git repo, the broken Makefile) are built by `setup.sh`, never
  committed.
- verify.sh tool restriction honored (bash/python3/git/sqlite3): 012 uses a python mini-make instead
  of `make` (absent from the slim sandbox image); 013 uses python's stdlib sqlite3, not the CLI; 014
  runs the hidden pytest file via import-and-call, not `pytest`.
- 010 QA nuance: `git init -q -b main` needs git ≥ 2.28 (host + bookworm both satisfy). The
  `make_gold.sh`-in-`solution/` device is the git analogue of a file overlay; it is the only feasible
  way to drive a solved git state through the passive-overlay oracle-sanity harness and is invisible
  to real agent runs.
- DID NOT COMMIT — handing off to the Tester first.

### [Tester] 2026-07-14 — QA

**Test summary**
- Format / lint: PASS (`make format-check`, `make lint-check`)
- `make pre-commit` (format-check + lint-check + unit-tests): PASS — 1647 passed, 0 failed
- `tests/unit/evals/benchmark/test_oracle_sanity.py`: 30/30 passed (15 tasks × both directions)
- `tests/unit/evals/benchmark/test_oracle_edge_cases_medium.py`: 14/14 passed
- `make integration-tests`: 112 passed / 1 failed / 2 skipped (344s). The failure,
  `tests/integration/test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit`,
  is OUT OF SCOPE for this diff (task 109 touches only `evals/benchmark/tasks/008..014` fixtures + one
  pure-subprocess unit test file; nothing under `src/decode/sandbox/`) and re-ran green in isolation
  (`uv run pytest tests/integration/test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit`
  → 1 passed in 11.19s) — a pre-existing docker-contention flake on this host (many stale
  `Created`/`Exited` containers from unrelated prior runs), not a regression from this task. Not
  blocking, but flagged for On-Call.
- Warnings: 0

**E2E adversarial pass**
- Happy path (own docker spot-run, task 013-sqlite-analyst, different task than the SWE's 008 spot-run):
  scripted `bash` model runs `python3 -c "...SELECT ... GROUP BY ... ORDER BY SUM(amount) DESC..."` →
  writes `answer.txt`, then finishes. Result: `agent_error=None`, `tool_calls=[bash]`,
  `verify exit_code=0`, `verify stdout=PASS`. Confirms the real `benchmark_sandbox`/docker pipeline
  (setup.sh-in-container, bash-tool routing, grade-time `verify/` injection) end-to-end for a second
  medium task. PASS.
- Break path 1 (WRONG, per-task, own inputs distinct from the SWE's probes) — all correctly FAIL:
  008 program that runs but prints an extra trailing line; 009 rename with a leftover mention of
  `compute_total` in a *comment* (`grep` catches it, stricter than required); 010 two separate commits
  instead of one, and a non-Conventional-Commits subject; 011 `version` as the string `"2"` instead of
  int `2`, a dropped record, malformed/empty JSON; 012 build succeeds but `artifact.txt` has the wrong
  content; 013 `"bob"` (case mismatch) and `"Alice"` (wrong customer); 014 `--json` prints an extra
  non-JSON line before the JSON object (unparseable stdout).
- Break path 2 (ALTERNATIVE-CORRECT, own inputs) — 6/7 PASS as required, **012 FAILS incorrectly**:
  008 inline-reimplementation repair (no import of either module) → PASS. 009 different rounding
  implementation, same results → PASS. 010 `git checkout -b add-search && git add src && git commit`
  (add-by-directory instead of by-file) → PASS. 013 not separately re-probed beyond the SWE's
  whitespace case (already covered). 014 not separately re-probed beyond the SWE's pretty/sorted-JSON
  case (already covered). **012: a Makefile that fixes both bugs correctly but adds a one-line
  explanatory comment containing a colon between the target header and its recipe — completely valid
  GNU Make (`make` ignores `#` comments unconditionally) — is wrongly graded FAIL by the mini-make
  oracle.** See "Acceptance criteria" below for the reproduction and root cause. FAIL.
- Break path 3 (malformed / boundary inputs) — 011 with `{not valid json` and an empty file both FAIL
  cleanly with a readable message (no crash, no stack trace to the "agent"). PASS.
- Contract checks: prompts in all seven `task.yaml` files never mention verify/oracle/solution/grade
  (`grep -rniE "verify|oracle|solution|grade"` → no hits); every `verify/` file uses only
  bash/python3/git/sqlite3 (read every `verify.sh` + `verify/test_cli_modes.py`); `git check-ignore -v`
  on every new committed asset under `evals/benchmark/tasks/0{08,09,10,11,12,13,14}*` → none ignored;
  `find ... -name .git -o -name "*.db"` → none (no nested `.git`, no committed sqlite DB); `grep -rn
  "solution"` over `evals/harness/benchmark.py` + `evals/harness/sandbox.py` (the real runner path) →
  no hits, confirming `solution/` never enters an agent run. PASS.
- 010 `make_gold.sh` deviation: reproduced the oracle-sanity harness's exact Workspace order by hand
  (seed `setup/` → run `setup.sh` → overlay `solution/make_gold.sh` → inject `verify/` → run
  `verify.sh`) — genuinely exercises the solution→PASS direction (not vacuous): PASS with output
  `PASS`. Reproduced the untouched direction too (no overlay): correctly FAILs
  (`FAIL: expected to be on branch 'add-search', but HEAD is 'main'`). The device is sound: `verify.sh`
  only branches on `[[ -f make_gold.sh ]]`, and `solution/` (where `make_gold.sh` lives) is never
  copied by the real runner (`sandbox.py`/`benchmark.py` never reference `solution` — see contract
  check above), so a real agent run never has that file and the guard is dead code there.

**Acceptance criteria**
- [x] PASS — Seven folders pass loader + both oracle-sanity directions in `make ci` —
      `tests/unit/evals/benchmark/test_oracle_sanity.py` 30/30 passed (own re-run).
- [x] PASS — 010/018-style git asserts run against `setup.sh`-built history, no nested committed
      `.git` — `evals/benchmark/tasks/010-git-hygiene/verify/verify.sh` parses live `git`
      porcelain/log; `find evals/benchmark/tasks/0{08..14}* -name .git -o -name "*.db"` → empty.
- [x] PASS — 014's hidden tests never touch the Workspace before grade time —
      `evals/benchmark/tasks/014-cli-flag-add/setup/` contains only `cli.py` (confirmed via `find`);
      `evals/harness/sandbox.py::SandboxRun.grade` injects `verify/` only after the agent run, and
      `evals/harness/benchmark.py`/`sandbox.py` never reference `solution` either.
- [x] PASS — Spot-run one task through the docker runner; result logged — reproduced independently for
      013-sqlite-analyst (see Happy path above): `agent_error=None`, `verify exit_code=0`, stdout
      `PASS`. (SWE's own 008 spot-run is plausible and consistent with this independent run.)
- [x] PASS — `make ci` green — `format-check`/`lint-check`/`pre-commit`(1647 passed) all green; the one
      integration failure is a pre-existing, diff-unrelated flake that passes in isolation (see Test
      summary).
- [x] FIXED (see round-2 re-verification below) — Oracle fairness for 012-makefile-doctor (task-109
      mandate: "ALTERNATIVE-CORRECT shape must PASS", ADR-0017 §5 "a broken oracle can't silently
      grade everything up or down").
      Expected: a correctly-repaired Makefile (tab-indented recipe, `build` depends on `prepare`) that
      also carries an explanatory comment containing a colon (e.g. `# Fixed: use tab now`) between a
      target header and its recipe must PASS — that is valid, idiomatic GNU Make and a very plausible
      shape for an agent's fix.
      Actual: `evals/benchmark/tasks/012-makefile-doctor/verify/verify.sh`'s `parse_makefile()` (lines
      23-45) never special-cases `#` comment lines before the `if ":" in raw:` target-header check
      (line 40). A comment containing a colon is misparsed as a bogus target definition, which steals
      `current` away from the real target, so the next tab-indented recipe line gets appended to the
      bogus target instead of `build` — `build`'s own recipe list ends up empty, `artifact.txt` is
      never created, and the oracle prints `FAIL: building did not produce artifact.txt`. Repro:
      ```
      $ printf 'build: prepare\n# Fixed: use tab now\n\tcat data.txt > artifact.txt\n\nprepare:\n\tprintf "payload\\n" > data.txt\n' > Makefile
      $ bash verify.sh
      FAIL: building did not produce artifact.txt
      ```
      Fix: in `parse_makefile()`, skip lines whose first non-tab character is `#` (comment) before the
      blank-line / space-indent / colon checks — e.g. `if raw.lstrip().startswith("#"): continue`
      right after the blank-line check (~line 34), so a comment can never be mistaken for a target
      header or silently swallowed into the wrong recipe.

**Evidence**
```
$ uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -v
...
============================== 30 passed in 2.42s ==============================

$ make pre-commit
...
======================= 1647 passed in 98.33s (0:01:38) ========================

$ make integration-tests
...
FAILED tests/integration/test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit
============= 1 failed, 112 passed, 2 skipped in 344.61s (0:05:44) =============

$ uv run pytest tests/integration/test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit -v
tests/integration/test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit PASSED [100%]
============================== 1 passed in 11.19s ==============================

# own docker spot-run, 013-sqlite-analyst
agent_error: None
tool_calls: [ToolCallRecord(name='bash', args={'command': "python3 -c \"...SELECT cu.name FROM customers cu JOIN orders o ON o.customer_id=cu.id GROUP BY cu.id ORDER BY SUM(o.amount) DESC LIMIT 1...\""})]
output: wrote answer.txt from a live DB query
verify exit_code: 0
verify stdout: PASS

# 012 alternative-correct-with-comment, mis-graded FAIL
$ bash verify.sh
FAIL: building did not produce artifact.txt
```

**Other issues found**
- 011's oracle rejects a migrated record that keeps an EXTRA unspecified field alongside a fully
  correct migration (strict dict equality in `verify.sh`). The prompt's field list reads as exhaustive
  ("The new file must be a single JSON object with two top-level keys... In every migrated record:
  rename..., replace..., keep... unchanged"), so this strictness is a defensible reading, not a bug —
  noted for visibility only, no fix required.
- `docker ps -a` on this host shows ~15+ stale `Created`/`Exited` containers accumulated from prior
  unrelated runs (unrelated to this diff) — likely contributor to the `test_sandbox_teardown.py` flake
  above; worth a `docker container prune` before the next full `make ci` run, not a task-109 fix.

**VERDICT: FAIL**
1 issue: the 012-makefile-doctor oracle's `parse_makefile()` misparses a comment line containing a
colon as a target header, incorrectly failing a plausible alternative-correct repair. SWE to fix
`evals/benchmark/tasks/012-makefile-doctor/verify/verify.sh` (skip `#`-prefixed lines before the
target-header check) and add a regression probe to
`tests/unit/evals/benchmark/test_oracle_edge_cases_medium.py` covering a correct Makefile fix with an
inline comment. All other seven tasks and every other acceptance criterion verified PASS with
evidence above — expect a fast second pass.

### [SWE] 2026-07-14 — Fixes (QA round 1)

**Issue (Tester, blocking):** 012-makefile-doctor `verify.sh::parse_makefile()` misparsed a `#`
comment containing a colon (e.g. `# Fixed: use a tab`) sitting between a target header and its tab
recipe as a bogus target — the real recipe attached to the comment-"target", `build`'s recipe came
out empty, `artifact.txt` was never produced, so a CORRECT Makefile repair graded FAIL.

**Fix**
- `evals/benchmark/tasks/012-makefile-doctor/verify/verify.sh` — in `parse_makefile()`, skip any line
  whose first non-whitespace char is `#` (a make no-op) at the TOP of the loop, before the
  tab/blank/space-indent/colon checks, so a colon inside a comment is never read as a target header.
- `tests/unit/evals/benchmark/test_oracle_edge_cases_medium.py` — added
  `test_012_passes_with_a_colon_comment_between_header_and_recipe` (regression probe: comment-with-colon
  between header and recipe must PASS).

**Verification**
- Exact Tester repro now prints `PASS` (exit 0).
- 012 oracle-sanity both directions + all medium probes: 45 passed (`-k "012 or oracle"`).
- `make format-check` / `make lint-check`: clean.
- `make pre-commit` (format+lint+unit): 1648 passed, 0 failing (+1 new regression probe).

**Notes**
- Tab-indented comments (`\t# ...`, a shell no-op recipe line) are now skipped too; build behaviour is
  identical since they produce no command.
- DID NOT COMMIT — handing back to the Tester for re-review.

### [Tester] 2026-07-14 — QA re-verification (round 2)

**Test summary**
- Format / lint: PASS (`make format-check`, `make lint-check`)
- `make pre-commit` (format-check + lint-check + unit-tests): PASS — 1648 passed, 0 failed (matches
  SWE's claim: +1 vs round 1's 1647, the new regression probe)
- `tests/unit/evals/benchmark/` (oracle-sanity 30 + edge-cases-medium 15 + edge-cases-easy 2): 47/47
  passed; the 012+oracle-sanity subset alone: 45 passed (`-k "012 or oracle"` — matches SWE's claim)
- Round-1 integration flake (`test_sandbox_teardown.py::...reaps_the_real_container_on_exit`) not
  re-run this round — it was already established as diff-unrelated (passes in isolation, touches no
  file in this diff) and is not gating this task's verdict.
- Warnings: 0

**Re-verification of the round-1 blocker**
- Exact round-1 repro against the FIXED oracle:
  ```
  $ printf 'build: prepare\n# Fixed: use tab now\n\tcat data.txt > artifact.txt\n\nprepare:\n\tprintf "payload\\n" > data.txt\n' > Makefile
  $ bash verify.sh
  PASS
  ```
  PASS (was `FAIL: building did not produce artifact.txt` in round 1).
- New probe `test_012_passes_with_a_colon_comment_between_header_and_recipe` matches my round-1 repro
  shape exactly (comment with a colon between `build: prepare` and its tab recipe).
- Revert-check (cheap, as requested): temporarily stripped the `if raw.lstrip().startswith("#"):
  continue` guard back out of `parse_makefile()` and re-ran the new probe in isolation —
  `FAILED ... AssertionError: expected PASS, got exit 1 / FAIL: building did not produce
  artifact.txt` — confirms the probe is a genuine regression test (goes RED against the unfixed
  parser), not vacuous. Restored the fix immediately after (verified via `grep
  "lstrip().startswith" verify.sh` and a clean `git status`).
- FAIL-direction sanity (own inputs, not reusing round-1 probes):
  - untouched `setup/`-built Makefile (space-indented recipe, missing `prepare` dep) still FAILs:
    `FAIL: make build would not succeed: line 2: recipe is indented with spaces, not a tab`.
  - tabs fixed but `prepare` prerequisite still missing still FAILs:
    `FAIL: ... recipe for 'build' failed: 'cat data.txt > artifact.txt' / cat: data.txt: No such
    file or directory`.
  - a Makefile that is nothing BUT comments (including one with a colon) does NOT vacuously pass —
    `FAIL: make build would not succeed: no rule to make target 'build'`. The `#`-skip does not turn
    the oracle into an always-pass rubber stamp.
- `git status --porcelain`: only the same 7 task folders + the one test file are untracked, plus the
  task-109 tracker file modified — no unrelated files touched by the fix.

**Acceptance criteria** — all 5 (verified round 1, unchanged) + the round-1 blocker: all PASS now.

**VERDICT: PASS**
The 012-makefile-doctor oracle fairness blocker from round 1 is fixed and independently re-verified:
exact repro now passes, the new regression probe is real (goes red against the unfixed parser), and
the fix does not weaken the FAIL direction (broken Makefiles, missing-dep Makefiles, and
comment-only Makefiles all still correctly FAIL). Full suite green (1648 unit / 0 failed, 47/47
benchmark oracle tests, 0 warnings), no unrelated files touched. Hand off to PA for acceptance
review.
