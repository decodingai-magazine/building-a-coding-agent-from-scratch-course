---
id: 137
feature: kitaru-replay-runtime
status: pending
---

# Agent version 2: replay context replication + worker replay verification

Tags: `infra`, `runtime`
Depends on: 134, 136
Blocks: —

This task implements ADR-0019 (§ Replay context). Replayed tool calls must land in a faithful,
ISOLATED clone of the original context — never the operator's host tree. Agent version 2's
run spec re-creates decode's context: `decode run` under `SANDBOX_MODE=docker` with a
repo-clone Workspace of this repo.

## Scope

- Register **agent version 2** for the workspace's `decode` agent
  (`https://f5ee9622-kitaru.cloudinfra.zenml.io`, CLI already authenticated) with a REAL run
  spec: command `decode run` (no inline prompt — task arrives via 136's input contract,
  `{"task": ..., "model": ...}`), env `SANDBOX_MODE=docker` + `SANDBOX_REPO=<this repo>`
  (or `--repo`), and provider credentials attached via Kitaru's secret mechanism
  (`--secret-id` / version-attached secrets) so the worker subprocess can call the LLM.
  Reuse the existing `decode` agent registration — never create a duplicate agent.
- Make the registration REPRODUCIBLE: either a small operator script
  (`scripts/register_kitaru_agent.py`, click + new client/CLI) or an exact documented CLI
  sequence in `running_the_code/03_runtime.md` — whichever is less code; the SWE picks after
  reading `kitaru agent version register --help` and the record-in-production doc
  (https://docs.zenml.io/kitaru/adapters/record-in-production.md).
- Restart the worker with provider credentials available, then run a **baseline replay**
  (no overrides) of cohort `decode-bad-request-400@1`, session
  `01a02529-ebf7-7133-869f-ea3f4a7bc493`, targeting agent version 2.
- "Executes" means: the worker claims the task, spawns `decode run`, and the replay reaches
  agent-level execution — it completes, or fails with an AGENT-level error. A spawn/import/
  config error (ModuleNotFoundError, missing env, command-not-found) is a FAIL of this task.
- Automatable slice: unit-test the registration script's spec-building (command, env, input
  schema match 136's contract) without network.

## Acceptance Criteria

- [ ] Agent version 2 exists on the workspace with the documented run spec (command `decode run`, docker sandbox env, repo clone, attached secrets) — reproducible via the script/documented sequence.
- [ ] The run-spec input schema matches 136's contract exactly (`task` required, `model` optional); any deviation forced by the adapter is fed back into 136 via this task's log, not silently absorbed.
- [ ] [HUMAN] Worker restarted with provider credentials; `kitaru worker list` shows it healthy.
- [ ] [HUMAN] Feature gate "(d)": a baseline replay of cohort `decode-bad-request-400@1` (session `01a02529-ebf7-7133-869f-ea3f4a7bc493`) EXECUTES on the worker — replay completes or fails agent-level; the evidence (replay id + status + worker log excerpt) is pasted into this task's log.
- [ ] Replayed tool calls ran inside the docker Workspace clone — the operator's host tree is untouched (spot-check via worker/container logs).

## Out of scope

- Evaluators, experiments, CI gates over this cohort (future feature).
- What-if replays with overrides (model/prompt) — baseline only proves the pipe.
- Worker deployment automation (systemd/CI) — a manually started worker is enough here.

## Log
