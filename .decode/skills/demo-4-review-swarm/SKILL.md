---
name: demo-4-review-swarm
description: Demo skill that fans out three parallel read-only Explore subagents to review three decode modules, then merges their findings into one severity-ranked verdict with health scores and a diagram per module, written to review-verdict.md.
---

Run a parallel code-review swarm over decode's own source: three read-only Explore subagents
review three modules at once, and you fold their reports into a single severity-ranked verdict
with a health scorecard.

This is a showcase of the `agent` tool's native parallel fan-out (ADR-0013): N `agent(...)` calls
in one response run concurrently, each an Explore subagent whose toolset is read-only by
construction (`read` / `glob` / `grep` / `lsp` — no `write`, `edit`, or `bash`). Nothing the
subagents do mutates the repo.

## Fan out — three subagents in ONE response

Spawn all three Explore subagents in a single model turn (so they run in parallel, not one after
the other), one per module:

- **Subagent A → `src/decode/permissions/`** — the allow/ask/deny gate, rules, and permission
  modes.
- **Subagent B → `src/decode/sandbox/`** — the executor seam, docker/modal backends, workspace,
  and hand-back.
- **Subagent C → `src/decode/context/`** — compaction and the JSONL session log.

Give each subagent the same brief: read every file in its module and report, as a read-only
reviewer, on correctness, clarity, error handling, and any risky edges. Ask each one to return:

1. A short prose summary of what the module does and how its files fit together.
2. A **text-based diagram** of the module's structure — a Mermaid `flowchart` showing the files
   and how they call each other.
3. A flat list of findings, each tagged with a severity: **Critical**, **Major**, or **Minor** —
   each naming the `file:line` it refers to.
4. A **health score from 1 to 10** with a one-sentence justification.

## Merge — one severity-ranked verdict

When all three reports come back, fold them into ONE verdict — do NOT paste the three reports
verbatim:

- A **health scorecard** up top: one row per module with its score /10 and the one-line
  justification.
- A single findings table across all three modules, **ranked by severity**: every **Critical**
  first, then every **Major**, then every **Minor** — each row naming the module and the
  `file:line` it refers to.
- The three modules' **Mermaid diagrams**, one per module, each under its own heading.
- A two-or-three-line closing judgement: is the reviewed code healthy, and what is the single
  most important thing to fix first?

## Ship the artifact

Write the merged verdict to `review-verdict.md` — this is the one file the swarm produces, and
GitHub renders its Mermaid diagrams natively, so it doubles as a shareable review artifact.

Reply with the scorecard, the count of findings per severity, and the closing judgement — and
point the human at `review-verdict.md` for the full verdict. Note in one line that the whole
review was read-only: the only file the run created is the verdict itself.
