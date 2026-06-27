---
id: 052-lsp-tool
feature: lsp-integration
status: done
---

# The `lsp` tool: model-callable code intelligence (4 ops, READ_ONLY)

Tags: `lsp`, `api`, `agent`
Depends on: #051
Blocks: #055, #056

This task implements ADR-0007 (the **active** channel). It adds one model-callable tool, `lsp`,
exposing the four Code Intelligence ops over the LSP Service (task 051). It is `READ_ONLY` and follows
the existing READ_ONLY convention **exactly** (same class as `read` / `web_fetch`).

## Scope

- **New tool module** `src/decode/tools/lsp.py` with `LSP_TOOL_NAME = "lsp"` and an
  `async def lsp(ctx: RunContext[AgentDeps], op: str, path: str, line: int | None = None,
  column: int | None = None) -> str`. Model the structure on `tools/web.py` (the closest READ_ONLY
  async tool) — module docstring explaining the contract, model-readable returns, errors mapped to
  `pydantic_ai.ModelRetry`, never crashing the loop.
  - `op` ∈ {`definition`, `references`, `hover`, `diagnostics`}. An unknown `op` raises `ModelRetry`
    listing the four valid ops.
  - `definition` / `references` / `hover` require `line` AND `column` (1-based, matching `read`'s
    `cat -n` lines and `grep`'s `path:lineno` — the model reads a symbol's position from those tool
    outputs). A missing line/column for these ops → `ModelRetry` asking for them. `diagnostics`
    needs only `path` (line/column ignored if given).
  - `path` resolves under `ctx.deps.cwd` (reuse the file tools' containment helper, or document why a
    direct resolution is safe); an out-of-tree / missing path → `ModelRetry`.
  - Calls the task-051 service ops with `(ctx.deps.cwd, path, line, column)`.
  - **Returns (model-readable strings):**
    - `definition` → the target location(s) as `path:line:column` (1-based); "no definition found"
      when empty.
    - `references` → a newline list of `path:line:column` (1-based), counted; "no references found"
      when empty.
    - `hover` → the hover text/markdown; "no hover info" when empty.
    - `diagnostics` → a compact list `severity path:line:column message` (all severities here — the
      tool is the explicit query surface; the *enricher* is the errors-only one), counted; "no
      diagnostics" when clean.
  - **Best-effort / unavailable:** when the service reports "unavailable" (no server, timeout, broken
    spawn, `lsp_enabled == False`) the tool returns a clear `ModelRetry` (e.g. "code intelligence is
    unavailable (the language server did not respond); fall back to `read`/`grep`") so the model
    adapts — it NEVER raises into the loop.
  - **READ_ONLY:** the tool only reads code intelligence; it does NOT raise `ApprovalRequired` and is
    classified `ToolKind.READ_ONLY`, so the gate auto-allows it under `default` mode (no prompt), like
    `read`/`grep`/`web_fetch`.
- **Registry wiring** (`tools/registry.py`): add one `ToolSpec(name=lsp_module.LSP_TOOL_NAME,
  func=lsp_module.lsp, kind=ToolKind.READ_ONLY)` in the read-only cluster, with a comment matching the
  surrounding style (task 052 / ADR-0007).
- **Persona allowlists:** add `lsp` to the `tools:` frontmatter of all four built-in agents
  (`src/decode/agents/builtin/{build,explore,plan,code-reviewer}.md`). It is a read-only
  code-intelligence tool, so every persona that already has `read`/`grep` should have it; without
  this, the per-tool `prepare=` callback (registry.py:160) hides it for that run.

## Acceptance criteria

- [x] `lsp` is registered with `kind=ToolKind.READ_ONLY`; a unit test asserts `TOOL_KIND["lsp"] ==
      ToolKind.READ_ONLY` and that it is in `TOOL_SPECS`.
- [x] Under `default` mode the `lsp` tool **auto-allows** (no permission prompt) — asserted via the
      gate (it never raises `ApprovalRequired`), mirroring the `read`/`web_fetch` tests.
- [x] `op=definition|references|hover` with a valid `path,line,column` returns the service's mapped
      result formatted as `path:line:column` / hover text (1-based); unit-tested against a **fake**
      task-051 service seam (no real `ty`).
- [x] `op=diagnostics` with `path` returns the compact diagnostics list (all severities), or "no
      diagnostics" when the fake reports clean.
- [x] Unknown `op` → `ModelRetry` listing the four ops; missing `line`/`column` for a position op →
      `ModelRetry`; out-of-tree/missing `path` → `ModelRetry`. All unit-tested; none crash.
- [x] Service "unavailable" → the tool returns a `ModelRetry` (model-readable), never an exception.
- [x] `lsp` appears in the `tools:` list of all four built-in personas; a unit test (or the existing
      persona-loading test) confirms each persona exposes `lsp`.
- [x] `make ci` green, 0 warnings.

## User stories

### Story: The user asks where a function is defined
1. User launches `uv run decode` (build agent, default mode) and types
   `where is build_agent defined?`.
2. The model reads/greps to locate the call (say `src/decode/tui/app.py:752`), then calls
   `lsp` with `op=definition, path="src/decode/tui/app.py", line=752, column=13`.
3. The tool auto-allows (no permission prompt — READ_ONLY) and returns
   `src/decode/agent/factory.py:NN:1`.
4. The model answers with the file:line of the definition.

### Story: The user asks who calls a function
1. User types `what calls assemble_memory?`.
2. The model calls `lsp` with `op=references` at `assemble_memory`'s position.
3. The tool returns a counted `path:line:column` list of call sites; the model summarizes them.

### Story: The language server is not installed
1. A user without `ty` on PATH asks `where is X defined?`.
2. The `lsp` tool's service reports "unavailable"; the tool returns a `ModelRetry` telling the model
   code intelligence is unavailable.
3. The model falls back to `grep`/`read` and still answers — the REPL never crashes.

### Story: The explore agent uses code intelligence
1. User runs `decode` and `/agent explore`, then asks a "how does X work?" question.
2. The explore agent (read-only toolset) now has `lsp` available and uses `hover`/`definition` to
   trace the symbol — proving the tool is allowlisted for non-build personas.

## Out of scope
- The passive enricher (053) — that is a separate channel folded into `write`/`edit`.
- Ops beyond the four; pagination of huge reference lists beyond a sane cap.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
The active channel: a single READ_ONLY `lsp` tool with a 4-op surface (definition/references/hover/
diagnostics) over the task-051 service, auto-allowed like `read`/`web_fetch`.

**Key decisions**
- One tool, `op` argument (not four tools) — minimal surface per the locked decision.
- 1-based line/column at the tool boundary (matches `read`/`grep`); the service did the wire conversion.
- READ_ONLY → never gates; unavailable/unknown-op/bad-args all map to `ModelRetry`, never a crash.
- Added to all four personas (read-only tool every persona benefits from).

**Dependencies**
- #051 — the LSP Service ops.

**User stories**
- 4 stories: go-to-definition, find-references, server-missing fallback, explore-agent usage.

Ready for implementation.

### [SWE] 2026-06-27 14:30 — Implementation

**Files modified**
- `src/decode/tools/lsp.py` — new `lsp` tool: `LSP_TOOL_NAME` + `async def lsp(ctx, op, path, line, column)`; 4-op dispatch over the task-051 service; model-readable returns; every recoverable problem → `ModelRetry`.
- `src/decode/tools/registry.py` — added `ToolSpec(name=LSP_TOOL_NAME, func=lsp, kind=ToolKind.READ_ONLY)` in the read-only cluster (next to `web_fetch`) + import.
- `src/decode/agents/builtin/{build,explore,plan,code-reviewer}.md` — added `lsp` to each persona's `tools:` frontmatter (after `grep`).
- `tests/unit/decode/tools/test_lsp.py` — new: 30 tests (kind, gating, 4 ops incl. 1-based/counted/all-severity, found-nothing strings, unknown-op/missing-position/out-of-tree/missing-path retries, unavailable→retry across all ops, one through the real agent proving auto-allow).
- `tests/unit/decode/tools/test_registry.py` — added `lsp` to the expected-tools set + READ_ONLY assertions.
- `tests/unit/decode/agents/test_loader.py` — added `lsp` to the read-only set + build's full set; new `test_all_builtin_personas_expose_the_lsp_tool`.

**Tests**
- Unit: 902 passing, 0 failing (`make ci`; was 893 → +9 net new). Integration: 9 passing (capstones unaffected).

**Acceptance criteria**
- [x] `lsp` registered `kind=READ_ONLY`; `TOOL_KIND["lsp"]==READ_ONLY` + in `TOOL_SPECS` — `test_lsp_is_registered_read_only`, `test_registry.py`.
- [x] Auto-allows under default mode, no prompt — `test_lsp_auto_allows_and_runs_through_the_agent` (mirrors web_fetch).
- [x] `definition|references|hover` → `path:line:column` / hover (1-based) vs fake seam — `test_lsp_definition_*`, `test_lsp_references_*`, `test_lsp_hover_*`.
- [x] `diagnostics` → compact all-severity list / "no diagnostics" — `test_lsp_diagnostics_*`.
- [x] unknown op / missing line|column / out-of-tree / missing path → `ModelRetry` — `test_lsp_unknown_op_*`, `test_lsp_missing_line_or_column_*`, `test_lsp_out_of_tree_*`, `test_lsp_missing_path_*`.
- [x] service "unavailable" → `ModelRetry`, never an exception — `test_lsp_unavailable_returns_model_retry` (all 4 ops).
- [x] `lsp` in all 4 personas — `test_all_builtin_personas_expose_the_lsp_tool` + per-persona loader tests.
- [x] `make ci` green, 0 warnings.

**Evidence**
```
$ make ci
uv lock --check
uv run ruff format --check  → 127 files already formatted
uv run ruff check           → All checks passed!
============================= 902 passed in 8.50s ==============================
```
End-to-end (real `build_agent()` + tool invoked as the model would, faked service — no real `ty`):
```
registered lsp?        True
TOOL_KIND[lsp]:        ToolKind.READ_ONLY == ToolKind.READ_ONLY
definition:            src/decode/agent/factory.py:42:1
references:            2 references: / src/decode/tui/app.py:10:5 / src/decode/cli.py:3:9
hover:                 def f() -> int
diagnostics:           2 diagnostics: / error mod.py:2:4 undefined name / warning mod.py:5:1 unused import
definition (none):     no definition found
diagnostics (clean):   no diagnostics
unavailable retry:     code intelligence is unavailable (...); fall back to `read`/`grep`.
unknown op retry:      Unknown op 'rename'; valid ops are: definition, references, hover, diagnostics.
missing column retry:  op 'definition' requires both line and column (1-based); provide them (...).
out-of-tree retry:     Path '../escape.py' resolves outside the working directory; (...).
unapproved:            raised ApprovalRequired (defers to gate -> auto-allow)
```

**Notes**
- **Gating mechanism (please read):** the spec/ADR phrase "does NOT raise `ApprovalRequired`" is internally contradictory with "the gate auto-allows it ... like `read`/`web_fetch`" — read/glob/grep/web_fetch all DO raise `ApprovalRequired` and are then auto-allowed at the gate by READ_ONLY kind. I implemented `lsp` the same way (gate guard first, like web_fetch), placing it in the read-only registry cluster as the spec directs. Net behavior is exactly what the criterion wants: **no prompt under default mode** (proven by the agent-level test: zero `PermissionRequested`, resolver never called). If the Tester instead wants `lsp` to never raise `ApprovalRequired` (run ungated like `ask_user`/`skill`), that's a one-line change — flag it and I'll adjust.
- `path` is validated up front via the file tools' containment helper (`files._resolve_in_cwd`) + an existence/dir check, so a missing/out-of-tree path is its own clear retry, not conflated with "unavailable".
- diagnostics surfaces ALL severities with readable labels (error/warning/info/hint); the errors-only enricher is task 053 (out of scope here).
- No commit yet — handing to the Tester.

### [Tester] 2026-06-27 15:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 127 files formatted; `ruff check` → all checks passed; `make pre-commit` → 893 passed)
- Unit tests: 893 passed / 0 failed (`make unit-tests`)
- Integration tests: 9 passed / 0 failed (`make integration-tests`; capstones unaffected)
- Warnings: 0 (`filterwarnings=["error"]` is configured, so any warning would fail the run)
- `lsp` tests: 30 collected/passed; no subprocess / no real `ty` (service seam faked)

**E2E adversarial pass** (drove the real `lsp` tool / real gate / real persona loader, faking only the task-051 service seam — the same network boundary the suite uses)
- Happy path: `lsp(op=definition|references|hover|diagnostics)` → `src/decode/agent/factory.py:42:1` / `2 references:\na.py:10:5\nb.py:3:9` / `def f() -> int` / `2 diagnostics:\nerror mod.py:2:4 undefined name\nhint mod.py:5:1 hint here` (PASS — 1-based, counted, all severities)
- Break 1 (permission/mode×kind): `PermissionGate(mode=…).check(READ_ONLY lsp)` → ALLOW for DEFAULT/PLAN/EDIT/BYPASS; agent-level forced call emits zero `PermissionRequested`, resolver never called (PASS — auto-allowed like `web_fetch`, which also raises `ApprovalRequired` then defers to the gate)
- Break 2 (UNAVAILABLE vs found-nothing): service `UNAVAILABLE` → `ModelRetry("…unavailable…fall back to read/grep")` for all 4 ops; `None`/`[]` → plain `no definition found`/`no references found`/`no hover info`/`no diagnostics` (PASS — `is UNAVAILABLE` checked first, never conflated; references/diagnostics also defensively map a stray `None` → "no X")
- Break 3 (severity edges): diagnostics with severities {1,2,3,4} → error/warning/info/hint; unknown {9,0} → `severity9`/`severity0` fallback, no KeyError crash (PASS)
- Break 4 (path safety): `/etc/passwd` (absolute out-of-tree) → ModelRetry "outside"; `""` → ModelRetry "directory" (resolves to cwd); a real subdir → ModelRetry "directory"; missing in-tree path → ModelRetry "No such file" (PASS — none reach the service)
- Break 5 (arg validation): unknown op / case variant `Definition` → ModelRetry listing the 4 ops; position op missing line OR column OR both → ModelRetry "line/column"; `diagnostics` works with only `path` and ignores a stray line/column (PASS — op-validity & position checks run before path resolution)
- Break 6 (hostile/large/unicode): 1000-element reference list → counted `1000 references:`, 1001 lines, no truncation/crash; non-ASCII hover (`def café() -> 整数 ☕`) and diagnostic message (`未定義の名前 «x»`) survive intact (PASS)
- Break 7 (never crashes / ordering): unapproved call raises `ApprovalRequired` BEFORE any validation; `line=0`/negative pass the None-check through to the (best-effort) service without crashing the tool (PASS)
- Break 8 (personas): all 4 built-ins (build/explore/plan/code-reviewer) list `lsp`; the `_restrict_to_active_agent("lsp")` prepare callback returns the tool_def for the non-build **explore** persona (control: `write` stays hidden for explore) (PASS — explore can actually call it)

**Acceptance criteria**
- [x] PASS — `lsp` registered `kind=READ_ONLY`; `TOOL_KIND["lsp"] is READ_ONLY` and in `TOOL_SPECS` — `test_lsp_is_registered_read_only`; `registry.py:104-108`
- [x] PASS — auto-allows under default mode, no prompt — `test_lsp_auto_allows_and_runs_through_the_agent` (zero `PermissionRequested`, resolver never called) + gate verified ALLOW for READ_ONLY in all 4 modes; mirrors `web_fetch` (`web.py:97-99` also raises `ApprovalRequired`). Per the resolved clarification, the observable contract (no prompt) is met.
- [x] PASS — `definition|references|hover` → `path:line:column`/hover (1-based) vs fake seam — `test_lsp_definition_*`/`references_*`/`hover_*` + my happy-path run
- [x] PASS — `diagnostics` → compact all-severity list / "no diagnostics" — `test_lsp_diagnostics_*` + unknown-severity fallback verified
- [x] PASS — unknown op / missing line|column / out-of-tree / missing path → ModelRetry, none crash — `test_lsp_unknown_op_*`, `test_lsp_missing_line_or_column_*`, `test_lsp_out_of_tree_*`, `test_lsp_missing_path_*` + my absolute-path/empty-path/dir probes
- [x] PASS — service "unavailable" → ModelRetry (model-readable), never an exception — `test_lsp_unavailable_returns_model_retry` (all 4 ops)
- [x] PASS — `lsp` in all 4 personas — `test_all_builtin_personas_expose_the_lsp_tool` + explore prepare-callback probe
- [x] PASS — `make ci` green, 0 warnings — 893 unit + 9 integration = 902 passed, 0 warnings, format/lint clean

**Evidence**
```
$ make pre-commit
... tests/unit/decode/tools/test_lsp.py ..............................  [ 71%]
============================= 893 passed in 8.29s ==============================
$ make integration-tests
============================== 9 passed in 1.73s ===============================
$ pytest <tester adversarial probes>  # 19 break-path probes, fake service seam
============================== 19 passed in 0.74s ==============================
```

**Other issues found** (non-blocking — PASS with note)
- No positivity validation on `line`/`column`: `line=0`/negative pass the None-check straight to the service (which converts 1-based→0-based on the wire). The tool does not crash (the service is best-effort → `UNAVAILABLE` on any client error), and the spec only requires the values to be *present*, not positive — so this is correct per spec. Flagging only as a possible future hardening for task 053/the service.
- The tool relies on the task-051 service's "never raises" contract: `_run_*` do not wrap the service call in try/except. This is the documented service boundary (every service op already maps exceptions → `UNAVAILABLE`), so it is sound; noted for awareness only.

**VERDICT: PASS**
