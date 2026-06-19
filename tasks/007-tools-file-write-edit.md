---
id: 007-tools-file-write-edit
feature: m1-vanilla-agent
status: pending
---

# Tools: file write + edit

## Scope
Mutating file tools (gated). `edit` uses exact-then-fuzzy matching validated against pi.

## Acceptance criteria
- [ ] `write` creates/overwrites; gated before touching disk.
- [ ] `edit` strips BOM + normalizes CRLF↔LF, matches exact (`indexOf`) then whitespace-normalized fuzzy, requires a UNIQUE match, restores original line endings on write.
- [ ] 0 or >1 matches → model-readable `ModelRetry` ("not found" / "ambiguous, N matches" / "empty").
- [ ] A denied write/edit leaves the file untouched.

## Out of scope
- Per-realpath mutation queue (unneeded under sequential M1; M3).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Normalization + error messages validated against pi.
