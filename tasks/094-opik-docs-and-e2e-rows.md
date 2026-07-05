---
id: 094-opik-docs-and-e2e-rows
feature: opik-observability
status: done
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

- [x] README has an Opik monitoring section: enablement (`OPIK_API_KEY`), self-host
  (`OPIK_URL_OVERRIDE`), what-you-see, no-op default, M13 pointer.
- [x] AGENTS.md gains an Opik E2E/manual-QA row (interactive) + a headless note, matching the actual
  startup line and behavior.
- [x] ADR-0013 §9 + Consequences carry the M10/ADR-0014 closure note (child cost now visible via
  nested Opik traces); no other ADR-0013 content changed.
- [x] `.env.example` Opik block verified accurate; glossary rows present and drift-free.
- [x] Terminology matches the glossary verbatim (Trace / Span / Thread / Observability); no
  invented synonyms. `make ci` green (docs-only).

## Out of scope

- Product code (091/092/093) and the automated capstone / live smoke (095).

## Log

### [SWE] 2026-07-05 13:50 — Implementation

**Files modified** (docs-only — NO product code)
- `README.md` — new `## Monitoring / Observability (Opik)` section (after Sandboxing, before Develop): enablement via `OPIK_API_KEY`, self-host via `OPIK_URL_OVERRIDE`, what-you-see (Trace per turn/run, Spans with tokens + priced cost), silent no-op default, memory/compaction + subagent free-riders, M13 pointer.
- `AGENTS.md` — one new Testing-E2E interactive row **Opik tracing (observability)** (inserted before the `decode run (headless)` row) with the verbatim startup line + a headless note (trace per `decode run`, Thread = exec_id, stdout stays the answer / stderr untouched, activation log-only).
- `docs/adr/0013-explore-subagents.md` — **append-only** closure: one sentence appended to §9 and one new Consequences bullet after "Seams left for later" (child token spend now visible via nested Opik traces, M10/ADR-0014). No existing ADR-0013 content rewritten (diff verified: pure additions).
- `tasks/094-opik-docs-and-e2e-rows.md` — status → in-progress; acceptance checkboxes ticked; this log entry.

**Verification against shipped code** (each doc claim traced to source before writing)
- Startup line `Decode - Opik tracing on (project 'decode').` — `tui/app.py:1111`, emitted once before the banner only when `opik_tracing_active` (`init_tracing()` at `app.py:955`).
- REPL trace `chat_turn`, `thread_id = session_id`, wraps the whole `while True` (approve/resume + follow-up share the trace) — `agent/loop.py:190` (+ docstring 114-118, 177-185).
- Headless `decode_run` / `decode_run_hitl`, `thread_id = current_execution_id()`, activation log-only / stdout pipe-clean, sibling-span ceiling under real provider on `checkpoint_strategy="calls"` — `runtime/flow.py:38-57, 523/529, 678/689`.
- Settings presence-based, four vars + defaults (`default` / `decode`), cloud base + self-host, trailing-slash tolerated — `config/settings.py:130-144`, `observability/tracing.py:42,73`.

**No-drift confirmations** (re-verified, nothing changed)
- `.env.example` Opik block (lines 57-70): commented, all four vars (`OPIK_API_KEY` / `OPIK_WORKSPACE` / `OPIK_PROJECT_NAME` / `OPIK_URL_OVERRIDE`), no-op default + cloud/self-host bases documented. Accurate — no edit.
- Glossary rows Trace / Span / Thread (Opik) / Observability (Opik tracing) present and consistent with shipped identifiers (`chat_turn`, `decode_run`/`decode_run_hitl` via Thread row, `init_tracing`, `OPIK_API_KEY`, `gen_ai.usage.*` / `gen_ai.aggregated_usage.*`). Drift-free — no edit.

**Tests / gate**
- Unit: 1490 passing, 0 failing — `make pre-commit` (format-check + lint-check + unit-tests) green.
- Integration: N/A — docs-only, no infra/product change.
- Table integrity: the AGENTS.md Opik row is a well-formed 3-cell row (4 pipe delimiters, no stray `|`).

**Acceptance criteria**
- [x] README Opik monitoring section (enablement / self-host / what-you-see / no-op default / M13).
- [x] AGENTS.md Opik interactive E2E row + headless note, matching the actual startup line + behavior.
- [x] ADR-0013 §9 + Consequences closure note; append-only (no other content changed).
- [x] `.env.example` verified accurate; glossary rows present + drift-free.
- [x] Glossary terminology used verbatim (Trace / Span / Thread / Observability); gate green (docs-only).

