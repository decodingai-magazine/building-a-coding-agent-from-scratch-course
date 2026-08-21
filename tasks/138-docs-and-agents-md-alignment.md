---
id: 138
feature: kitaru-replay-runtime
status: pending
---

# Docs alignment: AGENTS.md, running_the_code, retire dead future task

Tags: `docs`
Depends on: 131, 133, 137
Blocks: —

This task implements ADR-0019 (docs are authored in the grooming commit; this task aligns the
OPERATIVE docs the agents and humans actually load — AGENTS.md and runbooks — with the shipped
reality).

## Scope

- **AGENTS.md** (surgical, remove-over-add):
  - Header + Tech-Stack "Durability" row: `kitaru[local,pydantic-ai,llm]` / "Durable headless
    flow … checkpoints + replay … ADR-0008/0009" → kitaru 0.22.2 replay-based model:
    `kitaru[cli,mcp,worker]` + `kitaru-pydantic-ai` adapter, recording seam, ADR-0019; note
    the `pydantic-ai-slim >=2.22,<2.23` pin rationale.
  - "Kitaru replay & what-if (operator surface)" section: rewrite onto sessions/replays/
    workers + the installed `kitaru-investigation` / `kitaru-replay-experiment` skills
    (the referenced `kitaru-replay-ops` skill is already deleted on this branch).
  - Invariants: update the "at DECODE_ENV=local decode never imports kitaru" phrasing to the
    tightened "no kitaru import unless recording is configured (or a worker task context)".
- **running_the_code/03_runtime.md**: rewrite — plain headless `decode run`, recording opt-in,
  worker replay + agent version 2 (137's reproducible sequence lives or is referenced here).
- **running_the_code/06_credentials.md**: bucket-on-new-client (132) + Kitaru replay secrets
  (`--secret-id`) distinction. **07_infra.md**: managed workspace + worker replaces the old
  local-server/deploy story (mark the GCP appendix stale rather than rewriting it).
- Delete `tasks/future/hitl-replay-answer-reuse.md` (describes dead wait/replay semantics).
- Sweep stale references: `grep -rn "checkpoint\|durable\|exec_id\|--hitl\|decode replay" AGENTS.md running_the_code/ .claude/` and fix hits that describe the dead model (the
  compaction "checkpoint JSONL line" is a DIFFERENT concept — leave it).

## Acceptance Criteria

- [ ] AGENTS.md contains no reference to durable flows, checkpoints, waits, `decode replay`, `--hitl`, or `kitaru[local,pydantic-ai,llm]`; the replay section names sessions/replays/workers and the installed skills.
- [ ] `running_the_code/03_runtime.md` walks a new operator from `decode run` → recorded session → worker baseline replay, matching 137's evidence.
- [ ] `tasks/future/hitl-replay-answer-reuse.md` deleted.
- [ ] The grep sweep returns no stale durable-model hits in AGENTS.md / running_the_code / .claude.
- [ ] Glossary terms are used verbatim (Recording Seam, Kitaru Session, Kitaru Worker, Agent Version, Baseline Replay).

## Out of scope

- ADR/glossary authoring (done in the grooming commit).
- README marketing copy; scripts/*.sh cleanup.

## Log
