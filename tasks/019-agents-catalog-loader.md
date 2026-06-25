---
id: 019-agents-catalog-loader
feature: permission-system-agents-catalog
status: pending
---

# Agents catalog: AgentDef + Markdown/YAML loader + the 4 built-ins

Implements [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md) §5.
Depends on: 017, 018 · Blocks: 020

## Scope

Define the **Agent (persona)** entity and load the built-in **Agents Catalog** from bundled Markdown
files with YAML frontmatter. Pure load + validate — no wiring into the running agent yet (task 020).

- **Declare PyYAML** — `uv add pyyaml` (present only transitively today; AGENTS.md requires declaring
  direct runtime deps). Update `uv.lock`.
- **`entities/agent_def.py`** — `AgentDef` (validated): `name: str`, `description: str`,
  `tools: tuple[str, ...]` (allowlist, validated against the registry's known tool names),
  `mode: PermissionMode` (the default mode), `allow: tuple[str, ...]` + `deny: tuple[str, ...]`
  (optional agent-scoped rule strings, parsed by `permissions/rules.py` from task 018),
  `prompt: str` (the system-prompt body). **No `model` field** (deferred to step 3 — the loader
  ignores unknown frontmatter keys so it stays forward-compatible). Validation: unknown tool name →
  clear error naming the tool; bad mode → clear error; empty name/prompt → error; malformed `allow`/
  `deny` rule → clear error.
- **`agents/` package + bundled files** — create `src/decode/agents/` with `loader.py` and a
  `builtin/` dir of four `*.md` files (frontmatter + prompt body):
  - **build.md** — `tools`: full set (`read`/`glob`/`grep`/`write`/`edit`/`bash`/`todo_write`/
    `web_fetch`/`ask_user`/`enter_plan_mode`/`exit_plan_mode`/`sleep`), `mode: default`. Prompt: a
    capable build agent (the M1 base behaviour).
  - **plan.md** — `tools`: read-only set (`read`/`glob`/`grep`/`web_fetch`/`todo_write`) +
    `enter_plan_mode`/`exit_plan_mode` + `ask_user`, `mode: plan`. Prompt: research + plan, do not
    mutate; present the plan and call `exit_plan_mode`.
  - **explore.md** — `tools`: read-only set + `ask_user`, `mode: default`. Prompt: read the codebase
    and answer; no mutations.
  - **code-reviewer.md** — `tools`: read-only set + `bash` + `ask_user`, `mode: default`,
    `allow: ["bash(git *)"]` (git diff/log/show auto-allow; other bash still asks). Prompt: review a
    diff/code for correctness, simplicity, tests, standards.
- **`agents/loader.py`** — `load_builtin_agents() -> dict[str, AgentDef]` reads + validates every
  bundled file; `load_agent(name)` returns one or raises a clear "no such agent" error listing the
  available names. Files are **packaged data** (`importlib.resources`), so they ship in the wheel.
- The tool-name validation must accept the orchestration tool names (`enter_plan_mode`/
  `exit_plan_mode`/`sleep`) — task 021 registers them; validate against the known-name set (registry
  names ∪ the orchestration tool name constants) so build.md/plan.md validate cleanly regardless of
  task ordering.

## Acceptance criteria

- [ ] `pyyaml` is a declared direct runtime dependency in `pyproject.toml` + `uv.lock`;
      `uv lock --check` is current.
- [ ] `AgentDef` validates: unknown tool name → clear error naming the bad tool; bad `mode` → clear
      error; malformed `allow`/`deny` rule → clear error; a well-formed file loads with the right
      `name`/`tools`/`mode`/`allow`/`prompt`. Unit-tested.
- [ ] `load_builtin_agents()` returns exactly the four built-ins keyed by name
      (`build`/`plan`/`explore`/`code-reviewer`) with the tool allowlists, default modes, and
      (code-reviewer) the `bash(git *)` allow rule from ADR-0003 §5. Unit-tested.
- [ ] `load_agent("nope")` raises a clear error listing the four available agent names. Unit-tested.
- [ ] The four `*.md` files load via the installed package (packaged data), not a hard-coded repo
      path — a test loads them through the package.
- [ ] **Working looks like:** `load_agent("plan").mode is PermissionMode.PLAN` and its `tools`
      contain no `write`/`edit`/`bash`; `load_agent("code-reviewer").allow` contains `bash(git *)`.
- [ ] `make ci` green, 0 warnings; `tests/unit/decode/agents/` mirrors `src/decode/agents/`.

## Out of scope
- Wiring the active agent into the running agent (prompt + tool restriction + rules + mode) — task 020.
- A `model` frontmatter field (step 3 — providers).
- Subagent spawning (not this milestone).

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §5. Round-2 locks folded in: NO `model` field (step 3); `AgentDef` carries
optional agent-scoped `allow`/`deny` rules (reusing task 018's parser) so code-reviewer's `bash(git *)`
lives in the catalog, not the user's settings.json. Depends on 018 for the rule parser.
