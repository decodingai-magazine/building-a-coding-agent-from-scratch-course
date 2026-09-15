---
id: 170-remove-modal-hosted-kitaru-worker
feature: kitaru-local-workers
status: done
---

# Remove the Modal-hosted Kitaru Worker

## Scope
Kitaru Workers run only on the operator's machine (ADR-0023). Delete the Modal Worker app, its runbook
and its Agent Version; the managed workspace stays a `KITARU_API_URL` switch for where results go.

## Acceptance criteria
- [x] `scripts/modal_kitaru_worker.py`, `tests/unit/scripts/test_modal_kitaru_worker.py` and
      `running_the_code/07_evals_replays_deploy.md` are deleted.
- [x] `scripts/bootstrap_kitaru.py` registers one Agent Version (`SANDBOX_MODE=docker`); a fresh server names it `decode@1`.
- [x] `decode.remote.image` carries no Worker-only piece (`KITARU_BIN`, the `scripts` source layer).
- [x] No live doc (AGENTS.md, README, index.html, glossary, `.env.example`, runbooks, manual-e2e-qa skill)
      names the Modal Worker, `decode-kitaru-worker-<env>` or 07; ADRs and `tasks/done/` keep their history.
- [x] ADR-0023 supersedes ADR-0020 §5 and the worker half of §4; tasks/153 narrowed.
- [x] format / lint / pre-commit / unit tests green.

## Out of scope
- Unregistering the retired `none` Agent Version on servers that already carry it (operator action).
- The Modal Headless App and its recording into the managed workspace (unchanged).

## Log
### [SWE] 2026-09-15 — Implemented
Direct chat clean-up requested by the maintainer: "keep running Kitaru only locally; the only remote
version is pushing results to the managed control plane".
