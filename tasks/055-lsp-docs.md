---
id: 055-lsp-docs
feature: lsp-integration
status: pending
---

# Docs: ADR-0007, glossary, README LSP surface, AGENTS.md notes

Tags: `docs`, `lsp`
Depends on: #050, #051, #052, #053, #054
Blocks: #056

This task lands the documentation for the LSP feature so the shipped surfaces are described and the
ubiquitous language is recorded. The ADR's design and the glossary rows were **authored at grooming**
(handed to the SWE as drafts); this task writes them to disk along with the README + AGENTS.md prose
that only makes sense once the feature exists (the same split compaction used: ADR-0006 at the plan
gate, task 048 for the surrounding docs). PA authors docs; SWE writes the provided text verbatim and
adjusts only to match the as-built code.

## Scope

- **ADR:** create `docs/adr/0007-lsp-integration.md` from the PA grooming draft — Nygard five-section
  (Status: `Accepted`; Date; Context; Decision; Diagram — a coloured Mermaid system diagram;
  Consequences). It records: the two-channel design (active `lsp` tool + passive Diagnostics
  Enricher), `ty`-over-pylsp + the **preview / pre-1.0 caveat**, the hand-rolled client + lazy
  per-root + best-effort posture, the swappable-server seam, and the research framing
  (semantic-graph vs text; passive post-edit diagnostics as the highest-ROI channel). Verify it
  matches the as-built code (op set, settings names, behavior) before marking Accepted.
- **Glossary** (`docs/glossary.md`): add the four PA-authored rows — **Code Intelligence**,
  **Language Server**, **LSP Service**, **Diagnostics Enricher** — using the existing table format and
  cross-referencing the existing **Services Interface** row (LSP Service is its first concrete entry).
  Confirm these exact terms are used verbatim in the shipped code/identifiers and user-facing strings.
- **README** (`README.md`): add a short "LSP / code intelligence" surface section — what the `lsp`
  tool does (the four ops), the post-edit diagnostics behavior, the `LSP_*` settings + how to swap the
  server, and that it is best-effort (absent `ty` degrades silently). Match the README's existing
  voice/structure.
- **AGENTS.md:**
  - Refine the `services/` line in the Project Structure tree now that `services/lsp/` exists (it is
    no longer purely "created when you reach the step" — note LSP is the first concrete entry).
  - Add an LSP row to the **Testing E2E** manual-QA table (e.g. type `where is build_agent defined?` →
    the `lsp` tool auto-allows and returns the definition location; and a buggy `.py` write shows the
    appended `LSP diagnostics (ty)` block) consistent with the table's "Type this / Working looks like"
    columns.
  - If a Tech Stack row for the language server / `ty` is warranted, add it consistent with the
    existing rows (per-step "added at its step").
- **Doc-drift check:** the PR Reviewer flags drift, but this task should ensure the canonical glossary
  terms appear verbatim in the diff and no contradicting term ("language client", "ty integration",
  ad-hoc names) leaks into code/docs.

## Acceptance criteria

- [ ] `docs/adr/0007-lsp-integration.md` exists, Status `Accepted`, dated, with all five Nygard
      sections and a coloured Mermaid diagram; its Decision matches the as-built op set, settings, and
      best-effort behavior.
- [ ] `docs/glossary.md` carries the four new rows (Code Intelligence, Language Server, LSP Service,
      Diagnostics Enricher) in the existing table format; each term is used verbatim somewhere in the
      shipped code/strings.
- [ ] `README.md` has an LSP/code-intelligence section covering the four ops, post-edit diagnostics,
      the `LSP_*` settings, server-swap, and best-effort degradation.
- [ ] AGENTS.md: `services/` tree note updated; a Testing-E2E LSP row added.
- [ ] No live references to a non-canonical name for these concepts remain in code/docs/env.
- [ ] `make ci` green, 0 warnings (markdown/doc changes don't break the gate).

## User stories

### Story: A new contributor learns the LSP surface from the README
1. A contributor opens `README.md`, finds the "LSP / code intelligence" section.
2. They learn the `lsp` tool's four ops, that buggy `.py` edits get inline `ty` diagnostics, and how
   to swap the server via `LSP_SERVER_COMMAND`.
3. They run `uv run decode`, type `where is X defined?`, and observe the documented behavior.

### Story: A maintainer reads the design rationale
1. A maintainer opens `docs/adr/0007-lsp-integration.md`.
2. They see why `ty` (same vendor as `ruff`/`uv`) was chosen over pylsp, the honest pre-1.0 caveat,
   why the client is hand-rolled, and how the two channels fit together (with the diagram).

### Story: The glossary keeps the language consistent
1. A reader greps the codebase for "Diagnostics Enricher" and "LSP Service".
2. The terms appear verbatim in code comments/strings and the glossary, with no synonyms drifting.

## Out of scope
- Re-documenting unrelated surfaces; rewriting prior ADRs.
- Implementation changes (those are 050-054); this task only documents.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
Lands ADR-0007 (Accepted), the four glossary rows, the README LSP surface, and the AGENTS.md
tree/Testing-E2E notes — the same docs-task split the compaction feature used (task 048).

**Key decisions**
- ADR + glossary authored at grooming (drafts provided); written to disk here with README/AGENTS.md
  so the prose reflects the shipped surfaces.
- Four canonical terms enforced verbatim across code + docs.

**Dependencies**
- #050-#054 — the feature must exist to document its surfaces.

**User stories**
- 3 stories: README onboarding, ADR rationale, glossary consistency.

Ready for implementation.
