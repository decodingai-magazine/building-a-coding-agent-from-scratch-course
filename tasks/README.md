# Tasks

File-based task tracker (`TRACKER_MODE: file`). **One markdown file per atomic task**, committed to the repo. The set of these files *is* the Tasks Plan — there is no separate plan document.

## Format

`tasks/<NNN>-<slug>.md`, where `NNN` is a zero-padded monotonic counter:

```
tasks/
├── 002-agent-loop.md           # status: in-progress
├── 003-bash-tool.md            # status: pending
└── done/
    └── 001-bootstrap-tui.md    # status: done
```

State lives in the `status:` frontmatter field — **not** in the filename, which never changes. A finished task is flipped to `status: done` and `git mv`'d into `tasks/done/` in the same commit as its code, so the top level of `tasks/` lists only open work.

## Task file shape

```markdown
---
id: 003-bash-tool
feature: tools          # the feature slug this task belongs to
status: pending         # pending | in-progress | done
---

# Bash tool

## Scope
One atomic, independently-shippable unit of work (1–2 sentences).

## Acceptance criteria
- [ ] ...

## Out of scope
- ...

## Log
### [PA] 2026-06-19 12:30 — Grooming
...
```

## Lifecycle

- **PA** grooming writes the file with `status: pending`.
- **SWE** starts it → `status: in-progress`.
- After the **Tester** PASSES and the task is committed → `status: done`.

Every agent **appends** (never rewrites) a timestamped entry to `## Log`: `### [ROLE] YYYY-MM-DD HH:MM — subject`. Roles: `PA`, `SWE`, `Tester`, `PR Reviewer`, `On-Call`.

Tasks are created and driven by the squid pipelines (`/plan`, `/implement-task`, `/implement-night`).
