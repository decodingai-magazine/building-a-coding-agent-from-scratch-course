---
id: 048-compaction-docs
feature: context-compaction
status: done
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

- [x] Glossary **Compaction** row describes the **window-relative** two-tier cascade (no SQLite recovery
      log); **Compaction Boundary**, **Microcompaction**, **Memory Compression** (200-line trigger), and
      **Context Gauge** rows exist and are accurate.
- [x] README has a short "Context compaction" surface covering the cascade, output lines, resume,
      memory-file compression, **and the fill gauge**, linking ADR-0006; no ADR duplication.
- [x] AGENTS.md `context/` tree comment reads `(JSONL)`; the Datastore note frames SQLite as deferred and
      references ADR-0006.
- [x] ADR-0006 Status is `Accepted`; Decision/Diagram/Consequences match the shipped code (window-relative
      reserves, both tiers, memory compression, gauge; no stale flat-threshold claims).
- [x] No code changes; `make ci` green (docs only).

## Out of scope
- Any code/behaviour change (041-047).
- Superseding any other ADR.

## Log

### [SWE] 2026-06-26 23:55 — Implementation

Docs-only finalization of the `context-compaction` feature. No source/behaviour change. Before
editing, I read the shipped code (tasks 041-047) to reconcile every doc claim against reality:
`config/settings.py`, `context/compaction.py`, `context/session_log.py`, `memory/extract.py`,
`tui/render.py`, `agent/loop.py`, `tui/app.py`, `.env.example`. The implementation followed
ADR-0006 closely — no drift found — so the work was the status flip, the README surface, and the
two AGENTS.md fixes.

**Files modified**
- `docs/adr/0006-conversation-compaction.md` — flipped `Status:` `proposed` → `Accepted`. Verified
  Decision/Diagram/Consequences against shipped code: window-relative reserves (window
  `1_048_576`, full `0.20`→80%, micro `0.40`→60%), both tiers, microcompaction in-memory/not-
  persisted, full compaction persisted as a typed `compaction` JSONL line, memory compression at
  `memory_max_lines=200` with drop-oldest fallback, gauge `○◔◑◕●`, and the verified pydantic-ai
  fact (`run.result.usage.input_tokens` is a property — confirmed at `agent/loop.py:381`). No
  stale flat-threshold claims remain. No wording fixes needed.
- `README.md` — added a lean "Context compaction" surface (between `## Use` and `## Develop`) in
  the AGENTS.md "Testing E2E" spirit: a tier table (micro ~60% / full ~80% or `/compact`) with the
  exact output lines (`Decode - microcompacted …` / `Decode - compacted …`), `--resume` behaviour
  (full vs compacted history), the on-exit `MEMORY.md` compression at 200 lines, the footer fill
  gauge `○ ◔ ◑ ◕ ●` with green/yellow/red tiers, and the tuning settings
  (`COMPACTION_CONTEXT_WINDOW_TOKENS` + reserve fractions, env-var names confirmed against
  `.env.example`). Links ADR-0006; no ADR duplication.
- `AGENTS.md` — (a) Project Structure tree `context/` comment `(SQLite)` → `(JSONL)`; (b) Tech
  Stack `Datastore | SQLite` row note reframed: "Conversation log is JSONL today; compaction
  landed on it (ADR-0006). SQLite remains a deferred durable-store option." Row kept; nothing else
  touched.
- `docs/glossary.md` — **no edit**. The five rows (Compaction, Compaction Boundary,
  Microcompaction, Memory Compression, Context Gauge) authored in grooming were verified accurate
  against the shipped code (200-line trigger, gauge tiers at 60%/80%, micro not-persisted, full on
  JSONL no-SQLite) — no drift, so no change (PA-owned file; verify-only per task).

**Tests**
- Unit + integration: 839 passing, 0 failing (`make ci`). No new tests — docs-only task, no
  decidable code contract introduced.
- `make format-check` (114 files already formatted) and `make lint-check` (All checks passed)
  clean. ruff covers only code; markdown changes carry no lint surface.

**Acceptance criteria**
- [x] Glossary rows accurate (verified, no edit needed) — window-relative cascade, no SQLite.
- [x] README "Context compaction" surface added — cascade, output lines, resume, memory
      compression, fill gauge; links ADR-0006; no ADR duplication.
- [x] AGENTS.md `context/` reads `(JSONL)`; Datastore note frames SQLite as deferred + refs ADR-0006.
- [x] ADR-0006 Status `Accepted`; content matches shipped code (no stale flat-threshold claims).
- [x] No code changes; `make ci` green.

**Evidence**
```
$ make ci
... ruff format --check → 114 files already formatted
... ruff check → All checks passed!
============================= 839 passed in 7.92s ==============================
```

**Notes**
- `docs/architecture.png` left untouched as instructed.
- Glossary intentionally not edited (read-only PA territory; rows already accurate).
- Uncommitted, per role — handing off to the Tester.

### [Tester] 2026-06-26 23:59 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 114 files formatted; `ruff check` All checks passed)
- Unit tests: 831 passed / 0 failed
- Integration tests: 8 passed / 0 failed (capstone M1 + M3)
- Warnings: 0 (suite runs under `filterwarnings=["error"]` — a warning would have failed it)

