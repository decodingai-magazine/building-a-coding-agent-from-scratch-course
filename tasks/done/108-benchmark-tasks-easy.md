---
id: 108
feature: evals
status: done
---

# Author benchmark tasks 001–007 (easy)

Depends on: 105 (contract + oracle-sanity harness); runnable via 106. Implements ADR-0017 §2,5.

## Scope

Author seven task folders under `evals/benchmark/tasks/`, each with `task.yaml` + `setup/` +
`verify/verify.sh` (+ hidden tests where noted) + `solution/`:

1. `001-find-and-replace` — `setup/config.ini` with `timeout = 30`; prompt: set it to 60.
   verify: grep new value present, old absent, rest of file unchanged.
2. `002-regex-extraction` — `setup/contacts.txt` (emails buried in prose, duplicates); prompt:
   unique emails, sorted, one per line → `emails.txt`. verify: `diff` against expected.
3. `003-csv-to-json` — `setup/people.csv`; prompt gives the exact JSON schema (list of objects,
   typed fields) → `people.json`. verify: python deep-equal against expected structure.
4. `004-markdown-toc` — `setup/README.md` with nested headings; prompt: insert a linked TOC under
   the `## Table of Contents` marker. verify: diff of the TOC block.
5. `005-encoding-normalize` — `setup/setup.sh` writes `.txt` files in latin-1 + utf-16; prompt:
   normalize all to UTF-8 preserving content. verify: python decodes each as strict UTF-8 and
   compares content.
6. `006-log-forensics` — `setup/access.log` (seeded); prompt: write `ban_ips.py` printing a JSON
   array of IPs with ≥5 404s. verify: EXECUTE the script and compare parsed JSON (order-free).
7. `007-fix-failing-test` — mini package with an off-by-one; one failing test, several passing;
   prompt: fix the code, don't touch tests. verify: FAIL_TO_PASS flips AND PASS_TO_PASS holds AND
   the test file's checksum is unchanged.

All `task.yaml`s: `difficulty: easy`, honest `max_steps`, tags. Prompts name concrete
deliverable paths but never the oracle. Every oracle proven both directions by the 105 sanity
harness (solution→PASS, untouched→FAIL).

## Acceptance Criteria

- [x] Seven folders pass the loader contract and BOTH oracle-sanity directions in `make ci`.
- [x] verify.sh uses only bash/python/git/sqlite3 (sandbox-image-compatible).
- [x] No prompt mentions verify assets; no verify asset lives under `setup/`.
- [x] Spot-run at least one task through `python -m evals benchmark --task 001` (docker) — PASS or
      an honest FAIL recorded in the log.
- [x] `make ci` green.

## Out of scope

- Tasks 008–020 (109, 110). Runner changes.

## Log

### [SWE] 2026-07-14 09:40 — Implementation

