---
id: 020-wire-active-agent
feature: permission-system-agents-catalog
status: pending
---

# Wire the active agent: prompt, tool restriction, agent rules, default mode, --agent

Implements [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md) §5-7.
Depends on: 017, 019 · Blocks: 022

## Scope

Make the selected **Agent (persona)** drive the run: its system prompt, its allowed tool set, its
agent-scoped rules, and its default mode. Mechanism per ADR-0003 §6 (verified against pydantic-ai
1.107: per-tool `prepare=` callback reading `ctx.deps`, returning `None` to hide a disallowed tool).

- **`agent/deps.py`** — add `active_agent: AgentDef` (mutable). The loop/factory read it per turn.
- **`agent/factory.py`** — register each tool with a `prepare=` callback that returns the
  `ToolDefinition` when `spec.name in ctx.deps.active_agent.tools` else `None`. Add a **dynamic**
  `@agent.instructions` hook that appends `ctx.deps.active_agent.prompt` (alongside the existing
  static base + memory hook), so switching agents changes the prompt on the next turn with no rebuild.
- **Agent selection helper** (in `agents/`) — given an agent name: load the `AgentDef`, set
  `deps.active_agent`, call `gate.set_mode(agent.mode)` (selecting resets the mode), and load the
  agent's `allow`/`deny` rules into the gate as its active-agent `RuleSet` (merged with the user rules
  per task 018 precedence).
- **`cli.py`** — add `--agent NAME` (default `build`); validate against the catalog (unknown → one
  friendly stderr line + non-zero exit, like the no-key guard); pass to `run_app`.
- **`tui/app.py` `run_app`** — load the startup agent (default `build`), set `deps.active_agent`,
  load its rules, and initialise the gate mode from the agent's default before the loop.

## Acceptance criteria

- [ ] `AgentDeps.active_agent` holds the selected `AgentDef`; `run_app` defaults it to `build`.
- [ ] With `active_agent = plan` (tools exclude `write`/`edit`/`bash`), the model's visible tool
      schema for a run **omits** `write`/`edit`/`bash` and includes the read-only set — verified by
      driving a `TestModel` (which calls every visible tool) and asserting the mutating tools are
      never called / not in the schema.
- [ ] The per-agent system prompt is injected via the dynamic instructions hook: a run with
      `active_agent = code-reviewer` includes the code-reviewer prompt in the assembled instructions;
      switching to `build` changes it on the next turn (no rebuild). Unit-tested via the hook.
- [ ] Selecting an agent resets the gate mode to that agent's default (`plan` → `PLAN`, `build` →
      `DEFAULT`) **and** loads its rules: with `active_agent = code-reviewer`, a `bash("git diff")`
      auto-allows (agent `bash(git *)` rule) while `bash("rm x")` still ASKs. Unit-tested.
- [ ] `decode --agent plan` starts in plan mode with the plan tool set; `decode --agent nope` prints
      one friendly stderr line and exits non-zero (no traceback). Driven through the CLI.
- [ ] **Working looks like:** launched `--agent plan`, asking to write a file is denied (plan mode)
      and `write` is not even offered to the model; launched `--agent build`, the full tool set is
      available; launched `--agent code-reviewer`, `git diff` runs without a prompt.
- [ ] `make ci` green, 0 warnings; the M1 capstone (default `build` agent) still passes.

## Out of scope
- `/agent` and `/mode` slash commands, `--mode` flag, Shift+Tab cycle, footer (task 022).
- Subagent spawning.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §5-7. Tool restriction = per-tool `prepare=` (spike-verified on pydantic-ai
1.107; no `prepare_tools` kwarg exists). Round-2 lock: selecting an agent also loads its catalog rules
into the gate (so code-reviewer's `bash(git *)` takes effect), merged with user rules.
