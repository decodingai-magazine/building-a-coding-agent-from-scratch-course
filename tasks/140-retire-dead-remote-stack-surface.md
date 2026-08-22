---
id: 140
feature: kitaru-replay-runtime
status: pending
---

# Retire the dead remote-stack operator surface (Makefile deploy/run-remote, scripts/deploy.sh, last exec_id docstring)

Tags: `infra`, `docs`, `refactor`
Depends on: None (follow-up to 138 — done; the Tasks Plan declared `scripts/*.sh` out of scope)
Blocks: —

Post-acceptance follow-up to the kitaru-replay-runtime feature (PA-groomed at acceptance
review; NOT part of that feature's accepted gate). ADR-0019 deleted the self-hosted stack and
`running_the_code/07_infra.md` now opens with "there is no server to deploy any more", but
three operator-facing remnants still point at it — the worst is a live trap: `make deploy`
runs `scripts/deploy.sh`, which at line 333 calls the DELETED
`scripts/kitaru_bootstrap_api_key.py` and would crash mid-provision.

## Scope

- **Delete `make deploy` + `scripts/deploy.sh`** (and the `deploy` entry in `.PHONY` /
  `make help`). No stubs, no attic copy — 07_infra.md's stale-marked GCP appendix is the
  retained history, per ADR-0019's no-shims discipline. Add one line to that appendix noting
  the script was removed with ADR-0019.
- **Delete `make run-remote`** (bias-to-least: it is now just `DECODE_ENV=<env>
  SANDBOX_MODE=modal decode run --repo ... "<task>"`, which 03_runtime.md / 04_sandboxing.md
  already teach). Document that one-liner where `run-remote` was referenced in
  `running_the_code/07_infra.md`, if anywhere still points at the target. If the SWE finds
  `run-remote` carries real convenience value beyond the one-liner, keep-and-rewire is
  acceptable — but the default decision is delete.
- **Sweep the last dead-concept remnants in `src/` and `scripts/`:** fix the
  `src/decode/observability/tracing.py:147` docstring ("Kitaru exec_id for a run" → the run's
  per-run session id, per ADR-0019 §1 / the glossary's Thread (Opik) entry); sweep
  `scripts/demo-multiple-attempts.sh` comments naming `runtime/modal_app.py` /
  `run_agent_task` (delete or reword; the referenced modules are gone).
- Update any `running_the_code/` or `AGENTS.md` line that still names `make deploy` /
  `make run-remote` as live verbs (07_infra head table, Makefile help text in docs, if any).

## Acceptance Criteria

- [ ] `make deploy` and `make run-remote` are no longer Makefile targets; `make help` lists neither; `.PHONY` matches.
- [ ] `scripts/deploy.sh` no longer exists; `grep -rn "kitaru_bootstrap_api_key" .` (excluding `tasks/done/` and `docs/adr/`) returns nothing.
- [ ] `grep -rn "exec_id" src/` returns nothing.
- [ ] `grep -rn "deploy.sh\|run-remote" Makefile running_the_code/ AGENTS.md` returns no live-instruction hits (the 07_infra stale appendix's historical prose may mention the deletion).
- [ ] `running_the_code/07_infra.md` appendix carries the one-line removal note.
- [ ] Full unit suite green; `make ci` green (the Makefile edit must not break any wired target).

## User Stories

### Story: Course reader explores the Makefile and never hits a dead target
1. Reader runs `make help`
2. Every listed target works: no `deploy` pointing at a script that calls a deleted file, no `run-remote` duplicating what 03/04 runbooks teach
3. Reader looking for the old remote story lands in `07_infra.md`, whose head table names the real surface (managed workspace / Kitaru Worker / Agent Version / Modal / Opik) and whose stale appendix explains what was retired and why

### Story: Operator greps for the headless thread id and finds one truth
1. Operator reads `src/decode/observability/tracing.py` to understand Opik thread grouping
2. The docstring says the `decode run` thread key is the run's session id — matching `runtime/headless.py`, the glossary, and 03_runtime.md
3. No surface anywhere in `src/` still names the dead `exec_id` concept

---

Refs: `tasks/done/138-docs-and-agents-md-alignment.md` (SWE Notes "Out of scope, still stale"), ADR-0019

## Log

### [PA] 2026-08-22 — Grooming

**Summary**
Delete the last operator-facing remnants of the retired self-hosted stack that the 131-138
plan deliberately left out of scope: two Makefile targets (one a live trap), the deploy
script they drive, and the final `exec_id` docstring in `src/`.

**Key decisions**
- Delete over rewire, per ADR-0019's no-stubs discipline and bias-to-least; `run-remote`'s
  functionality survives as a documented one-liner.
- History lives in 07_infra.md's already-stale-marked appendix, not in kept-but-broken
  scripts.

**Dependencies**
- None — purely subtractive against surfaces no shipped task depends on.

**User stories**
- 2 stories covering: the course reader's `make help` walk and the operator's
  thread-id grep.

Ready for implementation.
