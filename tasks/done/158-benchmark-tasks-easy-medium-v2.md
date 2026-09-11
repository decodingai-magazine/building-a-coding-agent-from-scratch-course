---
id: 158-benchmark-tasks-easy-medium-v2
feature: evals-v2
status: done
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
- [x] Thirteen folders (`001`–`009`, `011`–`014`) on the v2 layout (`task.toml`, `instruction.md`, `environment/`, `tests/test.sh`, `solution/solve.sh`), legacy `task.yaml`/`setup/`/`verify/` gone from them; values per the table above; each `task.toml` carries a `description` one-liner. `evals/benchmark/tasks/010-git-hygiene/` deleted together with any test that names it.
- [x] Every instruction rewritten against the checklist: exact deliverable paths and names; the self-check command that works in the sandbox image; no mention of hidden assets; ≤ 250 words. 007/009/014 test files converted to `unittest.TestCase` so `python3 -m unittest -v <module>` runs in the image; instructions name that command.
- [x] `make ci` green: oracle-sanity proves all 13 both directions; canary test green; for 007/009/014 the nop direction demonstrates the F2P tests fail on base.
- [x] `tests/unit/evals/benchmark/test_verifier_edge_cases_easy.py` / `_medium.py`: every edge case from the deleted `test_oracle_edge_cases.py` / `_medium.py` (except 010's) has a counterpart against the new verifier (near-miss workspace → reward 0; tolerant-format workspace → reward 1); file header lists the ported cases.
- [x] `git diff --stat` shows no change outside `evals/benchmark/tasks/` and `tests/unit/evals/benchmark/`.

## Out of scope
- Hard tasks 015–020 (159). New tasks. Loader changes (157). Reframing 010 (dropped, not reframed).

## Log
### [PA] 2026-09-10 — Grooming
Calibration rule of thumb for a 35B MoE with tools: an easy task should complete in ≤ 6 requests with a 15-step cap (the cap is never the reason it fails); medium in ≤ 12 with 20–25. The `unittest` conversion is what makes "run the tests" a real instruction inside a pytest-less image — the old prompts assumed Claude-level improvisation. 010's premise is unobservable through Hand-back; the human chose to drop it rather than reframe it (6 medium tasks remain).

### [SWE] 2026-09-11 14:20 — Implementation

**Files modified**
- `evals/benchmark/tasks/{001..009,011..014}/` — thirteen tasks rebuilt on the v2 layout
  (`task.toml` + `instruction.md` + `environment/` + `tests/test.sh` + `solution/solve.sh`); legacy
  `task.yaml` / `setup/` / `verify/` deleted from each.
- `evals/benchmark/tasks/010-git-hygiene/` — DELETED (ADR-0022 §3: its premise is unobservable
  through the Hand-back). No test named it; the numbering gap stays.
- `evals/benchmark/tasks/{007,009,014}/tests/test_*.py` — the hidden, graded `unittest.TestCase`
  suites (overlaid last); `{007,009}/environment/test_*.py` — the agent-visible seed copies, also
  `unittest.TestCase` so `python3 -m unittest -v <module>` works in the pytest-less image.
- `evals/benchmark/tasks/README.md` — the "nineteen tasks land later" paragraph now states what is
  actually on the format (13 here, 6 hard still legacy, gap at 010).
- `tests/unit/evals/benchmark/conftest.py` — NEW: `seed_workspace` / `grade_workspace` /
  `assert_reward` fixtures, i.e. grade time with the Oracle step swapped for an arbitrary answer.
- `tests/unit/evals/benchmark/test_verifier_edge_cases_easy.py` / `_medium.py` — NEW: the ported
  adversarial cases (header lists each v1 case → its v2 counterpart).

**Tests**
- Unit: 2494 passed, 1 skipped (`fastapi` absent) — `make unit-tests`. Benchmark slice: 76 passed
  (`tests/unit/evals/benchmark`), up from 9 (fixture-only) before.
- Integration: 115 passed in 400 s — `make integration-tests`.
- `uv lock --check` + `make format-check` + `make lint-check` + `make pre-commit` clean.

**Acceptance criteria**
- [x] Thirteen v2 folders, values per the conversion table — verified by
  `evals/harness/task_loader.load_benchmark_tasks()` (13 loaded, table below) and
  `tests/unit/evals/benchmark/test_oracle_sanity.py`.
- [x] Instructions rewritten to the checklist (≤ 250 words: max is 170, 012) — every named
  self-check command executed inside the real sandbox image (evidence below).
- [x] Oracle gate green both directions for all 13 + canary green; the nop direction of 007/009/014
  shows the F2P tests failing on base (`Ran 4 tests … FAILED (failures=1)` for 007).
- [x] Every v1 edge case except 010's has a v2 counterpart asserting on the REWARD (0.0 / 1.0,
  never an exit code), plus two the v1 suite could not state (005's seeded-bytes premise; declared
  node ids vs. what `test.sh` runs).
