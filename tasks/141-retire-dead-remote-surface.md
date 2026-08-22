---
id: 141
feature: modal-remote-headless
status: pending
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

- [ ] `make deploy` and `make run-remote` are no longer Makefile targets; `make help` lists neither; `.PHONY` matches.
- [ ] `scripts/deploy.sh`, `scripts/demo-multiple-attempts.sh`, and `docker/flow.Dockerfile` no longer exist; `docker/` is gone if empty.
- [ ] `grep -rn "kitaru_bootstrap_api_key" .` (excluding `tasks/`, `docs/adr/`, `.git`) returns nothing.
- [ ] `grep -rn "exec_id" src/` returns nothing.
- [ ] `grep -rn "deploy.sh\|run-remote\|demo-multiple-attempts\|flow.Dockerfile\|KITARU_STACK\|--group remote" Makefile scripts/ src/ pyproject.toml AGENTS.md` returns nothing; `running_the_code/` may keep historical prose mentions only in 07_infra's appendix pending task 146.
- [ ] `[dependency-groups]` has no `remote` entry; `uv lock` regenerated; `uv lock --check` green.
- [ ] `running_the_code/07_infra.md` appendix carries the one-line removal note.
- [ ] `tasks/140-...md` is `status: done` with an absorption log entry referencing this task.
- [ ] Full unit suite green; `make ci` green.

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