**Evidence**
```
$ make format-check && make lint-check
uv run ruff format --check
183 files already formatted
uv run ruff check
All checks passed!

$ make pre-commit
... format-check + lint-check ...
uv run pytest tests/unit
======================= 1490 passed in 107.39s (0:01:47) =======================

$ git diff --stat
 AGENTS.md                           |  1 +
 README.md                           | 33 +++++++++++++++++++++++++++++++++
 docs/adr/0013-explore-subagents.md  |  7 ++++++-
 tasks/094-opik-docs-and-e2e-rows.md |  2 +-
```

**Notes**
- NOT COMMITTED — handing to the Tester first per role workflow.
- `docs/notes/` untouched (user-owned). No product code changed. Terminology is glossary-verbatim; no invented synonyms.
- ADR-0013's `-2` in the diff stat is the line-wrap of the appended §9 sentence, not a deletion — `git diff docs/adr/0013-explore-subagents.md` confirms every existing word is preserved.

### [Tester] 2026-07-05 14:35 — QA

Docs-only task — QA is accuracy + integrity, not test suites. Every doc claim traced to shipped source; the README enablement was run live (offline) as a new user.

**Test summary**
- Format / lint / pre-commit: PASS (`format-check` exit 0, `lint-check` exit 0, `pre-commit` unit run 1490 passed)
- Unit tests: 1490 passed / 0 failed
- `uv lock --check`: PASS (lockfile consistent — docs-only, no dep change) → the `make ci` delta is green
- Hermetic Opik trace tests (the doc-claim backstop): 40 passed (repl + headless integration + tracing/loop/flow/app unit)
- Warnings: 0

**E2E adversarial pass** (live docs probe — `filterwarnings=["error"]`, no real network needed)
- Happy path: `printf '/quit\n' | GEMINI_API_KEY=fake OPIK_API_KEY=fake uv run decode` → prints `Decode - Opik tracing on (project 'decode').` **verbatim**, BEFORE the banner; REPL renders + `/quit` exits `Decode - bye.` cleanly (PASS)
- Break path 1 (boundary: key unset): `... OPIK_API_KEY= uv run decode` → Opik-line count = 0; banner is first line — decode byte-identical (PASS)
- Break path 2 (failure mode: bogus OTLP endpoint): the fake `OPIK_API_KEY`/cloud base makes `BatchSpanProcessor` export to an unreachable endpoint — app did **not** crash, hang, or block; background export fails silently, REPL fully usable (PASS)
- Break path 3 (table integrity: interior-pipe injection): new AGENTS.md row has exactly 4 pipe delimiters (= header's 3 cells), no interior bare `|` — renders as a well-formed row (PASS)
- Break path 4 (terminology: invented synonyms): grep of new prose for `telemetry`/`logging`-as-concept — only hit is the substring inside `OpenTelemetry SDK` (correct standard name), not a synonym (PASS)

**Acceptance criteria**
- [x] PASS — README Opik monitoring section (enablement / self-host / what-you-see / no-op / M13) — `README.md:470-501`; startup line verified live; `OPIK_URL_OVERRIDE` self-host + `/v1/traces` append + trailing-slash tolerance match `observability/tracing.py:42,73-75`; M13 pointer consistent with AGENTS/glossary
- [x] PASS — AGENTS.md Opik interactive row + headless note match actual startup line/behavior — `AGENTS.md:195`; `chat_turn`/`decode_run`/`decode_run_hitl` = `agent/loop.py:190`, `runtime/flow.py:529,690`; thread_id = session id / `current_execution_id()`; "before the banner" confirmed (`tui/app.py:1111` < banner `:1123`)
- [x] PASS — ADR-0013 §9 + Consequences carry M10/ADR-0014 closure; no other content changed — `git diff --numstat` = +6/-1; the single deleted line (`` `model` field, by design). ``) reappears verbatim as the appended sentence's prefix; Status/Decision untouched; ADR-0014 exists (`docs/adr/0014-opik-observability.md`, Accepted)
- [x] PASS — `.env.example` Opik block accurate + glossary rows present/drift-free — `.env.example:57-70` (four vars, defaults `default`/`decode`, cloud + self-host bases, presence-based no-op); glossary rows Trace/Span/Thread(Opik)/Observability at `docs/glossary.md:66-69`, identifiers match code (`chat_turn`, `gen_ai.usage.*`, `gen_ai.aggregated_usage.*`, `init_tracing`, `send_to_logfire=False`); neither file in the diff (drift-free)
- [x] PASS — terminology glossary-verbatim, no invented synonyms; gate green — Trace/Span/Thread/Observability used verbatim in both surfaces; four env-var defaults match `config/settings.py:138-144`