- [x] `git diff --stat` touches only `evals/benchmark/tasks/` and `tests/unit/evals/benchmark/`
  (plus this tracker file, which the role mandates).

**Evidence**
```
$ uv run python -c "... load_benchmark_tasks() ..."
13 tasks loaded
001-find-and-replace      easy   Operations steps=12 agent=600s verif=120s f2p=0 p2p=0
002-regex-extraction      easy   Software   steps=15 agent=600s verif=120s f2p=0 p2p=0
003-csv-to-json           easy   Software   steps=15 agent=600s verif=120s f2p=0 p2p=0
004-markdown-toc          easy   Software   steps=15 agent=600s verif=120s f2p=0 p2p=0
005-encoding-normalize    easy   Software   steps=15 agent=600s verif=120s f2p=0 p2p=0
006-log-forensics         easy   Security   steps=15 agent=600s verif=120s f2p=0 p2p=0
007-fix-failing-test      easy   Software   steps=15 agent=600s verif=120s f2p=1 p2p=3
008-dependency-repair     medium Software   steps=20 agent=900s verif=120s f2p=0 p2p=0
009-multi-file-rename     medium Software   steps=25 agent=900s verif=120s f2p=2 p2p=0
011-json-schema-migration medium Software   steps=20 agent=900s verif=120s f2p=0 p2p=0
012-makefile-doctor       medium Operations steps=20 agent=900s verif=120s f2p=0 p2p=0
013-sqlite-analyst        medium Science    steps=20 agent=900s verif=120s f2p=0 p2p=0
014-cli-flag-add          medium Software   steps=25 agent=900s verif=120s f2p=1 p2p=2

$ uv run pytest tests/unit/evals/benchmark -q
76 passed in 3.69s

$ make unit-tests
2494 passed, 1 skipped in 39.67s

$ make integration-tests
115 passed in 400.77s (0:06:40)

$ make ci
uv lock --check / format-check / lint-check / pytest
2609 passed, 1 skipped in 448.76s (0:07:28)

# adversarial probe (by hand, not committed): plant a trivially-passing suite where the HIDDEN one
# lands, then grade — the overlay must still win.
007 planted tests/test_ranges.py     -> reward 0
007 planted tests/ranges.py (shadow) -> reward 0
014 planted tests/test_cli_modes.py  -> reward 0
009 planted tests/test_billing.py    -> reward 0
001 swapped tests/expected.ini       -> reward 0

# e2e — every instruction's self-check command, run in the REAL sandbox image
# (ghcr.io/astral-sh/uv:python3.12-bookworm-slim) against a real Seed Repo, solved and unsolved:
$ docker run --rm -v /tmp/decode-158-solved/007-fix-failing-test:/workspace -w /workspace $IMG \
    bash -lc 'python3 -m unittest -v test_ranges'
Ran 4 tests in 0.000s
OK
$ ... same, on the SEED: Ran 4 tests ... FAILED (failures=1)
$ 012 (seed):   cat -A Makefile  ->  "build:$" / "    cat data.txt > artifact.txt$"  (spaces)
$ 012 (solved): cat -A Makefile  ->  "build: prepare$" / "^Icat data.txt > artifact.txt$"
$ 013 (solved): cat answer.txt -> Bob; python3 -c "import sqlite3; ..." -> [('Alice',),('Bob',),('Carol',)];
                which sqlite3 -> absent (as the instruction says)
$ 014 (solved): python3 cli.py Ada --times 2 -> two lines; --json -> {"name": "Ada", "times": 3, ...}
$ 014 (seed):   python3 cli.py Ada --json -> "error: unrecognized arguments: --json"
$ 008 (solved): python3 main.py -> area=12.57 / fact=120
$ 006 (solved): python3 ban_ips.py -> ["10.0.0.1", "10.0.0.3"]
$ 009 (solved): python3 -m unittest -v test_billing -> OK; grep -rn compute_total . -> exit 1 (no match)
$ 005 (solved): both files print their accented text under encoding='utf-8'
```

