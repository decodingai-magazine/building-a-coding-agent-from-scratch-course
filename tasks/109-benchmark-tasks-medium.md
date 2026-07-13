---
id: 109
feature: evals
status: pending
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

- [ ] Seven folders pass loader + both oracle-sanity directions in `make ci`.
- [ ] 010/018-style git asserts run against `setup.sh`-built history (no nested committed `.git`).
- [ ] 014's hidden tests never touch the Workspace before grade time.
- [ ] Spot-run one task through the docker runner; result logged.
- [ ] `make ci` green.

## Out of scope

- Tasks 015–020 (110).

## Log
