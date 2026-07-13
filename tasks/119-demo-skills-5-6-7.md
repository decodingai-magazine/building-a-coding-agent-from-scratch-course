---
id: 119
feature: evals
status: pending
---

# Demo skills 5–7: review-swarm, sandbox-feature-pr, todoist-app

Depends on: none. Implements ADR-0017 §2 (Track A).

## Scope

- **demo-5-review-swarm** — prompt-only: fan out THREE parallel subagents via the `agent` tool
  (explore subagents doing read-only review work, ADR-0013), one per decode module (suggest
  `src/decode/permissions/`, `src/decode/sandbox/`, `src/decode/context/`); merge into ONE
  severity-ranked verdict (Critical/Major/Minor) INCLUDING text-based diagrams (Mermaid/ASCII) of
  each module's structure.
- **demo-6-sandbox-feature-pr** — the meta "decode improves decode" demo. Body documents the full
  flow: launch `SANDBOX_MODE=docker decode --repo <course repo URL>` (note: the grilled spec wrote
  `--sandbox docker`; the CLI exposes sandbox mode via `SANDBOX_MODE` — document the invocation
  that works, and mention the modal variant `SANDBOX_MODE=modal`); implement a small
  self-contained feature inside the Workspace; on exit the Hand-back pushes the
  `decode/<session-id>` Session Branch (ADR-0012 §8); then `gh pr create --draft` against the
  course repo from that branch.
- **demo-7-todoist-app** — prompt-only: write a single-file `index.html` todo app — vanilla JS +
  `localStorage`, zero deps: add / complete / filter (all|active|done) — then `open index.html`.

**Tests**: same loader-parse unit coverage as 118 for all three skills.

## Acceptance Criteria

- [ ] Three skills in the catalog, loadable by name; frontmatter/body conventions hold.
- [ ] demo-6's documented invocation is verified against the real CLI flags (no `--sandbox` flag
      invented) and names both sandbox rungs + the draft-PR step.
- [ ] Manual spot-run of demo-5 or demo-7 end-to-end, logged.
- [ ] `make ci` green.

## Out of scope

- Adding a `--sandbox` CLI flag (file separately if wanted). Credential-proxy anything (non-goal).

## Log