**Notes**
- **`make` is absent from the sandbox image** (`docker run --rm <image> which make git sqlite3 pytest`
  → only `python3`; the docker/modal backends add only `git`+`gh`). So 012's instruction states that
  plainly, names BOTH repairs explicitly (tab-indent the recipe; `build` must depend on `prepare`)
  and gives `cat -A Makefile` as the self-check — verified to render tabs as `^I` in that image.
  013's instruction likewise states the `sqlite3` CLI is absent and points at python's module.
- **013's verifier now hard-codes `Bob`** instead of re-querying `orders.db`. Under v2 the Verifier
  grades a checkout OF THE AGENT'S BRANCH, so a live query would grade against a database the agent
  could have rewritten to make a wrong `answer.txt` right. Same reasoning 011 already used. The seed
  data is fixed and the oracle still COMPUTES its answer, so gold and data cannot drift apart.
- **007's sha256 tamper check is gone** (per the task spec): `tests/` is overlaid last, so the
  agent's own copy of a test file is inert by construction.
- The hidden suites are run as `(cd tests && python3 -m unittest -v <node ids>)` and put the checkout
  root on `sys.path` from inside the module, so `ranges`/`billing` always resolve to the agent's
  source and a same-named file planted in `tests/` cannot shadow it.
- Two tests beyond the port, both cheap: 005's premise (the COMMITTED blobs really are NOT valid
  UTF-8 — read out of the object store, since a trial hands the agent a clone; the two-direction gate
  structurally cannot see this) and a drift guard that every declared
  `[verifier.tests]` node id appears verbatim in that task's `test.sh`.
- 015–020 keep the legacy layout and stay invisible to the loader until task 159.

### [Tester] 2026-09-11 04:30 — QA

**Test summary**
- Format / lint: PASS (`ruff format --check` 324 files; `ruff check` all passed)
- `uv lock --check`: PASS
- Unit tests: 2494 passed / 1 skipped (`fastapi` absent, pre-existing) — includes
  `tests/unit/evals/benchmark/` (76 passed: oracle-sanity both directions x13, canary x13x3,
  edge-case suites, drift guards)
- Integration tests: 115 passed in 372s (`test_benchmark_docker.py` included, docker-gated)
- Warnings: 0
- code-review plugin: enabled in `.claude/settings.json` but not invokable from this Tester's tool
  set (no slash-command/plugin-invocation tool available) — flagging the gap explicitly rather than
  silently skipping it, per the review protocol.

**E2E adversarial pass**
- Happy path: solved 001 and 003 via `solution/solve.sh`, ran the documented self-check
  (`cat config.ini`, `python3 -c "import json; print(json.load(open('people.json')))"`) inside the
  real `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` image → matched the gold content (PASS).
- Break path 1 (right content, wrong file name — 005): wrote gold `cafe.txt`/`zurich.txt` content to
  `cafe_utf8.txt`/`zurich_utf8.txt` → reward 0.0 (PASS).
- Break path 2 (stale reference in a docstring/comment — 009): fully renamed
  `compute_total`→`calculate_total` at every call site but left `"""Billing math. Ported from
  compute_total (legacy name)."""` in `billing.py` → reward 0.0, stdout names the match. The
  instruction explicitly says "the text `compute_total` must not appear in ANY `.py` file" (i.e.
  comments count) — the verifier's `grep -I --include='*.py' '\bcompute_total\b'` matches that
  wording exactly; this is by design, not a bug (PASS).
- Break path 3 (agent-authored always-passing test shadows the hidden suite — 007 and 014): planted
  a `tests/test_ranges.py` / `tests/test_cli_modes.py` with every method replaced by `pass`, via
  `grade_workspace` (which overlays the task's real `tests/` LAST, exactly as `oracle_sanity.py`
  does) → reward 0.0 both times, i.e. the overlay wins and the plant is inert (PASS).
- Break path 4 (tolerant vs. strict formatting — 013): `answer.txt` = `"Bob\n\n"` (extra trailing
  blank line) → reward 1.0 (matches the documented "compared stripped of whitespace" contract);
  `Answer.txt` (wrong case) → reward 0.0 on a genuine Linux filesystem, confirmed by copying files
  *into* a container (not bind-mounting, which on Docker Desktop for Mac silently inherits macOS's
  case-insensitive APFS and gave a false positive on the first attempt — noted as a testing-harness
  gotcha, not a product bug) (PASS).
- Break path 5 (extra blank lines around an otherwise-correct fix — 012): Makefile with leading/
  trailing blank lines around the correct `build: prepare` + tab-indented recipe → reward 1.0
  (PASS, verifier is correctly whitespace-tolerant).
