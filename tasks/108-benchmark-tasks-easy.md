---
id: 108
feature: evals
status: pending
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

- [ ] Seven folders pass the loader contract and BOTH oracle-sanity directions in `make ci`.
- [ ] verify.sh uses only bash/python/git/sqlite3 (sandbox-image-compatible).
- [ ] No prompt mentions verify assets; no verify asset lives under `setup/`.
- [ ] Spot-run at least one task through `python -m evals benchmark --task 001` (docker) — PASS or
      an honest FAIL recorded in the log.
- [ ] `make ci` green.

## Out of scope

- Tasks 008–020 (109, 110). Runner changes.

## Log
