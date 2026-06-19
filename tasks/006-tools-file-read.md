---
id: 006-tools-file-read
feature: m1-vanilla-agent
status: pending
---

# Tools: file read + glob + grep

## Scope
Read-only file tools and the shared output-truncation helper + the tool registry.

## Acceptance criteria
- [ ] `tools/truncate.py`: dual cap (2000 lines OR 50 KB, snap to line boundary), overflow spilled to a temp file whose path is returned.
- [ ] `tools/files.py`: `read` (line-paginated `offset`/`limit`, numbered lines, truncated), `glob`, `grep` — all honor `ctx.deps.cwd`, tagged `read_only=True`.
- [ ] `tools/registry.py` registers tools on the agent (flat registry, each tagged read-only or not).
- [ ] Missing path → model-readable `ModelRetry`.

## Out of scope
- Write/edit (task 007); parallel read-only execution (M3).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Truncation constants validated against pi.
