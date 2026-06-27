---
id: 055-lsp-docs
feature: lsp-integration
status: done
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

- [x] `docs/adr/0007-lsp-integration.md` exists, Status `Accepted`, dated, with all five Nygard
      sections and a coloured Mermaid diagram; its Decision matches the as-built op set, settings, and
      best-effort behavior. (Verified + reconciled: fixed the "never raising `ApprovalRequired`" drift.)
- [x] `docs/glossary.md` carries the four new rows (Code Intelligence, Language Server, LSP Service,
      Diagnostics Enricher) in the existing table format; each term is used verbatim somewhere in the
      shipped code/strings. (Verified accurate against as-built; all four grep-confirmed in `src/decode/`.)
- [x] `README.md` has an LSP/code-intelligence section covering the four ops, post-edit diagnostics,
      the `LSP_*` settings, server-swap, and best-effort degradation.
- [x] AGENTS.md: `services/` tree note updated; a Testing-E2E LSP row added.
- [x] No live references to a non-canonical name for these concepts remain in code/docs/env.
- [x] `make ci` green, 0 warnings (markdown/doc changes don't break the gate).

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

### [SWE] 2026-06-27 14:20 — Implementation

**Files modified**
- `docs/adr/0007-lsp-integration.md` — reconciled the Decision §1 "Active" bullet against as-built (the `lsp` tool DOES raise `ApprovalRequired`; only fix below).
- `README.md` — added the "LSP / code intelligence" surface section + listed `lsp` in the tools line.
- `AGENTS.md` — `services/` tree now shows the concrete `services/lsp/`; added a "Code intelligence" Tech Stack row and a Testing-E2E `lsp` QA row.
- `tasks/055-lsp-docs.md` — status `pending → in-progress`; acceptance checkboxes; this log.

**Note:** the ADR-0007 + the four glossary rows were authored/committed at grooming (like ADR-0006 for compaction); this task VERIFIED + reconciled them against the shipped code and wrote the README/AGENTS.md surfaces. The glossary needed **no** change (all four rows accurate and verbatim in code).

**ADR reconciliation (the only ADR content edit — before/after)**
- Drift: Decision §1 Active bullet claimed the `lsp` tool gives "no prompt, never raising `ApprovalRequired`." As-built (`src/decode/tools/lsp.py` L95-97), `lsp` is `ToolKind.READ_ONLY` and raises `ApprovalRequired` until `ctx.tool_call_approved`, exactly like `read`/`web_fetch`; the gate then **auto-allows** it under `default` mode (no human prompt).
  - Before: "…so the permission gate auto-allows it under `default` mode exactly like `read`/`web_fetch` — no prompt, never raising `ApprovalRequired`."
  - After: "…so — exactly like `read`/`web_fetch` — it raises `ApprovalRequired` until the call is approved and the permission gate **auto-allows** it under `default` mode (no human prompt)."
- Everything else in ADR-0007 confirmed AS-BUILT, no edit needed: op set (`definition`/`references`/`hover`/`diagnostics`), the 5 settings + defaults (`lsp_enabled=True`, `lsp_server_command="ty"`, `lsp_server_args=["server"]`, `lsp_diagnostics_on_edit=True`, `lsp_request_timeout_s=10.0`), errors-only enricher (`severity==1`, header `LSP diagnostics (ty) — fix these:`), `UNAVAILABLE`-vs-`None` ("no answer" vs "found nothing"), lazy per-root + cached-broken-spawn + best-effort, swappable-server seam (`_spawn_process` mirroring bash `_EXECUTOR`/web `_TRANSPORT`), `ty` as a dev-group dep (`pyproject.toml` L58), and shutdown on the `run_app` exit path next to the memory write-back (`tui/app.py` L914-917). The Mermaid diagram already said "auto-allowed" (no false claim); Consequences already said "auto-allows like other read-only tools." Status kept **Accepted**.

**Glossary reconciliation**
- All four rows (Code Intelligence, Language Server, LSP Service, Diagnostics Enricher) accurate vs as-built — **no wording change**. Each term grep-confirmed verbatim in `src/decode/` (e.g. "LSP Service" in `services/lsp/service.py`; "Diagnostics Enricher" in `tools/files.py`).
- Non-canonical-synonym sweep (`language client`, `ty integration`, …) across `src/`, `docs/`, `README.md`, `AGENTS.md`, `.env.example` → **zero hits**.

**Tests**
- Unit: N/A — docs-only, no behavior change.
- Full gate: `make ci` → 919 passed, 0 warnings (the suite runs under `filterwarnings=["error"]`; lockfile check + format-check + lint-check all green).

**Acceptance criteria**
- [x] ADR-0007 exists/Accepted/dated/five sections + coloured Mermaid; Decision matches as-built (drift fixed).
- [x] Glossary four rows present + accurate; each term verbatim in shipped code.
- [x] README LSP section (four ops, post-edit diagnostics, `LSP_*` settings, server-swap, best-effort).
- [x] AGENTS.md `services/` tree note updated + Testing-E2E `lsp` row added (+ Tech Stack row).
- [x] No non-canonical name leaks (grep clean).
- [x] `make ci` green, 0 warnings.

**Evidence**
```
$ make format-fix && make lint-fix && make format-check && make lint-check
uv run ruff format
127 files left unchanged
uv run ruff check --fix
All checks passed!
uv run ruff format --check
127 files already formatted
uv run ruff check
All checks passed!

$ make ci
...
============================= 919 passed in 8.65s ==============================

$ grep -rin "language client\|ty integration" src/ docs/ README.md AGENTS.md .env.example
(no output)
```

**Notes**
- Docs-only; no source/behavior change. The README onboarding story step 3 (`uv run decode` → `where is X defined?` against a real Gemini + real `ty`) is a manual e2e check — every documented claim was cross-checked against the as-built code instead (no automatable runtime surface here).
- Left the ADR's "~120 lines" client size as-is: it is an explicit approximation of the hand-rolled wire logic (framing/handshake/match-by-id), not the full docstring-heavy file — not behavioral drift.
- DID NOT commit — handing off to the Tester first.

### [Tester] 2026-06-27 15:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 127 files clean; `ruff check` all passed)
- Unit tests: 910 passed / 0 failed
- Integration tests: 9 passed / 0 failed (919 total, matches SWE claim)
- Warnings: 0 (suite runs under `filterwarnings=["error"]`)

**E2E adversarial pass (docs-only → DOC-CORRECTNESS, every claim cross-checked vs shipped code)**
- Diff scope: `git diff --name-only` → only `AGENTS.md`, `README.md`, `docs/adr/0007-lsp-integration.md`, `tasks/055-lsp-docs.md` — no source/unrelated files (PASS).
- Break path 1 (reconciled ADR permission line — the one content edit): ADR Decision §1 "raises `ApprovalRequired` until approved, gate auto-allows under `default`" vs `tools/lsp.py:95-97` (`if not ctx.tool_call_approved: raise ApprovalRequired`) + `tools/registry.py:104-108` (`ToolKind.READ_ONLY`) → MATCH (PASS).
- Break path 2 (env-var drift): all 5 README `LSP_*` names vs `config/settings.py` fields → `LSP_ENABLED/SERVER_COMMAND/SERVER_ARGS/DIAGNOSTICS_ON_EDIT/REQUEST_TIMEOUT_S` ↔ `lsp_enabled/lsp_server_command/lsp_server_args/lsp_diagnostics_on_edit/lsp_request_timeout_s`, exact 1:1; defaults `True / "ty" / ["server"] / True / 10.0` match `settings.py:109-120` and `.env.example:110-121` (PASS).
- Break path 3 (header-string accuracy): README + AGENTS.md claim block header `LSP diagnostics (ty) — fix these:` vs `tools/files.py:407` `f"LSP diagnostics ({settings.lsp_server_command}) — fix these:"` with default `ty` → renders byte-identical; errors-only via `files.py:403` `d.severity == _LSP_ERROR_SEVERITY` (`= 1`, `files.py:69`) (PASS).
- Break path 4 (synonym leak): `grep -rin "language client|ty integration|…" src/ docs/ README.md AGENTS.md .env.example` → 0 hits; all 4 canonical terms grep-confirmed verbatim in `src/decode/` (LSP Service, Diagnostics Enricher, Code Intelligence, Language Server) (PASS).
- Break path 5 (AGENTS.md E2E example location claim): `where is build_agent defined?` → `src/decode/agent/factory.py:68:5` vs `grep -n "def build_agent" → factory.py:68` (`def ` = 4 chars → symbol col 5) → MATCH (PASS).
- Break path 6 (ADR structural claims): four ops (`tools/lsp.py:58-59`), `UNAVAILABLE`-vs-`None` (`tools/lsp.py:135-138`, `services/lsp/service.py`), lazy per-root + cached-broken-spawn + swappable `_spawn_process` seam (`service.py:53`), `shutdown_all` on `run_app` exit next to memory write-back (`tui/app.py:908-917`), `ty` dev-group dep (`pyproject.toml:58` under `[dependency-groups].dev`), Status `Accepted` + dated + 5 Nygard sections + coloured Mermaid (`classDef fill:#…`) → all MATCH (PASS).

**Acceptance criteria**
- [x] PASS — ADR-0007 exists, Status `Accepted`, dated 2026-06-27, 5 Nygard sections + coloured Mermaid; Decision matches as-built op set/settings/best-effort. Evidence: `docs/adr/0007-lsp-integration.md:1-162`; reconciled §1 line vs `tools/lsp.py:95-97` + `registry.py:104-108`.
- [x] PASS — Glossary 4 rows present + accurate + verbatim in code. Evidence: `docs/glossary.md:43-46`; grep-confirmed in `services/lsp/service.py:1`, `tools/files.py:66`, `tools/registry.py:101`, `tui/app.py:910`.
- [x] PASS — README LSP section covers 4 ops, post-edit diagnostics, `LSP_*` settings, server-swap, best-effort. Evidence: `README.md:158-174`; cross-checked vs `settings.py` / `files.py` / `pyproject.toml`.
- [x] PASS — AGENTS.md `services/` tree note updated + Testing-E2E `lsp` row + Tech Stack `Code intelligence` row. Evidence: `AGENTS.md:42-43, 64, 190`.
- [x] PASS — No non-canonical name for the LSP concepts in code/docs/env. Evidence: synonym grep → 0 hits.
- [x] PASS — `make ci`-equivalent gate green, 0 warnings. Evidence: pre-commit (910 unit) + integration (9) green; format/lint clean.

**Evidence**
```
$ make pre-commit
... 910 passed in 8.23s
$ make integration-tests
... 9 passed in 1.60s
$ git diff --name-only
AGENTS.md
README.md
docs/adr/0007-lsp-integration.md
tasks/055-lsp-docs.md
```

**Other issues found (non-blocking — PA/PR-Reviewer call, not an AC)**
- ADR §3 + Diagram say the hand-rolled client is "~120 lines"; `services/lsp/client.py` is 345 lines total (~233 statement lines, docstring-heavy). The SWE log explains it as the wire-logic core estimate. It is a soft prose approximation, not a behavioral claim, and matches no acceptance criterion — flagging for PR-Reviewer discretion only.

**VERDICT: PASS**