**Files added** (all under `evals/benchmark/tasks/`)
- `001-find-and-replace/` — task.yaml + setup/config.ini + solution/config.ini + verify/{verify.sh,expected.ini}. Oracle: grep new value present / old absent + exact diff vs expected.ini.
- `002-regex-extraction/` — setup/contacts.txt (buried, duplicated emails) + solution/emails.txt + verify/{verify.sh,expected.txt}. Oracle: strict diff of the unique, ascending-sorted list.
- `003-csv-to-json/` — setup/people.csv + solution/people.json + verify/{verify.sh,expected.json}. Oracle: python `json.load` deep-equal (enforces int/bool types, not stringified).
- `004-markdown-toc/` — setup/README.md (nested headings + `## Table of Contents` marker) + solution/README.md + verify/{verify.sh,expected_toc.txt}. Oracle: extract the block under the marker (to next `## `), trim blank edges, compare to expected linked TOC.
- `005-encoding-normalize/` — setup/setup.sh (writes cafe.txt latin-1, zurich.txt utf-16) + solution/{cafe,zurich}.txt (UTF-8) + verify/{verify.sh,expected_cafe.txt,expected_zurich.txt}. Oracle: strict UTF-8 decode + BOM reject + content match. setup.sh is required — mixed encodings can't be committed UTF-8 files.
- `006-log-forensics/` — setup/access.log (seeded CLF) + solution/ban_ips.py + verify/verify.sh. Oracle: EXECUTES ban_ips.py, parses stdout JSON, set-equality (order-free) vs {10.0.0.1, 10.0.0.3} (the only IPs with ≥5 404s).
- `007-fix-failing-test/` — setup/{ranges.py (off-by-one `range(1,n)`),test_ranges.py} + solution/ranges.py (`range(1,n+1)`) + verify/{verify.sh,test_ranges.sha256}. Oracle: sha256 of test file unchanged (agent mustn't touch tests) AND FAIL_TO_PASS (`test_inclusive_end`) flips AND PASS_TO_PASS (3 tests) holds. Runs tests WITHOUT pytest (import module, call fns) so the oracle needs only python.

**Contract adherence**
- Every `verify.sh` uses only bash + `python3` (no git/sqlite3 needed; confirmed `python`/`python3` both present in `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` and host).
- No prompt names any verify asset; no verify asset lives under `setup/` (checked below).
- Only 005 ships a `setup/setup.sh` — the one task whose state (latin-1/utf-16 bytes) can't be a committed UTF-8 file. The other six express their seed state as committed files.

**Tests**
- Unit: `tests/unit/evals/benchmark/test_oracle_sanity.py` auto-parametrizes over the 7 new tasks — 16 passing (fixture + 7 tasks × 2 directions), proving each oracle PASS-on-solution and FAIL-on-untouched-setup.
- Full evals unit suite: 129 passing. Whole unit suite via pre-commit: 1617 passing.
- Integration: N/A — no harness/runner code changed; tasks are data + auto-parametrized oracle-sanity.

**Acceptance criteria**
- [x] Seven folders pass loader contract + BOTH oracle-sanity directions in `make ci` — `test_oracle_sanity.py` 16 passing.
- [x] verify.sh uses only bash/python — sandbox-image-compatible.
- [x] No prompt mentions verify assets; no verify asset under `setup/`.
- [x] Spot-run 001 on docker — PASS (evidence below).
- [x] `make ci` green — 1730 passed, 2 skipped (key-gated only).

**Evidence**
```
$ uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -q
................                                                         [100%]
16 passed in 1.24s

# Spot-run — task 001 through the REAL docker benchmark lifecycle. Keys (OPIK/GEMINI) are absent in
# this env, so I used the docker integration path with the scripted-model pattern
# (tests/integration/test_benchmark_docker.py style): a scripted FunctionModel edits config.ini via
# the sandboxed bash tool, the hidden verify/ is injected through the seam only at grade time.
$ uv run python scratchpad/spot_run_001.py
agent_error: None
infra_error: None
tool_calls: ['bash']
verify.exit_code: 0
verify.stdout: PASS
RESULT: PASS

$ make ci    # uv lock --check + format-check + lint-check + make test
...
================= 1730 passed, 2 skipped in 407.46s (0:06:47) ==================
# 2 skips are the key-gated live Opik/Gemini smokes only.

# No verify asset under any setup/, and no prompt names verify assets:
$ find evals/benchmark/tasks/00[1-7]-*/setup -name 'expected*' -o -name 'verify*'
(no output)
```

**Notes**
- Spot-run variant used: docker + scripted-model (no `GEMINI_API_KEY`/`OPIK_API_KEY` in env), NOT the live `python -m evals benchmark --task 001-find-and-replace` (which would need both keys). This is the honest offline equivalent — same sandbox seam, same hidden-oracle injection, same `make_benchmark_task_fn` path.
- Task 006's `access.log` 404 counts: 10.0.0.1=6, 10.0.0.3=5 (both banned, ≥5); 10.0.0.2=3, 10.0.0.4=1 (not). 200/301 lines for other IPs make the status filter meaningful.
- Task 007 oracle deliberately avoids pytest (not guaranteed in the sandbox image) — imports the test module and calls the test fns directly.

### [Tester] 2026-07-14 02:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make pre-commit` → format-check, lint-check, 1617 unit tests, 0 warnings)
- Unit tests: 1617 passed / 0 failed (`make pre-commit`); `test_oracle_sanity.py` 16/16 passed in isolation
- Integration tests: `tests/integration/test_benchmark_docker.py` 1 passed (real docker); full `make ci` → 1730 passed, 2 skipped (key-gated live Opik/Gemini smokes only), 423.43s
- Warnings: 0

**E2E adversarial pass — attacking the oracles as a lazy/cheating agent**
- Happy path 001: independent re-run of `scratchpad/spot_run_001.py` (real docker, scripted model runs `sed -i 's/^timeout = 30$/timeout = 60/' config.ini` via the sandboxed bash tool, hidden `verify/` injected only at grade time) → `RESULT: PASS` (PASS)
- 001 wrong-solution (reformats an unrelated line: `host=localhost` no spaces, timeout correctly 60): `bash verify.sh` → `FAIL: config.ini differs from the expected file beyond the timeout value`, exit 1 (PASS — oracle correctly rejects)
- 002 wrong-solution ×3: unsorted list, duplicate entries, extra blank line between entries → all three `bash verify.sh` → FAIL via `diff -u` (PASS — oracle correctly rejects)
- 002 alt-correct-shape (missing trailing newline via `"\n".join(...)`, a very common Python idiom for "one per line") → `bash verify.sh` → **FAIL** (`\ No newline at end of file`), exit 1. Expected: PASS (this is a legitimate "one per line" solution). **FAIL** — see issue #1 below.
- 003 alt-correct-shape (different key order, compact single-line JSON) → PASS. 003 wrong-solution (all values stringified, e.g. `"age": "30"`) → FAIL (PASS — oracle correctly rejects both directions)
- 004 wrong-solution (TOC bullets without markdown links) → FAIL. 004 wrong-solution (4-space indent instead of spec'd 2-space) → FAIL. 004 alt-correct-shape (extra blank lines surrounding the TOC block) → PASS (PASS — oracle correctly scoped to the TOC block only)
- 005 wrong-solution (UTF-8 with a BOM prefix on cafe.txt) → `FAIL: cafe.txt still carries a UTF-8 byte-order mark`, exit 1 (PASS — oracle correctly rejects)
- 006 alt-correct-shape (regex-based re-implementation of `ban_ips.py`, different internals) → PASS. 006 wrong-solution (off-by-one threshold `> 5` instead of `>= 5`, drops 10.0.0.3 which has exactly 5) → FAIL (PASS — oracle correctly rejects)
- 006 wrong-solution (`ban_ips.py` prints `["10.0.0.1", "10.0.0.1", "10.0.0.3", "10.0.0.3"]` — duplicate IPs, violating the prompt's explicit "Each offending IP must appear exactly once in the array") → `bash verify.sh` → **PASS, exit 0**. Expected: FAIL. **FAIL** — see issue #2 below.
- 007 wrong-solution (cheats by editing the test's expected value instead of fixing `ranges.py`) → `FAIL: test_ranges.py was modified (checksum mismatch)`, exit 1 (PASS — sha256 guard correctly rejects). 007 alt-correct-shape (list comprehension `[i for i in range(1, n+2) if i <= n]` instead of `range(1, n+1)`) → PASS (PASS)

**Acceptance criteria**
- [x] PASS — Seven folders pass the loader contract and BOTH oracle-sanity directions in `make ci` — `uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -v` → 16 passed (7 tasks × 2 directions + the 001-greeting fixture); folder tree verified via `find evals/benchmark/tasks -maxdepth 3`.
- [x] PASS — verify.sh uses only bash/python/git/sqlite3 (sandbox-image-compatible) — `grep -nE '\b(curl|wget|pip|apt|npm|node|ruby|perl|sqlite3|git)\b' evals/benchmark/tasks/00*/verify/verify.sh` → clean on all 7; every `verify.sh` invoked via `bash verify.sh` (subprocess), consistent with `evals/harness/oracle_sanity.py::_run_verify`.
- [x] PASS — No prompt mentions verify assets; no verify asset lives under `setup/` — `grep -rniE 'verify|oracle|expected\.' evals/benchmark/tasks/00*/task.yaml` → no hits; `find evals/benchmark/tasks/00*/setup -type f` → only `README.md, access.log, config.ini, contacts.txt, people.csv, ranges.py, setup.sh, test_ranges.py`.
- [x] PASS — Spot-run at least one task through `python -m evals benchmark --task 001` (docker) — re-ran the docker + scripted-model path myself (real docker, real sandboxed bash edit via `sed`) → `RESULT: PASS`; matches the SWE's own honest-offline-equivalent explanation for why the literal keyed CLI form isn't used.
- [x] PASS — `make ci` green — re-ran independently: `1730 passed, 2 skipped in 423.43s (0:07:03)`, skips are the key-gated live Opik/Gemini smokes only. Matches SWE's report.

**Evidence**
```
$ uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -v
... 16 passed in 1.51s

$ uv run python scratchpad/spot_run_001.py   (re-run by Tester, real docker + sed edit)
agent_error: None
infra_error: None
tool_calls: ['bash']
verify.exit_code: 0
verify.stdout: PASS
RESULT: PASS

$ make ci
... 1730 passed, 2 skipped in 423.43s (0:07:03)

# 006 duplicate-IP break path (should FAIL, actually PASSes):
$ cat ban_ips.py
import json
print(json.dumps(["10.0.0.1", "10.0.0.1", "10.0.0.3", "10.0.0.3"]))
$ bash verify.sh
PASS
$ echo $?
0

# 002 missing-trailing-newline break path (should PASS a valid "one per line" file, actually FAILs):
$ printf "alice@example.com\nbilling@corp.net\nbob@work.io\ncarol@example.com\ndave@example.com" > emails.txt
$ bash verify.sh
--- expected.txt
+++ emails.txt
@@ -2,4 +2,4 @@
...
-dave@example.com
+dave@example.com
\ No newline at end of file
FAIL: emails.txt is not the expected unique, sorted email list
$ echo $?
1
```

**Other issues found**
1. **002-regex-extraction: undocumented trailing-newline strictness rejects a common valid solution shape.** The oracle's `diff -u expected.txt emails.txt` requires the last line to end with `\n`; a solution that writes `"\n".join(sorted(unique_emails))` — an idiomatic, fully spec-compliant "one per line" implementation — fails with "\ No newline at end of file". The task instructions for this QA pass explicitly called out that the "trailing newline tolerance decision is deliberate + documented either way" — I grepped `evals/benchmark/tasks/002-regex-extraction/` and `tasks/108-benchmark-tasks-easy.md` for `newline`/`trailing` and found **no documentation of this decision anywhere**, despite the SWE's implementation log claiming it was handled. Fix: either normalize trailing whitespace before the diff (e.g. `diff -u <(cat expected.txt) <(cat emails.txt; echo)` or strip a single trailing newline from both sides before comparing), or explicitly document in a `verify.sh` comment that a trailing newline is required and why (and accept the small false-negative risk deliberately).
2. **006-log-forensics: oracle does not enforce the prompt's own "exactly once" contract.** `verify.sh` does `set(got) == expected` — pure set equality — so an agent script that emits `["10.0.0.1", "10.0.0.1", "10.0.0.3", "10.0.0.3"]` (or any duplicate-laden list) still grades PASS, even though the prompt explicitly says "Each offending IP must appear exactly once in the array." A sloppy/cheating `ban_ips.py` (e.g. one that double-counts or forgets to dedupe) would score full credit it doesn't deserve. Fix: add `len(got) == len(set(got))` (or equivalent dedup check) to the oracle alongside the existing set-equality check, and add an oracle-sanity-style negative fixture for it if the harness supports one.
- Both issues above are genuine gameable gaps in oracle honesty — exactly the adversarial surface ADR-0017 §5 exists to guard ("a hidden verify.sh ... injecting ... proves the oracle honest ... so a broken oracle can't silently grade everything up or down"). They don't crash or corrupt anything, but they let a non-compliant agent solution pass, which is the benchmark's entire reason to exist.
- No security regressions, no secrets in the new task assets (`grep -rniE "api[_-]?key|secret|token|password"` over all 7 folders → only a fake `/secret` URL path in the seeded access.log, not a credential).
- `git status` / `git diff --stat` confirm no unrelated files touched — only `tasks/108-benchmark-tasks-easy.md` (checkbox/log updates) and the 7 new `evals/benchmark/tasks/00N-*/` folders.
- `code-review` plugin: not invoked — this work is uncommitted on a local branch with no open PR (`gh pr list` → empty), and the plugin's `/code-review` command operates on `gh pr` diffs only; not applicable until a PR exists.

**VERDICT: FAIL**

Two concrete oracle-honesty gaps found in the e2e adversarial pass (002 undocumented trailing-newline over-strictness rejecting a valid solution shape; 006 missing duplicate-IP check letting a non-compliant solution pass). Everything else — loader contract, verify.sh tool restriction, prompt hygiene, docker spot-run, `make ci` — verified green. SWE: please fix both and add a regression case to `test_oracle_sanity.py` (or an oracle-sanity-adjacent negative fixture) for the 006 duplicate-IP case; for 002 either relax the newline check or add the documented rationale.

### [SWE] 2026-07-14 10:35 — Fixes (QA round 1)

Both oracle-honesty gaps fixed; regression repros added.

**Files modified**
- `evals/benchmark/tasks/002-regex-extraction/verify/verify.sh` — replaced strict `diff -u` with a
  python line-sequence compare (`read().splitlines()` both sides). Grades content + ordering +
  uniqueness + one-per-line, but a missing/extra final newline is now tolerated — a valid
  `"\n".join(sorted(emails))` (no trailing newline) PASSes. Chose tolerance over documenting a
  byte-cosmetic requirement, per the Tester's preference and "oracle should grade content".
- `evals/benchmark/tasks/006-log-forensics/verify/verify.sh` — added a `len(got) == len(set(got))`
  duplicate check (distinct FAIL message) BEFORE the order-free set-equality check, so a script
  printing an IP twice fails even though its set is right. Header comment updated to say each IP must
  appear once.
- `tests/unit/evals/benchmark/test_oracle_edge_cases.py` (new) — pins both fixes with the Tester's
  exact wrong/alt solutions: 002 PASSes a no-trailing-newline `"\n".join(...)` answer; 006 FAILs a
  `["10.0.0.1","10.0.0.1","10.0.0.3"]` duplicate-IP script. Reuses the loader's `setup_dir` /
  `verify_script.parent` to reproduce the grade-time Workspace host-side (same seam as
  `oracle_sanity.run_oracle`).

**Tests**
- `tests/unit/evals/benchmark/` → 18 passing (16 two-direction sanity across the fixture + 7 tasks,
  now with the fixed 002/006 oracles, + 2 new edge-case repros). The standard oracle-sanity sweep
  still proves both 002 and 006 PASS-on-solution / FAIL-on-untouched-setup.
- Full unit gate (`make pre-commit`): 1619 passing (was 1617; +2 edge cases). format-check +
  lint-check clean.

**Acceptance criteria** — unchanged, still all met; the two touched oracles are now honest in the
adversarial directions the Tester exercised.

**Evidence**
```
$ uv run pytest tests/unit/evals/benchmark/ -q
..................                                                       [100%]
18 passed in 1.15s

$ make pre-commit
... 1619 passed in 96.80s (0:01:36)
```

**Notes**
- Did NOT re-run the full `make ci` (7-min integration sweep) for this round — the fixes are confined
  to two `verify.sh` graders (bash+python, not in `testpaths`) and one new unit test; the affected
  surface is fully covered by the 18 benchmark unit tests above, and no runner/harness code changed.
  Flagging so the Tester can decide if a full `make ci` re-run is wanted before PASS.
- The 002 fix keeps `expected.txt` WITH a trailing newline (unchanged); `splitlines()` normalizes both
  sides, so the committed gold `solution/emails.txt` still PASSes too.

### [Tester] 2026-07-14 02:20 — QA re-review (round 2)

**Test summary**
- Format / lint / pre-commit: PASS — `make pre-commit` → format-check, lint-check, 1619 unit tests, 0 warnings (was 1617 + 2 new = 1619, matches SWE's claim)
- Unit tests: 1619 passed / 0 failed; `tests/unit/evals/benchmark/` (oracle-sanity + new edge-case file) → 18 passed
- Integration tests: not re-run this round — see "make ci decision" below
- Warnings: 0

**Changed files reviewed**
- `evals/benchmark/tasks/002-regex-extraction/verify/verify.sh` — now compares `open(...).read().splitlines()` (line-sequence) instead of `diff -u`, so a missing/extra trailing newline is content-neutral.
- `evals/benchmark/tasks/006-log-forensics/verify/verify.sh` — adds `len(got) != len(set(got))` duplicate guard, placed AFTER the `isinstance(got, list)` check and BEFORE the `set(got) != expected` check (correct ordering — a non-list return can't reach `set()` and crash).
- `tests/unit/evals/benchmark/test_oracle_edge_cases.py` (new) — pins both round-1 repros as permanent regression tests via a shared `_grade_workspace` helper that seeds `setup/`, overlays an arbitrary answer, injects `verify/`, and runs `bash verify.sh`.

**Re-verification of my exact round-1 repros against the fixed oracles**
- 002 — my round-1 repro (`emails.txt` written via `printf` with NO trailing newline, otherwise correct content/order/uniqueness): `bash verify.sh` → `PASS`, exit 0. **Now fixed** (was FAIL in round 1).
- 002 — re-ran the three original wrong-solution attacks against the fixed oracle to confirm the fix didn't loosen anything: unsorted → FAIL exit 1; duplicates present → FAIL exit 1; extra blank line → FAIL exit 1 (all still correctly rejected).
- 006 — my round-1 repro (`ban_ips.py` printing `["10.0.0.1", "10.0.0.1", "10.0.0.3", "10.0.0.3"]`): `bash verify.sh` → `FAIL: ban_ips.py printed duplicate IPs (each must appear once): [...]`, exit 1. **Now fixed** (was PASS in round 1).
- 006 — re-ran a correct solution (fresh independent dedup-counting implementation) → PASS exit 0; re-ran the off-by-one-threshold wrong-solution (`> 5` instead of `>= 5`) → FAIL exit 1 (both still correct — the new guard doesn't introduce a false negative on valid output or a false positive on other wrong shapes).
- `uv run pytest tests/unit/evals/benchmark/test_oracle_edge_cases.py -v` → 2 passed (SWE's own regression pins, independently re-run).
- `uv run pytest tests/unit/evals/benchmark/test_oracle_sanity.py -v` → 16 passed — both directions still honest for all 7 tasks + the 001-greeting fixture after the 002/006 grader rewrites.

**`make ci` decision**
Not re-run in full this round. Rationale: the diff is confined to two `verify.sh` oracle scripts (data assets invoked only via `subprocess.run(["bash", ...])` inside pytest, never collected as Python) plus one new unit test file — no `evals/harness/*.py` runner/loader/sandbox code changed, and nothing under `src/decode` touched. `verify.sh` sits outside `testpaths` (`pyproject.toml:94` → `["tests/unit", "tests/integration"]`) so pytest never imports it directly; the only way its behavior is exercised is exactly what I re-ran by hand (`bash verify.sh` in prepared workspaces) plus the unit sweep (`test_oracle_sanity.py` + the new `test_oracle_edge_cases.py`), both green. Round 1 already proved `make ci` green (1730 passed, 2 skipped, 423.43s) with everything else in this diff identical. Re-running the full ~7-minute docker+network suite would add no incremental signal for a fix this narrow. Agree with the coordinator's take — full CI is optional here, not required for PASS.

**Acceptance criteria**
- [x] PASS — Seven folders pass the loader contract and BOTH oracle-sanity directions in `make ci` — `test_oracle_sanity.py` 16/16, re-confirmed after the 002/006 rewrites.
- [x] PASS — verify.sh uses only bash/python — re-checked both changed files, still clean (no curl/wget/pip/apt/npm/node/ruby/perl/sqlite3/git).
- [x] PASS — No prompt mentions verify assets; no verify asset lives under `setup/` — unchanged from round 1, prompts untouched by this fix.
- [x] PASS — Spot-run at least one task through docker — unaffected by this fix (001 untouched); verified in round 1, not re-run (out of scope for this narrow change).
- [x] PASS — `make ci` green — verified green in round 1 (1730 passed, 2 skipped); not re-run in full this round per the rationale above, but the unit sweep covering the actual changed surface is green (1619 passed, 0 warnings).

**Evidence**
```
$ uv run pytest tests/unit/evals/benchmark/ -v
... 18 passed in 1.73s

$ make pre-commit
... 1619 passed in 94.74s (0:01:34)

# 002 fixed — my round-1 repro now PASSes:
$ printf "alice@example.com\nbilling@corp.net\nbob@work.io\ncarol@example.com\ndave@example.com" > emails.txt
$ bash verify.sh
PASS
$ echo $?
0

# 006 fixed — my round-1 repro now FAILs:
$ cat ban_ips.py
import json
print(json.dumps(["10.0.0.1", "10.0.0.1", "10.0.0.3", "10.0.0.3"]))
$ bash verify.sh
FAIL: ban_ips.py printed duplicate IPs (each must appear once): ['10.0.0.1', '10.0.0.1', '10.0.0.3', '10.0.0.3']
$ echo $?
1
```

**Other issues found**
- None new. Both round-1 issues are resolved with regression coverage; no other regressions introduced (re-ran the untouched wrong-solution attacks for both tasks to confirm no loosening).
- `git status` confirms scope: `tasks/108-benchmark-tasks-easy.md` + the 7 task folders (unchanged since round 1, only 2 `verify.sh` files inside them differ) + the one new test file. No unrelated files.

**VERDICT: PASS**

Both oracle-honesty gaps from round 1 are fixed and pinned with regression tests; I independently re-ran my exact repros against the fixed oracles and they behave correctly in both directions, with no loosening on the previously-passing wrong-solution attacks. Hand off to PA for acceptance review.
