---
id: 089-explore-subagents-docs
feature: explore-subagents
status: done
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

- [x] README has an "Explore subagents" section naming: the `agent` tool, read-only children
  (`read/glob/grep/lsp`), parallel fan-out, silent-until-done TUI, and the three settings.
- [x] AGENTS.md's `agents/` Project-Structure line no longer frames subagents as future (`+ subagents`
  fulfilled), and an e2e manual-QA row for the `agent` tool exists with a concrete "Type this" +
  "Working looks like".
- [x] The headless ceiling is documented (no replay anchors inside a child; `decode replay --model`
  does not reach inside; child tokens invisible until M10).
- [x] No behaviour/code change; the docs match shipped behaviour, spot-checked against #087/#088
  (the toolset, the settings names/defaults, the READ_ONLY auto-allow).
- [x] `make format-check` / `make lint-check` unaffected (Markdown only); no broken intra-repo links.

## Out of scope

- Any source/test change (all behaviour ships in #087/#088).
- Re-authoring ADR-0013 or the glossary (grooming commit).
- The capstone (#090).

## Log

### [SWE] 2026-07-05 02:20 — Implementation

Prose-only ripple for the shipped explore-subagents feature (#087/#088). No behaviour/code change:
the only `.py` edit is a module docstring; everything else is Markdown. Every claim was verified
against the shipped source, not the ADR alone.

**Files modified**
- `README.md` — new `## Explore subagents` surface section (between LSP and Sandboxing): the `agent`
  tool, read-only `read`/`glob`/`grep`/`lsp` children, parallel fan-out, silent-until-done TUI, and
  the three `SUBAGENT_*` tuning settings with defaults.
- `AGENTS.md` — (1) Project-Structure `agents/` line reworded from the future-framed `+ subagents`
  to the shipped axis "Build/Plan/Code-Reviewer (primary) + Explore (subagent, spawned via the agent
  tool)"; (2) new e2e manual-QA table row for the `agent` tool (concrete "Type this" +
  "Working looks like"); (3) new headless-ceiling paragraph in the runtime/replay prose (one opaque
  tool call → one checkpoint; child model calls are not replay anchors; `decode replay --model` does
  not reach inside; child tokens invisible until Opik/M10 — ADR-0013 §9).
- `src/decode/agents/__init__.py` — docstring only: replaced the stale "Subagent spawning is out of
  scope this milestone (the catalog is main-agent only)" with the shipped primary/subagent axis
  (087 Tester's deferred QA ripple). No executable change.

**Tests**
- Unit: 1453 passing, 0 failing — `make unit-tests` (output below). Proves zero behaviour change.
- Integration: N/A — docs/docstring only, no infra touched.

**Acceptance criteria**
- [x] README "Explore subagents" section — the `agent` tool, `read/glob/grep/lsp` children, parallel
  fan-out, silent-until-done, three settings (`SUBAGENT_MAX_PARALLEL`/`_MAX_REQUESTS`/`_RESULT_MAX_BYTES`).
- [x] AGENTS.md `agents/` line no longer frames subagents as future + new e2e `agent` row exists.
- [x] Headless ceiling documented (no anchors inside a child; `decode replay --model` doesn't reach
  inside; child tokens invisible until M10) — AGENTS.md runtime/replay prose.
- [x] No behaviour/code change; docs spot-checked against #087/#088 (toolset, settings names/defaults
  `4`/`25`/`16000`, READ_ONLY auto-allow, BYPASS child gate).
- [x] `make format-check` / `make lint-check` clean; ADR-0013 link target verified present.

**Consistency spot-checks (against shipped source, not the ADR)**
- `subagent_max_parallel=4` / `subagent_max_requests=25` / `subagent_result_max_bytes=16000` —
  `src/decode/config/settings.py:148-150` (all `Field(gt=0)`); env names `SUBAGENT_*` in `.env.example`.
- `agent` tool = `ToolKind.READ_ONLY`, granted build/plan/code-reviewer, never explore —
  `src/decode/tools/registry.py:149-158` + the three primary `builtin/*.md`.
- Child toolset `read/glob/grep/lsp`, `subagent: true` — `src/decode/agents/builtin/explore.md`.
- Child gate = BYPASS, no-op event sink, no `usage=ctx.usage`, `truncate(...max_bytes=...)` —
  `src/decode/tools/agent.py`. Native fan-out `asyncio.create_task` + per-loop semaphore (ADR-0013 §7).
- ADR-0003 §5 partial-supersession note already points at ADR-0013 (grooming commit) — no edit made
  (read-only on `docs/adr/`; its body text is the historical record, handled by the supersession banner).
- Grep across `src/`+README+AGENTS.md for `main-agent only`/`no subagent`/`out of scope this milestone`:
  only hit was the `agents/__init__.py` docstring (fixed). 087 files (`agent_def.py`/`select.py`/
  `loader.py`) already carry the correct axis framing.

**Evidence**
```
$ make format-check
uv run ruff format --check
173 files already formatted

$ make lint-check
uv run ruff check
All checks passed!

$ make unit-tests
... (trimmed) ...
======================= 1453 passed in 92.53s (0:01:32) ========================
```

**Notes**
- `docs/notes/` untouched (tracked, off-limits). `docs/adr/` + `docs/glossary.md` read-only — not edited.
- No commit yet — handing to the Tester first per lifecycle.

### [Tester] 2026-07-05 03:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 173 files already formatted; `ruff check` → All checks passed!)
- Unit tests: 1453 passed / 0 failed
- Integration tests: N/A — docs + one docstring line, no infra touched
- Warnings: 0 (`filterwarnings=["error"]`; a clean pass = zero warnings)

**E2E adversarial pass** (docs task → the adversarial mode is a docs-vs-shipped-source divergence hunt, not a CLI break)
- Happy path — changed set is exactly the 4 intended files, only 1 `.py` (`agents/__init__.py`) and its sole diff line sits *inside* the module docstring (`git diff -- '*.py'` → 4 ins / 2 del, docstring-only): PASS
- Break path 1 (settings name/default/env-casing divergence): README + AGENTS.md claim `SUBAGENT_MAX_PARALLEL=4` / `SUBAGENT_MAX_REQUESTS=25` / `SUBAGENT_RESULT_MAX_BYTES=16000` vs `settings.py:148-150` (`Field(4/25/16_000, gt=0)`) and `.env.example:98-100` (exact env casing) → all match: PASS
- Break path 2 (child-toolset exactness): docs claim read-only `read`/`glob`/`grep`/`lsp`, never `write`/`edit`/`bash`/`web_fetch`/`ask_user` vs `builtin/explore.md` (`tools:` = read/glob/grep/lsp, `subagent: true`) and `agent.py` (child gate `PermissionMode.BYPASS`, `_silent_emit` no-op sink, no `usage=ctx.usage`, `truncate(..., max_bytes=subagent_result_max_bytes)`) → match: PASS
- Break path 3 (headless-ceiling substance vs ADR-0013 §9): AGENTS.md "one opaque tool call → one checkpoint under `"calls"`; not individual replay anchors; `decode replay --model` doesn't reach inside; child rides parent's model / `AgentDef` has no model field; `agent` never in the sandbox-bash cache-disable set; child tokens invisible until Opik/M10" vs ADR §9 + `runtime/flow.py:408-409` (`tool_checkpoint_config_by_name={BASH_TOOL_NAME: {"cache": False}}`, populated only when `sandbox_mode != "none"`, `agent` absent) → substance-exact: PASS
- Break path 4 (READ_ONLY / auto-allow claim): docs claim the `agent` tool auto-allows (no prompt, "can only cause reads") vs `registry.py:154-158` (`kind=ToolKind.READ_ONLY`, comment "it can only cause reads") + `agent.py` (runs inline, never raises `ApprovalRequired`) → match: PASS
- Break path 5 (stale-prose completeness grep across README/AGENTS.md/src `*.py`,`*.md`): `main-agent only` / `no subagent` / `out of scope this milestone` / `+ subagents` → 0 remaining hits (the one stale sentence in `agents/__init__.py` is the fixed diff); `cli.py:485` / `select.py:42` / `loader.py:76` already frame explore as subagent-only-not-selectable; the two "Build / Plan / Explore / Code-Reviewer" enumerations are factual file lists matching the glossary Agents Catalog row, not "explore is selectable": PASS

**Acceptance criteria**
- [x] PASS — README "Explore subagents" section names the `agent` tool, read-only `read/glob/grep/lsp` children, parallel fan-out, silent-until-done TUI, and the three settings — `README.md:313-336`; verified vs `registry.py`, `explore.md`, `settings.py:148-150`, `.env.example:98-100`
- [x] PASS — AGENTS.md `agents/` line reworded to shipped axis (`Build/Plan/Code-Reviewer (primary) + Explore (subagent, spawned via the agent tool)`, no `+ subagents` future-promise left) + new e2e `agent` row with concrete "Type this"/"Working looks like" — `AGENTS.md:38,194`; agrees with glossary Agents Catalog / Subagent / Agent-tool rows
- [x] PASS — Headless ceiling documented (no replay anchors inside a child; `decode replay --model` doesn't reach inside; child tokens invisible until M10) — `AGENTS.md:317-323`; substance-exact vs ADR-0013 §9 + `runtime/flow.py:408-409`
- [x] PASS — No behaviour/code change; docs match shipped #087/#088 (toolset, settings 4/25/16000, READ_ONLY auto-allow, BYPASS child gate) — only `.py` diff is the `agents/__init__.py` docstring; 1453 unit tests green
- [x] PASS — `make format-check` / `make lint-check` clean (Markdown only); ADR-0013 + ADR-0012 link targets present, no broken intra-repo links

**Evidence**
```
$ make format-check
uv run ruff format --check
173 files already formatted
$ make lint-check
uv run ruff check
All checks passed!
$ make unit-tests
======================= 1453 passed in 92.24s (0:01:32) ========================
$ git status --porcelain -- docs/adr docs/glossary.md docs/notes
              (empty — off-limits dirs untouched)
$ git status --porcelain
 M AGENTS.md
 M README.md
 M src/decode/agents/__init__.py
 M tasks/089-explore-subagents-docs.md
```

**Other issues found**
- None. `docs/adr/` + `docs/glossary.md` + `docs/notes/` untouched (verified clean). ADR-0003's body still reads "main-agent only / explore — read-only set + ask_user" (lines 95/101) — but that is historical ADR text guarded by the §5 partial-supersession banner pointing at ADR-0013 (correctly NOT edited by this docs task; docs/adr is off-limits and the current-truth glossary Subagent row already excludes `ask_user`).

**VERDICT: PASS**