**E2E adversarial pass (doc-correctness — every doc claim checked vs shipped code)**
- Happy path: read the full README "Context compaction" surface and confirmed it reads as a coherent user-facing manual-QA section between `## Use` and `## Develop`, linking ADR-0006 with a valid relative path. (PASS)
- Break path 1 (output-line wording exactness): README `Decode - microcompacted context (elided N old tool output(s), …)` and `Decode - compacted context (~N tokens → summary + M recent messages).` vs `tui/render.py:181` / `:172-173` → byte-for-byte match including the trailing period; the README ellipsis fairly abbreviates the `~N tokens` tail, no overstatement. (PASS)
- Break path 2 (env-var names + defaults vs settings/.env): `COMPACTION_CONTEXT_WINDOW_TOKENS=1048576`, `COMPACTION_RESERVE_FRACTION=0.20`, `MICROCOMPACTION_RESERVE_FRACTION=0.40`, `COMPACTION_ENABLED` vs `config/settings.py:70-79` (`compaction_context_window_tokens=1_048_576`, `compaction_reserve_fraction=0.20`, `microcompaction_reserve_fraction=0.40`, `compaction_enabled`) and `.env.example:74-82` → exact match; the 80%/60% fill arithmetic (`window*(1-reserve)`) is correct. (PASS)
- Break path 3 (gauge glyphs + color tiers): README `○ ◔ ◑ ◕ ●` and "green <~60% / yellow ~60–80% / red ≥~80%" vs `render.py:32` `_GAUGE_GLYPHS="○◔◑◕●"` and `context_gauge` (`>= danger_at` red, `>= warn_at` yellow, else green) with `app.py:502-503` deriving `warn_at=1-0.40=0.60`, `danger_at=1-0.20=0.80` → exact. (PASS)
- Break path 4 (`/compact` claim — exists + idle-only): README "full at 80% / `/compact`" + "manual `/compact` still works" vs `app.py:134-140` `is_compact_command` and `_handle_compact_command` (`if runner.phase is not Phase.IDLE: emit(_COMPACT_BUSY); return` → idle-only; ignores thresholds & `compaction_enabled`). (PASS)
- Break path 5 (200-line memory trigger): README "200-line cap" vs `config/settings.py:64` `memory_max_lines=200` and `memory/extract.py:27-31` `compress_memory_file` (LLM dedupe at the cap, drop-oldest as guaranteed fallback). (PASS)
- Break path 6 (ADR no stale flat-threshold claim): scanned ADR-0006 §3/Decision/Consequences — every flat-threshold mention is explicitly marked as *superseded* ("supersedes the earlier flat-threshold choice"), i.e. recorded divergence, not a live claim. `run.result.usage.input_tokens` is a property — confirmed at `agent/loop.py:381` (`self._last_input_tokens = run.result.usage.input_tokens`, no call). (PASS)

**Acceptance criteria**
- [x] PASS — Glossary Compaction row is window-relative, no SQLite recovery log; Compaction Boundary, Microcompaction, Memory Compression (200-line), Context Gauge rows present + accurate — `docs/glossary.md:19-23`; all match shipped settings/render/extract code. Verify-only, correctly left unedited.
- [x] PASS — README "Context compaction" surface covers cascade, output lines, resume, memory compression, fill gauge; links ADR-0006; no ADR duplication — `README.md:138-160`.
- [x] PASS — AGENTS.md `context/` comment reads `(JSONL)` and Datastore note frames SQLite as deferred + references ADR-0006 — `AGENTS.md:44,67`; diff touches only those 2 lines.
- [x] PASS — ADR-0006 Status is `Accepted`; Decision/Diagram/Consequences match shipped code (window-relative reserves, both tiers, micro in-memory/not-persisted, full persisted as `compaction` JSONL line, `input_tokens` property) — `docs/adr/0006-conversation-compaction.md:3,28-102`.
- [x] PASS — No code changes; `make ci` green — `git diff --stat` shows only AGENTS.md, README.md, docs/adr/0006, tasks/048 (no src/ or tests/); 839 tests pass, 0 warnings.

**Evidence**
```
$ make unit-tests
============================= 831 passed in 7.45s ==============================
$ make integration-tests
============================== 8 passed in 1.76s ===============================
$ make format-check && make lint-check
114 files already formatted
All checks passed!
$ git diff --stat
 AGENTS.md                                |  4 +-
 README.md                                | 20 +++++++++
 docs/adr/0006-conversation-compaction.md |  2 +-
 tasks/048-compaction-docs.md             | ...
```

**Other issues found**
- None blocking. Note: `code-review` plugin is enabled but the diff is pure Markdown (no code surface for it to act on), so it has no actionable signal here; the manual doc-correctness checklist is the backstop.
- Minor (non-blocking, pre-existing — out of scope): the new README section says `.decode/MEMORY.md`, consistent with the existing README Memory paragraph; AGENTS.md "Testing E2E" elsewhere writes `./MEMORY.md`. Not introduced by this task; flagging only as a future-cleanup note.

**VERDICT: PASS**
