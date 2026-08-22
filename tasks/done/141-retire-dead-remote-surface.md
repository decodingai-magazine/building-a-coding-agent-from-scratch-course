---
id: 141
feature: modal-remote-headless
status: done
---

# Retire the dead GCP/ZenML remote surface (absorbs task 140) — Makefile targets, deploy.sh, demo script, flow.Dockerfile, `remote` dep group, exec_id docstring

Tags: `infra`, `refactor`, `docs`
Depends on: None (absorbs `tasks/140-retire-dead-remote-stack-surface.md`, marked done-by-absorption at grooming)
Blocks: 146 (docs task assumes the dead surface is gone)

First task of the modal-remote-headless feature (ADR-0020 §6): clear the ground the Modal
successor builds on. Absorbs the FULL scope of task 140 (pending, PA-groomed) and extends it
with the feature-level deletions the human approved: the N-attempts demo script (its successor
lands in task 143), the flow Dockerfile, and the GCP-only `remote` dependency group.

## Scope

**From task 140, verbatim:**

- Delete `make deploy` + `scripts/deploy.sh` (and the `deploy` entry in `.PHONY` / `make help`).
  No stubs, no attic copy. Add one line to 07_infra.md's stale-marked GCP appendix noting the
  script was removed (the full appendix rework is task 146's).
- Delete `make run-remote` (bias-to-least: its replacement is the Modal Headless App, task 142;
  until then the docs may briefly point nowhere new — acceptable for one task's window, 146
  closes it).
- Fix the `src/decode/observability/tracing.py:147` docstring: "Kitaru exec_id for a run" → the
  run's per-run session id (ADR-0019 §1; glossary "Thread (Opik)").
- Update any `running_the_code/` or `AGENTS.md` line that still names `make deploy` /
  `make run-remote` as live verbs (minimal edits only; the 07_infra rewrite is task 146).

**New in this feature:**

- Delete `scripts/demo-multiple-attempts.sh` entirely (not just its comments — supersedes 140's
  "sweep comments" line). Its successor is task 143's spawn helper.
- Delete `docker/flow.Dockerfile`; remove the `docker/` directory if that leaves it empty.
- Delete the `remote` group from `[dependency-groups]` in `pyproject.toml` (all GCP/ZenML
  submit-side deps: gcsfs, kfp, kubernetes, google-cloud-*) and re-lock. Verified at grooming:
  its only consumers were `make run-remote`, `scripts/deploy.sh`, and
  `scripts/demo-multiple-attempts.sh` — all deleted here. Re-verify with grep before removing.
- Flip `tasks/140-retire-dead-remote-stack-surface.md` to `status: done` with a log entry
  naming this task, if the grooming commit has not already done so.

## Acceptance Criteria

- [x] `make deploy` and `make run-remote` are no longer Makefile targets; `make help` lists neither; `.PHONY` matches.
- [x] `scripts/deploy.sh`, `scripts/demo-multiple-attempts.sh`, and `docker/flow.Dockerfile` no longer exist; `docker/` is gone if empty.
- [x] `grep -rn "kitaru_bootstrap_api_key" .` (excluding `tasks/`, `docs/adr/`, `.git`) returns nothing. — Tester-ruled acceptable-as-documented: 2 hits remain (`running_the_code/07_infra.md:38`, `kitaru_plan.md:146`), both inert historical prose, neither a live instruction; see Tester log for the full ruling.
- [x] `grep -rn "exec_id" src/` returns nothing.
- [x] `grep -rn "deploy.sh\|run-remote\|demo-multiple-attempts\|flow.Dockerfile\|KITARU_STACK\|--group remote" Makefile scripts/ src/ pyproject.toml AGENTS.md` returns nothing; `running_the_code/` may keep historical prose mentions only in 07_infra's appendix pending task 146.
- [x] `[dependency-groups]` has no `remote` entry; `uv lock` regenerated; `uv lock --check` green.
- [x] `running_the_code/07_infra.md` appendix carries the one-line removal note.
- [x] `tasks/140-...md` is `status: done` with an absorption log entry referencing this task.
- [x] Full unit suite green; `make ci` green.

## User Stories

### Story: Course reader explores the Makefile and never hits a dead target
1. Reader runs `make help`
2. Every listed target works: no `deploy` pointing at a script that calls a deleted file, no
   `run-remote` submitting a flow that no longer exists
3. `uv sync` after a fresh clone installs no GCP/ZenML submit stack — the lock is lighter and
   `uv tree` shows no `kfp`/`gcsfs`/`kubernetes`

### Story: Operator greps for the headless thread id and finds one truth
1. Operator reads `src/decode/observability/tracing.py` to understand Opik thread grouping
2. The docstring says the `decode run` thread key is the run's session id — matching
   `runtime/headless.py`, the glossary, and 03_runtime.md
3. No surface anywhere in `src/` still names the dead `exec_id` concept

## Out of scope

- The new Modal story in 07_infra.md (task 146) and the attempts successor (task 143).
- Any change to `src/decode/sandbox/` — the ModalBackend and its `decode-sandbox-<env>` apps
  are live and untouched.

---

Refs: `tasks/140-retire-dead-remote-stack-surface.md` (absorbed), ADR-0019, ADR-0020 §6

## Log

### [SWE] 2026-08-22 14:20 — Implementation

Pure deletion task (ADR-0020 §6) — no new behavior, so no red/green TDD: the contract is
"these surfaces no longer exist", proven by the AC greps + the full suite staying green.

**Files modified**
- `Makefile` — deleted the `deploy` and `run-remote` targets; `.PHONY` now reads
  `… sync-secrets build install-cli uninstall-cli ci help` (both names dropped).
- `scripts/deploy.sh` — DELETED (`git rm`; 540 lines of GCP/ZenML provisioning, incl. the
  line-333 call into the already-deleted `kitaru_bootstrap_api_key.py`).
- `scripts/demo-multiple-attempts.sh` — DELETED (`git rm`; successor is task 143's spawn helper).
- `docker/flow.Dockerfile` — DELETED (`git rm`); `docker/` was left empty and is therefore gone.
- `pyproject.toml` — removed the `remote` entry from `[dependency-groups]` (14 GCP/ZenML deps).
- `uv.lock` — re-locked; **pure deletion, 619 lines, zero version bumps** for any surviving
  package (`git diff -U0 uv.lock | grep '^+'` → empty), so the `grpcio`/`urllib3` pins that
  only existed to constrain the GCP stack dropped out without moving anything else.
- `src/decode/observability/tracing.py` — `root_span` docstring: dead "Kitaru exec_id for a run"
  → "the decode session id: the REPL's, or the fresh per-run id `decode run` mints (ADR-0019 §1)",
  matching `runtime/headless.py:47` and the glossary's **Thread (Opik)** row verbatim.
- `running_the_code/07_infra.md` — one removal note appended to the stale-appendix banner,
  naming ADR-0020 §6 and every removed surface, with "git history is the archive".
- `.dockerignore` — header comment no longer names the deleted `docker/flow.Dockerfile`
  (see Notes: file itself kept).
- `tasks/141-…md` — `status: in-progress`, AC boxes updated.

**Tests**
- Unit: 2256 passing, 0 failing (`make pre-commit`).
- Integration: full `make ci` green — 2368 passed in 476.74s, zero new failures vs HEAD.
- No test touched: `grep -rn "deploy.sh\|run-remote\|demo-multiple\|flow.Dockerfile\|group remote\|KITARU_STACK" tests/ evals/ .github/ .pre-commit-config.yaml` → no hits before the deletions, so nothing referenced the removed surface.

**Acceptance criteria**
- [x] `make deploy` / `make run-remote` gone — `make deploy` → `No rule to make target 'deploy'` (exit 2), same for `run-remote`; `make help` lists 16 targets, neither among them; `.PHONY` matches.
- [x] `scripts/deploy.sh`, `scripts/demo-multiple-attempts.sh`, `docker/flow.Dockerfile` gone; `docker/` removed — `ls` → "No such file or directory" for all three.
- [ ] `grep -rn "kitaru_bootstrap_api_key" .` — 2 hits remain, both historical prose, both deliberate (see Notes). No live reference anywhere.
- [x] `grep -rn "exec_id" src/` → no hits.
- [x] `grep -rn "deploy.sh\|run-remote\|demo-multiple-attempts\|flow.Dockerfile\|KITARU_STACK\|--group remote" Makefile scripts/ src/ pyproject.toml AGENTS.md` → no hits.
- [x] no `remote` in `[dependency-groups]`; `uv lock` regenerated; `uv lock --check` green.
- [x] 07_infra appendix carries the removal note.
- [x] `tasks/done/140-…md` already `status: done` with the PA's "Done-by-absorption" entry naming task 141 — verified at grooming, not redone.
- [x] Full unit suite + `make ci` green.

**Evidence**
```
$ make help
  install              Install/refresh the venv from pyproject + uv.lock, and wire git hooks.
  test / unit-tests / integration-tests / lint-* / format-* / pre-commit
  eval-benchmark / eval-regression / sync-secrets / build / install-cli / uninstall-cli / ci / help
  (16 targets — no deploy, no run-remote)

$ make deploy;  make run-remote
make: *** No rule to make target `deploy'.  Stop.        (exit 2)
make: *** No rule to make target `run-remote'.  Stop.

$ ls scripts/deploy.sh scripts/demo-multiple-attempts.sh docker/
ls: docker/: No such file or directory
ls: scripts/demo-multiple-attempts.sh: No such file or directory
ls: scripts/deploy.sh: No such file or directory

$ grep -rn "exec_id" src/                     # → no output
$ grep -rn "deploy.sh\|run-remote\|demo-multiple-attempts\|flow.Dockerfile\|KITARU_STACK\|--group remote" \
    Makefile scripts/ src/ pyproject.toml AGENTS.md      # → no output

$ uv sync && uv tree | grep -Ei "kfp|gcsfs|kubernetes|google-cloud"
Resolved 145 packages in 4ms / Checked 142 packages in 21ms
(no output — the GCP submit stack is gone from a fresh sync)
$ grep -c 'name = "kfp"\|name = "gcsfs"\|name = "kubernetes"' uv.lock
0
$ uv lock --check
Resolved 145 packages in 2ms

$ git diff --stat uv.lock pyproject.toml
 pyproject.toml |  16 --
 uv.lock        | 619 ---------------------------------------------------------
 2 files changed, 635 deletions(-)

$ make pre-commit
============================ 2256 passed in 44.27s =============================

$ make ci
======================= 2368 passed in 476.74s (0:07:56) =======================

$ uv run decode --help        # e2e smoke after the dep-group removal
... Commands:  run  Run a single TASK headlessly, then print the agent's answer...   exit=0
$ uv run decode run --help
... --model ID / --repo URL-OR-PATH / --local / --help                               exit=0
```

**Notes**
- **AC 3 left unchecked, deliberately — 2 surviving `kitaru_bootstrap_api_key` hits, neither a
  live instruction.** (1) `running_the_code/07_infra.md:38`, inside the stale-appendix banner,
  in the sentence that *states the script is deleted* — AC 5 of this same task explicitly blesses
  historical prose in that appendix pending task 146, so the two criteria read against each other
  here; I kept the honest history. (2) `kitaru_plan.md:146`, the tracked kitaru-migration plan
  doc, which lists this very deletion as planned work — same class of artifact as `tasks/`
  (a record of intent), and rewriting or deleting it is outside this task's scope. Both are
  one-line fixes if the PA/Tester prefers the literal grep; say the word and I'll make it.
- **`.dockerignore` kept, comment fixed.** Its only consumer was the deleted `docker/flow.Dockerfile`
  (`grep -rn dockerignore .` → no other hits), so it is arguably dead too — but task 142 builds the
  Modal image in-code and Modal's `add_local_dir(ignore=FilePatternMatcher.from_file(".dockerignore"))`
  idiom can reuse it. Bias-to-least: leave the file, drop the dangling filename from its header.
  If 142 doesn't adopt it, it's a one-line follow-up deletion.
- **Adjacent, out of this task's scope (AC says `src/`):** `evals/README.md:74` and
  `evals/harness/online.py:5` still describe the Opik thread key as "Kitaru `exec_id`" — same
  dead concept, same one-line fix, but outside `src/` and outside the named scope. Worth a
  rollup line in task 146 or its own trivial task; not fixed here to keep the diff to scope.
- **No stubs, no attic copies** — every removal is a `git rm`; git history is the archive, exactly
  as ADR-0020 §2 and the task specify.
- `AGENTS.md` needed **no** edit: it never named `make deploy` / `make run-remote` as live verbs
  (its Makefile line already lists only install · test · lint · format · pre-commit · build · ci).
- Deliberately untouched per Out of scope: `src/decode/sandbox/` (ModalBackend is live), the
  07_infra GCP appendix body and its `make run-remote` prose (task 146's rework), ADR-0008/0010
  historical `exec_id` text.

### [Tester] 2026-08-22 15:00 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` → 307 files already formatted; `make
  lint-check` → all checks passed; `make pre-commit` → 2256 passed, 0 failed, 0 warnings)
- Unit tests: 2256 passed / 0 failed
- Integration tests: 112 passed / 0 failed (see Notes on one transient flake, re-run clean)
- Warnings: 0

**E2E adversarial pass**
- Happy path: fresh `rm -rf .venv && uv sync` (clean re-clone simulation) → resolves 145
  packages, no GCP/ZenML deps; `uv run decode --help` and `uv run decode run --help` both print
  full, correct help text, exit 0 (PASS).
- Break path 1 (dead-target muscle memory): `make deploy` → `make: *** No rule to make target
  'deploy'. Stop.` exit 2; `make run-remote` → same pattern for `run-remote`, exit 2. Friendly,
  standard Make failure, no partial execution, no leaked script invocation (PASS).
- Break path 2 (dead dep-group muscle memory): `uv run --group remote decode run "test"` →
  `error: Group `remote` is not defined in the project's `dependency-groups` table`, exit 2 — no
  traceback, no accidental fallback to installing anything (PASS).
- Break path 3 (missing required input on the surviving `decode run`): `uv run decode run` (no
  TASK, no Kitaru inputs) → one friendly `Decode:` stderr line, exit 1, no traceback (PASS —
  confirms the deletion didn't collaterally break the live `decode run` command it sits next to).
- Break path 4 (fresh-clone dependency graph): `uv tree | grep -Ei "kfp|gcsfs|kubernetes|google-
  cloud"` → no output after a clean `uv sync`; `grep -c 'name = "kfp"...' uv.lock` → 0 (PASS —
  the GCP/ZenML stack is verifiably gone from a cold install, not just "not imported").

**Acceptance criteria**
- [x] PASS — `make deploy`/`make run-remote` gone from Makefile/`.PHONY`/`make help` —
      `make help` lists 16 targets, neither present; `.PHONY` line has no `deploy`/`run-remote`;
      `make deploy`/`make run-remote` both exit 2 with "No rule to make target".
- [x] PASS — `scripts/deploy.sh`, `scripts/demo-multiple-attempts.sh`, `docker/flow.Dockerfile`
      gone, `docker/` removed — `ls` on all four paths → "No such file or directory".
- [x] PASS (ruled acceptable-as-documented) — `kitaru_bootstrap_api_key` grep, excluding
      `tasks/`, `docs/adr/`, `.git` — re-ran independently: 2 hits remain,
      `running_the_code/07_infra.md:38` and `kitaru_plan.md:146`. Ruling on the SWE's flagged
      judgment call: **both acceptable, checkbox checked.** (1) `07_infra.md:38` sits inside the
      appendix's pre-existing, untouched-by-this-diff text (`git diff running_the_code/07_infra.md`
      shows only 5 added lines, the new "Removed in ADR-0020 §6" note — line 38 predates this
      task) — the entire rest of the file past the `## Appendix` heading (lines 30-511+, sections
      1-5) is the same stale appendix this task's own AC5 explicitly blesses as keeping historical
      `make run-remote`/script mentions pending task 146's full rewrite; AC3's exclusion list
      (`tasks/`, `docs/adr/`) simply forgot to also name the appendix it explicitly permits
      elsewhere in the same task — a drafting oversight, not a live-reference bug. (2)
      `kitaru_plan.md:146` is a root-level tracked planning scratch-doc, already independently
      flagged as stale by a prior PR Reviewer entry (`tasks/done/138-...md`: "kitaru_plan.md:3
      still reads 'not yet implemented' — delete or archive") — i.e. the whole file is already
      queued for archival/deletion as its own nit, and editing one line of it here would be
      duplicate, soon-overwritten work outside this task's named scope (Makefile/scripts/docker/
      pyproject/tracing.py/07_infra appendix note). No live command, doc instruction, or code path
      anywhere in the repo still names `kitaru_bootstrap_api_key.py` as something to run.
- [x] PASS — `grep -rn "exec_id" src/` → no output.
- [x] PASS — `grep -rn "deploy.sh\|run-remote\|demo-multiple-attempts\|flow.Dockerfile\|
      KITARU_STACK\|--group remote" Makefile scripts/ src/ pyproject.toml AGENTS.md` → no output;
      `running_the_code/07_infra.md`'s only such mentions are inside the pre-existing stale
      appendix (lines 30-511), consistent with the AC's carve-out.
- [x] PASS — `[dependency-groups]` has no `remote` entry (`git diff pyproject.toml` shows a clean
      16-line deletion, nothing else touched); `uv lock --check` → "Resolved 145 packages" clean;
      re-verified with a fresh `rm -rf .venv && uv sync` — 0 GCP/ZenML packages installed.
- [x] PASS — `running_the_code/07_infra.md` appendix carries the removal note — read the diff:
      4-line "Removed in ADR-0020 §6" paragraph naming every deleted surface, "git history is the
      archive."
- [x] PASS — `tasks/done/140-...md` is `status: done` with a "Done-by-absorption" log entry
      naming `tasks/141-...md` verbatim.
- [x] PASS — Full unit suite green; `make ci` green — reproduced independently (see Evidence);
      one integration flake investigated and ruled non-regression (see Notes).

**Evidence**
```
$ make pre-commit
============================ 2256 passed in 39.96s =============================

$ make ci   (first independent run)
1 failed, 2367 passed in 491.52s — FAILED tests/integration/test_lsp_capstone.py::
  test_lsp_capstone_real_ty_wire — "could not spawn 'ty' ... No such file or directory"

$ uv run pytest tests/integration/test_lsp_capstone.py::test_lsp_capstone_real_ty_wire -v
tests/integration/test_lsp_capstone.py::test_lsp_capstone_real_ty_wire PASSED [100%]
1 passed in 0.90s

$ uv run pytest tests/integration -q   (full clean re-run)
112 passed in 456.25s (0:07:36)

$ rm -rf .venv && uv sync && uv tree | grep -Ei "kfp|gcsfs|kubernetes|google-cloud"
Resolved 145 packages in 4ms
(no output)

$ uv run decode run
Decode: decode run needs a TASK to run: ...            (exit 1, friendly, no traceback)

$ make deploy; make run-remote
make: *** No rule to make target `deploy'.  Stop.       (exit 2)
make: *** No rule to make target `run-remote'.  Stop.

$ uv run --group remote decode run "test"
error: Group `remote` is not defined in the project's `dependency-groups` table   (exit 2)
```

**Other issues found**
- **Non-blocking, unrelated diff already present in working tree**: `tasks/done/138-docs-and-
  agents-md-alignment.md` shows as modified (+15 lines, a PR Reviewer log entry for a prior
  feature rollup) but is untouched by task 141's own diff — confirmed via `git diff --stat HEAD`
  that the SWE's actual change-set for 141 is exactly the 10 files named in their log. Flagging
  for hygiene only; not a task-141 defect, and not blocking this verdict.
- **Adjacent, out-of-scope "Kitaru exec_id" prose** (SWE's judgment call 3): confirmed
  `evals/README.md:74` and `evals/harness/online.py:5` still describe the Opik thread key as
  "Kitaru `exec_id`". Agreed this is legitimately outside `src/` and outside this task's named
  scope. Added a follow-up note to `tasks/146-docs-remote-story-on-modal.md`'s Log so it isn't
  lost.
- **`.dockerignore` kept, comment fixed** (SWE's judgment call 2): reasonable, low-risk,
  reversible; PASS with note. If task 142 doesn't end up reusing it via
  `FilePatternMatcher.from_file`, it's a trivial follow-up deletion — not blocking here.
- The one integration-suite failure (`test_lsp_capstone_real_ty_wire`) is a transient spawn-
  under-load flake unrelated to this task's diff (zero LSP files touched; `ty` binary present at
  `.venv/bin/ty` and resolves fine via `uv run which ty`); passed both in isolation and in a full
  clean re-run of the integration suite. Consistent with prior documented flakes in this repo's
  docker/subprocess-heavy integration suite (e.g. `tasks/done/119`, `tasks/done/103`).

**VERDICT: PASS**
