---
id: 048-compaction-docs
feature: context-compaction
status: pending
---

# Docs: finalize ADR-0006, glossary, README, AGENTS.md tree/stack fix

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §1 (recorded divergence) on the
documentation surface, covering all mechanisms (microcompaction, window-relative full compaction, memory
compression at 200 lines, the fill gauge). Docs-only — no code. Corrects the AGENTS.md target-tree
"(SQLite)" wording for `context/`.
Depends on: 044, 045, 046, 047 · Blocks: —

## Scope

- **ADR-0006** — confirm committed + cross-linked; reconcile Decision/Diagram/Consequences to the shipped
  reality (window-relative reserves, both tiers, memory compression at 200 lines, the gauge) if anything
  drifted during 042-047, then flip Status to `Accepted`.
- **Glossary** (`docs/glossary.md`) — **refine** **Compaction** (window-relative reserves; no SQLite),
  keep **Compaction Boundary**, **add** **Microcompaction** (no-LLM, in-memory; fires at the higher micro
  reserve), **Memory Compression** (LLM compression of `MEMORY.md` at the 200-line cap vs drop-oldest),
  and **Context Gauge** (the footer fill circle). Land the rows drafted in grooming.
- **README** — lean "Context compaction" manual-QA surface: the window-relative cascade (micro at the
  60%-full line, full at 80% / `/compact`), the expected output lines (`Decode - microcompacted …` /
  `Decode - compacted …`), `--resume` continues the compacted conversation, the on-exit memory-file
  compression at 200 lines, **the footer fill gauge `○◔◑◕●` and its green/yellow/red tiers**, and the
  relevant settings (window + reserves). Short; link ADR-0006.
- **AGENTS.md** — two precise edits (ADR-0006 §1): the `context/` tree comment `(SQLite)` → `(JSONL)`;
  the Tech Stack `Datastore | SQLite` row note reframed as **deferred** ("Conversation log is JSONL today;
  compaction landed on it (ADR-0006). SQLite remains a deferred durable-store option"). Keep the row.

Use canonical glossary terms throughout. Do not contradict ADR-0006.

## Acceptance criteria

- [ ] Glossary **Compaction** row describes the **window-relative** two-tier cascade (no SQLite recovery
      log); **Compaction Boundary**, **Microcompaction**, **Memory Compression** (200-line trigger), and
      **Context Gauge** rows exist and are accurate.
- [ ] README has a short "Context compaction" surface covering the cascade, output lines, resume,
      memory-file compression, **and the fill gauge**, linking ADR-0006; no ADR duplication.
- [ ] AGENTS.md `context/` tree comment reads `(JSONL)`; the Datastore note frames SQLite as deferred and
      references ADR-0006.
- [ ] ADR-0006 Status is `Accepted`; Decision/Diagram/Consequences match the shipped code (window-relative
      reserves, both tiers, memory compression, gauge; no stale flat-threshold claims).
- [ ] No code changes; `make ci` green (docs only).

## Out of scope
- Any code/behaviour change (041-047).
- Superseding any other ADR.

## Log