- All rewards observed were exactly `0.0` or `1.0`, per the README contract (never a stray exit
  code, never `None`/error read as zero).

**Acceptance criteria**
- [x] PASS — Thirteen v2 folders (001-009,011-014), legacy `task.yaml`/`setup/`/`verify/` gone,
      values per the conversion table, `010-git-hygiene/` deleted with no test naming it as a task —
      Evidence: `ls evals/benchmark/tasks/` (010 absent); independently re-ran
      `load_benchmark_tasks()` → 13 tasks, every (category, steps, agent_timeout, verif_timeout,
      f2p count, p2p count) column matches the table in the spec exactly; `find … -name task.yaml`
      / `setup/` / `verify/` under the 13 folders → none found.
- [x] PASS — Every instruction rewritten to the checklist (≤250 words incl. canary line: max
      observed 170 words on 014; none mention `tests/`/`solution/`/`reward`/`verif`; exact
      deliverable path + self-check command named) — Evidence: `wc -w */instruction.md` on all 13
      (max 170); `grep -il 'tests/\|solution/\|reward\|verif' */instruction.md` → no hits; read
      all 13 instructions in full, incl. 007 and 014 (initially missed, added on advisor
      follow-up) — both name `python3 -m unittest -v test_ranges` / point at
      `python3 cli.py Ada --times N [--json]` as the self-check.
- [x] PASS — `make ci` green both directions for all 13 + canary + nop direction for 007/009/014
      shows the F2P node ids failing on base — Evidence: `make unit-tests` 2494 passed (incl.
      `test_oracle_sanity.py` and `test_canary.py`, both parametrized over all 13 real tasks);
      manually ran the nop direction for all three: 007 → `test_inclusive_end` FAILED, the other
      three OK; 014 → `test_json_mode` FAILED, the other two OK; 009 → bypassing the grep gate and
      running `python3 -m unittest -v test_billing.TestBilling.test_sum_without_tax
      test_billing.TestBilling.test_sum_with_tax` directly on the bare seed gives `ERROR` on BOTH
      declared F2P node ids (`ImportError: cannot import name 'calculate_total'`) — the declared
      ids are demonstrably attributable failures, not a grep side-effect.
- [x] PASS — Every v1 edge case ported (except 010's, dropped with rationale) — Evidence: diffed
      against `git show 34b78eb:tests/unit/evals/benchmark/test_oracle_edge_cases{,_medium}.py`:
      easy v1 had exactly 2 cases (002, 006), both present in v2 with 1:1 name mapping stated in the
      new file's header; medium v1 had 15 cases, 13 ported + 010's 2 explicitly dropped (matches
      the header's own table); both files' header-claimed mappings verified against the actual
      `def test_...` names in the new files.
- [x] PASS — `git diff --stat` touches only `evals/benchmark/tasks/`, `tests/unit/evals/benchmark/`
      (plus the task tracker file, mandated) — Evidence:
      `git status --porcelain | grep -v '^evals/benchmark/tasks/\|^tests/unit/evals/benchmark/\|^tasks/158-'`
      → empty.

**Evidence**
```
$ make unit-tests
2494 passed, 1 skipped in 39.96s
$ make integration-tests
115 passed in 372.07s (0:06:12)
$ uv lock --check
Resolved 146 packages in 3ms
$ uv run python -c "from evals.harness.task_loader import load_benchmark_tasks; ..."
13 tasks loaded  (table matches spec exactly, re-run independently by the Tester)
$ (cd tests && python3 -m unittest -v test_billing.TestBilling.test_sum_without_tax test_billing.TestBilling.test_sum_with_tax)   # 009, bare seed
ERROR: test_sum_without_tax ... ImportError: cannot import name 'calculate_total' from 'billing'
ERROR: test_sum_with_tax ... ImportError: cannot import name 'calculate_total' from 'billing'
FAILED (errors=2)
```

**Other issues found**
- None blocking. Two notes for the record, not fixes needed: (1) the code-review plugin could not
  be invoked from this Tester's tool surface — orchestrator should be aware that signal is missing
  from this review; (2) verifying case-sensitivity-dependent near-misses (013's `Answer.txt`) via a
  Docker-Desktop-for-Mac bind mount gives a false positive because that mount inherits macOS's
  case-insensitive APFS — copying files *into* the container (not bind-mounting) is required for a
  trustworthy result on a Mac laptop; the actual sandbox backends (Modal, Linux docker hosts) are
  unaffected.

**VERDICT: PASS**
