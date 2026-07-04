---
id: 089-explore-subagents-docs
feature: explore-subagents
status: pending
---

# Docs ripples — README blurb + AGENTS.md `+ subagents` promise + e2e row

Tags: `docs`
Depends on: #088
Blocks: #090

## Scope

Prose-only ripple for the shipped feature (ADR-0013 + the glossary updates already landed in the
grooming commit — this task references, does not re-author, them). No code.

- **README** — add an "Explore subagents" surface blurb: what the `agent` tool does (spawn read-only
  Explore subagents that read the codebase and return a compressed report), that N calls in one turn
  **fan out in parallel**, that children are read-only (`read/glob/grep/lsp`) and permission-free, that
  the TUI is silent-until-done, and the three tuning settings (`subagent_max_parallel`,
  `subagent_max_requests`, `subagent_result_max_bytes`). Match the tone/structure of the existing
  surface sections.
- **AGENTS.md** — fulfill the **`+ subagents`** promise in the Project Structure `agents/` line (reword
  so subagents are shipped, not future). Add one **e2e manual-QA table row** for the `agent` tool
  (a "Type this" + "Working looks like"), mirroring the existing rows (e.g. the `lsp` and `decode run`
  rows): e.g. *"explore how X works across the repo"* → the model issues one or more `agent(...)`
  calls that **auto-allow** (no prompt — READ_ONLY), each renders as a tool call whose result panel is
  a compressed report, and multiple calls run in parallel. Note the two tuning settings inline.
- **Headless ceiling** — in AGENTS.md's runtime/replay prose, one honest line: a subagent run is one
  opaque tool call → one checkpoint; **nested child model calls are not individual replay anchors**, a
  `decode replay --model` swap does **not** reach inside a child, and child token spend is invisible
  until Opik (M10) — ADR-0013 §9.
- **Consistency pass** — the AGENTS.md agents-catalog description and the glossary Subagent / Agents
  Catalog / Agent tool rows agree; ADR-0003's §5 partial-supersession Status note (grooming commit)
  points at ADR-0013.

## Acceptance Criteria

- [ ] README has an "Explore subagents" section naming: the `agent` tool, read-only children
  (`read/glob/grep/lsp`), parallel fan-out, silent-until-done TUI, and the three settings.
- [ ] AGENTS.md's `agents/` Project-Structure line no longer frames subagents as future (`+ subagents`
  fulfilled), and an e2e manual-QA row for the `agent` tool exists with a concrete "Type this" +
  "Working looks like".
- [ ] The headless ceiling is documented (no replay anchors inside a child; `decode replay --model`
  does not reach inside; child tokens invisible until M10).
- [ ] No behaviour/code change; the docs match shipped behaviour, spot-checked against #087/#088
  (the toolset, the settings names/defaults, the READ_ONLY auto-allow).
- [ ] `make format-check` / `make lint-check` unaffected (Markdown only); no broken intra-repo links.

## Out of scope

- Any source/test change (all behaviour ships in #087/#088).
- Re-authoring ADR-0013 or the glossary (grooming commit).
- The capstone (#090).

## Log
