---
id: 026-skill-dispatcher-tool
feature: skills
status: pending
---

# Skills: the `skill` dispatcher tool + registry wiring + the four agents see it

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (the Skill Dispatcher, ungated).
Depends on: 024, 025 · Blocks: 027

## Scope

Add the **Skill Dispatcher**: an ungated `skill(name)` tool that returns the named skill's full body
as the tool result. This IS the model-facing, on-demand half of progressive disclosure. Mirror the
ungated tools (`tools/sleep.py`, `tools/orchestration.py`): `ToolKind.OTHER`, never raises
`ApprovalRequired`, `ModelRetry` on bad input.

- **`tools/skills.py`**:
  - `SKILL_TOOL_NAME = "skill"`.
  - `async def skill(ctx: RunContext[AgentDeps], name: str) -> str` — **`name`-only signature, no
    `args`** (ADR-0004 §2: lazy v1, no built-in needs structured args). Calls
    `load_skills(ctx.deps.cwd)`, returns the named skill's `body`. An **unknown `name` raises
    `pydantic_ai.ModelRetry`** listing the available skill names (model-readable, never a crash),
    mirroring `sleep`'s `ModelRetry`.
  - **Ungated**: it never raises `ApprovalRequired`, so it never reaches the permission gate (loading
    instructions is harmless). Docstring follows the `sleep` / `orchestration` conventions and states
    the ungated rationale + that the *actions a skill describes* still pass through their own gates —
    e.g. the `commit` skill's `git add`/`git commit` run via the gated `bash` tool, so default mode
    asks and plan mode denies the commit (ADR-0004 §7).
- **`tools/registry.py`** — add `ToolSpec(name=SKILL_TOOL_NAME, func=skill, kind=ToolKind.OTHER)` to
  `TOOL_SPECS` (ungated, in the same group as `ask_user`/`sleep`). `TOOL_KIND` and `KNOWN_TOOL_NAMES`
  derive `skill` automatically; no other wiring needed. Extend the module docstring's ungated-tools
  list to include `skill`.
- **`agents/builtin/{build,plan,explore,code-reviewer}.md`** — add `skill` to each agent's `tools:`
  list. The registry hides any tool not in the active agent's allowlist (`_restrict_to_active_agent`),
  so without this the dispatcher is invisible — all four must list it (ADR-0004 §4: all agents see all
  skills).
- **Update agent-catalog tests** that pin an exact tool set/count (e.g. `build` goes 12 → 13 tools) so
  no count-drift assertion fails.

## Acceptance criteria

- [ ] `SKILL_TOOL_NAME == "skill"`; `skill` takes `(ctx, name)` only (no `args` parameter).
- [ ] `skill(ctx, "commit")` returns the **built-in** commit skill body (verified against
      `load_builtin_skills()["commit"].body`). Unit-tested (direct call, hand-built `RunContext` like
      `test_orchestration`'s direct harness).
- [ ] `skill(ctx, "nope")` raises `pydantic_ai.ModelRetry` whose message **lists the available skill
      names**. Unit-tested.
- [ ] `skill` respects the project override: with `<cwd>/.decode/skills/commit.md` present,
      `skill(ctx, "commit")` returns the **project** body. Unit-tested.
- [ ] **Ungated, loop-driven:** a `FunctionModel` + real `build_agent()` test scripting a
      `skill("commit")` call returns the body as the tool result and emits **no** `PermissionRequested`
      event (callable even when the gate is in plan mode). Mirrors `test_orchestration.py`'s harness.
- [ ] **Invariant — dispatcher ungated, induced action gated:** a loop-driven test scripting
      `skill("commit")` *then* a mutating tool call (a `bash`/`write`) shows the `skill` call produced
      **no** `PermissionRequested` while the subsequent mutating call **does** reach the gate
      (PermissionRequested emitted / denied in plan mode). This pins ADR-0004 §7 with `commit` as the
      worked example. Unit-tested.
- [ ] `skill` is in `TOOL_SPECS` with `kind == ToolKind.OTHER`; `"skill" in KNOWN_TOOL_NAMES`; and
      `load_builtin_agents()` validates cleanly with `skill` now in all four agents' `tools` (every
      built-in agent lists `skill`). Unit-tested.
- [ ] An agent whose `tools` omits `skill` hides the dispatcher (the `prepare=` callback returns
      `None`) — assert via the existing restriction path; all four built-ins include it.
- [ ] Updated agent-catalog tests reflect the new tool (no exact-count assertion fails); `make ci`
      green, 0 warnings; `tests/unit/decode/tools/test_skills.py` mirrors `src/decode/tools/skills.py`.

## Out of scope
- Catalog assembly + the `@agent.instructions` injection hook — task 027.
- The `/<skill-name>` TUI invocation — task 028.
- The capstone integration test — task 029.
- A `~/.decode/skills` source and a per-agent skill allowlist (deferred, ADR-0004).
- Structured `args` on the dispatcher (deferred, ADR-0004 §2).

## Log
### [PA] 2026-06-25 — Grooming
The dispatcher mirrors the ungated `sleep`/orchestration tools to the letter: `ToolKind.OTHER`,
never raises `ApprovalRequired`, `ModelRetry` on a bad name. Signature is `skill(name)` only
(ADR-0004 §2 — no built-in needs structured args; additive later). Two-layer tests (direct +
loop-driven) like `test_orchestration.py`. Added the **ungated-dispatcher / gated-induced-action**
invariant test (skill → then a gated bash/write) so the round-2 commit-active behavior's safety is
pinned with `commit` as the worked example. Because `skill` is a real registered spec from the start,
`KNOWN_TOOL_NAMES` picks it up automatically — so adding `skill` to the four agents' `tools:` lists
validates cleanly within this task. Flagged the tool-count test update (build 12 → 13).