**Doc-vs-code truth audit (spot evidence)**
- Startup string: `tui/app.py:1111` `f"Decode - Opik tracing on (project '{settings.opik_project_name}')."`, default `opik_project_name="decode"` → verbatim match (also confirmed live)
- No-op / byte-identical: `observability/tracing.py:68-70` (no key → returns False, builds nothing, mutates no `os.environ`); backed by `test_inactive_turn_emits_zero_spans` / `test_inactive_*_run_emits_zero_spans`
- Export from settings, never global `OTEL_*`: `observability/tracing.py:20-22,74-85`
- `--resume` → new Thread: `tui/app.py:1052` `SessionLog.create(...)` (no `session_id`) mints fresh `uuid4()` (`context/session_log.py:113`); resume only seeds history (`:1048`)
- Subagent nesting closure: global `instrument_pydantic_ai()` covers subagents (`observability/tracing.py:5-6`); nesting mechanism proven by `test_in_turn_compaction_nests_under_the_turn_root` (same-trace via contextvars)

**Evidence**
```
$ printf '/quit\n' | GEMINI_API_KEY=fake OPIK_API_KEY=fake uv run decode 2>&1 | head -3
Warning: Input is not a terminal (fd=0).
Decode - Opik tracing on (project 'decode').
Decode - gemini:gemini-2.5-flash - type a line; /quit exits.

$ printf '/quit\n' | GEMINI_API_KEY=fake OPIK_API_KEY= uv run decode 2>&1 | grep -c "Opik tracing on"
0

$ make format-check && make lint-check     → 183 files formatted; All checks passed! (exit 0)
$ make pre-commit                          → 1490 passed
$ uv lock --check                          → Resolved 155 packages (exit 0)
$ uv run pytest <opik trace tests> -q      → 40 passed
$ git status --porcelain                   → only AGENTS.md, README.md, docs/adr/0013-*.md, tasks/094-*.md (docs/notes untouched)
```

**Other issues found** (non-blocking — PASS with note)
- ADR-0013 diff is `-1/+6`, not the "-2/+7" in the SWE note; immaterial bookkeeping — append-only integrity is intact and every original word is preserved.
- ADR-0013 §9 closure wording "(same asyncio task / contextvars)" is slightly loose for *parallel* subagents (fanned out via `asyncio.create_task` = separate tasks; nesting holds because contextvars copy-propagate into the child task). The observable claim (child nests inside parent trace) is correct; the "/ contextvars" half captures the real mechanism. Accepted-ADR prose nuance only — not worth a fix on its own; orchestrator/PR-Reviewer's call.
- `code-review` plugin is enabled but the diff is zero-code (README/AGENTS/ADR/task prose); its regression value doesn't apply here — the doc-vs-code audit + live probe cover the accuracy dimension instead. Noted for transparency.

**VERDICT: PASS**

### [PA] 2026-07-05 18:20 — Acceptance Review

**VERDICT: ACCEPT** (feature-level; full AC-cluster evidence in the `tasks/095` acceptance log)

Verified from the user's POV as part of the opik-observability feature review on PR #27. The docs let a
real user succeed unaided: the README "Monitoring / Observability (Opik)" section covers enable /
self-host / what-you-see / no-op / M13, the AGENTS.md E2E row + headless note match the shipped startup
line and behavior (checked live), the ADR-0013 §9 + Consequences closure is genuinely append-only
(`+7/-1`, the `-1` a line-wrap; every original word preserved), and the 4 glossary rows
(Trace/Span/Thread (Opik)/Observability) are drift-free against the shipped identifiers. Terminology is
glossary-verbatim throughout. One non-blocking prose nit (README headline vs the headless sibling
caveat) recorded in the `tasks/095` log for the PR Reviewer. Hand off to the PR Reviewer.
