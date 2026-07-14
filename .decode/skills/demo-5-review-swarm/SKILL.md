---
name: demo-5-review-swarm
description: Demo skill that fans out three parallel read-only Explore subagents to review three decode modules, then merges their findings into one severity-ranked verdict with a diagram per module.
---

Run a parallel code-review swarm over decode's own source: three read-only Explore subagents review
three modules at once, and you fold their reports into a single severity-ranked verdict.

This is a showcase of the `agent` tool's native parallel fan-out (ADR-0013): N `agent(...)` calls in
one response run concurrently, each an Explore subagent whose toolset is read-only by construction
(`read` / `glob` / `grep` / `lsp` — no `write`, `edit`, or `bash`). Nothing here mutates the repo.

## Fan out — three subagents in ONE response

Spawn all three Explore subagents in a single model turn (so they run in parallel, not one after the
other), one per module:

- **Subagent A → `src/decode/permissions/`** — the allow/ask/deny gate, rules, and permission modes.
- **Subagent B → `src/decode/sandbox/`** — the executor seam, docker/modal backends, workspace, and
  hand-back.
- **Subagent C → `src/decode/context/`** — compaction and the JSONL session log.

Give each subagent the same brief: read every file in its module and report, as a read-only
reviewer, on correctness, clarity, error handling, and any risky edges. Ask each one to return:

1. A short prose summary of what the module does and how its files fit together.
2. A **text-based diagram** of the module's structure — a Mermaid `flowchart`/`classDiagram` or an
   ASCII box-and-arrow sketch showing the files and how they call each other.
3. A flat list of findings, each tagged with a severity: **Critical**, **Major**, or **Minor**.

## Merge — one severity-ranked verdict

When all three reports come back, fold them into ONE verdict, do NOT paste the three reports
verbatim:

- A single findings table (or list) across all three modules, **ranked by severity**: every
  **Critical** first, then every **Major**, then every **Minor** — each row naming the module and
  the file:line it refers to.
- The three modules' **diagrams**, one per module, each under its own heading (keep the Mermaid or
  ASCII the subagent produced).
- A two-or-three-line closing judgement: is the reviewed code healthy, and what is the single most
  important thing to fix first?

Report the merged verdict only. Note in one line that the whole review was read-only — no file in
the repo was changed.
