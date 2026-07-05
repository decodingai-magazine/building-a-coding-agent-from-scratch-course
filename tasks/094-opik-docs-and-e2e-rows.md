---
id: 094-opik-docs-and-e2e-rows
feature: opik-observability
status: pending
---

# Opik observability — docs ripples, E2E rows, ADR-0013 §9 closure

Tags: `observability`, `opik`, `docs`
Depends on: #092, #093
Blocks: #095

## Scope

Document the shipped tracing so a reader can turn it on and know what they will see (ADR-0014). No
product code.

- **README** — a "Monitoring / Observability (Opik)" section: enable by setting `OPIK_API_KEY`
  (presence-based); self-host via `OPIK_URL_OVERRIDE`; what you get (a trace per REPL turn / per
  `decode run`, every LLM + tool call with inputs/outputs, latency, tokens, and cost for priced
  models); the silent-no-op default; that memory write-back + compaction ride along as their own small
  traces; and that evals are M13.
- **AGENTS.md** — add a Testing-E2E table row (in the interactive surfaces table) for Opik: what to
  set, and what "working" looks like (a `Decode - Opik tracing on (project 'decode').` line on launch;
  a trace per turn visible in the Opik UI grouped by session thread). Add a headless note (a trace per
  `decode run`, grouped by exec_id). These ARE the manual-QA rows the feature is verified against.
- **`.env.example`** — re-verify the Opik block from 091 reads correctly end to end (commented,
  documented, presence-based).
- **ADR cross-refs** — add a one-line closure note to `docs/adr/0013-explore-subagents.md` §9 and its
  Consequences "Seams left for later" bullet: child token spend / per-child cost is now visible via
  Opik traces (M10, ADR-0014) — the subagent child run nests inside the parent turn's trace. Do not
  rewrite ADR-0013's decision; append the closure pointer only (the one allowed Accepted-ADR edit
  style for a fulfilled future-seam).
- **Glossary** — confirm the four grooming-authored rows (Trace, Span, Thread (Opik), Observability)
  are present and consistent with the shipped code identifiers.

## Acceptance Criteria

- [ ] README has an Opik monitoring section: enablement (`OPIK_API_KEY`), self-host
  (`OPIK_URL_OVERRIDE`), what-you-see, no-op default, M13 pointer.
- [ ] AGENTS.md gains an Opik E2E/manual-QA row (interactive) + a headless note, matching the actual
  startup line and behavior.
- [ ] ADR-0013 §9 + Consequences carry the M10/ADR-0014 closure note (child cost now visible via
  nested Opik traces); no other ADR-0013 content changed.
- [ ] `.env.example` Opik block verified accurate; glossary rows present and drift-free.
- [ ] Terminology matches the glossary verbatim (Trace / Span / Thread / Observability); no
  invented synonyms. `make ci` green (docs-only).

## Out of scope

- Product code (091/092/093) and the automated capstone / live smoke (095).

## Log
