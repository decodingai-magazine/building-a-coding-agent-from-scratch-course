---
name: adr
description: Draft an Architecture Decision Record in the project's Nygard template.
---
Draft a new ADR under `docs/adr/` for the decision I describe.

1. Find the next number: the highest `docs/adr/NNNN-*.md` + 1, zero-padded to 4 digits.
2. Use the Nygard template, in this order: `# NNNN. Title`, then **Status** (Proposed),
   **Context**, **Decision**, **Consequences**.
3. Context states the problem and forces; Decision is the choice in active voice
   ("We will…"); Consequences lists both the upsides and the new costs/risks.
4. Keep it to what a future reader needs — no restating the whole codebase.
5. Write the file; report the path and title.
