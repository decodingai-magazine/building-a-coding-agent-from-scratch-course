---
id: 052-lsp-tool
feature: lsp-integration
status: pending
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

- [ ] `lsp` is registered with `kind=ToolKind.READ_ONLY`; a unit test asserts `TOOL_KIND["lsp"] ==
      ToolKind.READ_ONLY` and that it is in `TOOL_SPECS`.
- [ ] Under `default` mode the `lsp` tool **auto-allows** (no permission prompt) — asserted via the
      gate (it never raises `ApprovalRequired`), mirroring the `read`/`web_fetch` tests.
- [ ] `op=definition|references|hover` with a valid `path,line,column` returns the service's mapped
      result formatted as `path:line:column` / hover text (1-based); unit-tested against a **fake**
      task-051 service seam (no real `ty`).
- [ ] `op=diagnostics` with `path` returns the compact diagnostics list (all severities), or "no
      diagnostics" when the fake reports clean.
- [ ] Unknown `op` → `ModelRetry` listing the four ops; missing `line`/`column` for a position op →
      `ModelRetry`; out-of-tree/missing `path` → `ModelRetry`. All unit-tested; none crash.
- [ ] Service "unavailable" → the tool returns a `ModelRetry` (model-readable), never an exception.
- [ ] `lsp` appears in the `tools:` list of all four built-in personas; a unit test (or the existing
      persona-loading test) confirms each persona exposes `lsp`.
- [ ] `make ci` green, 0 warnings.

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
