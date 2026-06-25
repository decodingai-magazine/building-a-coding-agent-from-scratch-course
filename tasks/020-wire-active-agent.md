---
id: 020-wire-active-agent
feature: permission-system-agents-catalog
status: done
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

- [x] `AgentDeps.active_agent` holds the selected `AgentDef`; `run_app` defaults it to `build`.
- [x] With `active_agent = plan` (tools exclude `write`/`edit`/`bash`), the model's visible tool
      schema for a run **omits** `write`/`edit`/`bash` and includes the read-only set — verified by
      driving a `TestModel` (which calls every visible tool) and asserting the mutating tools are
      never called / not in the schema.
- [x] The per-agent system prompt is injected via the dynamic instructions hook: a run with
      `active_agent = code-reviewer` includes the code-reviewer prompt in the assembled instructions;
      switching to `build` changes it on the next turn (no rebuild). Unit-tested via the hook.
- [x] Selecting an agent resets the gate mode to that agent's default (`plan` → `PLAN`, `build` →
      `DEFAULT`) **and** loads its rules: with `active_agent = code-reviewer`, a `bash("git diff")`
      auto-allows (agent `bash(git *)` rule) while `bash("rm x")` still ASKs. Unit-tested.
- [x] `decode --agent plan` starts in plan mode with the plan tool set; `decode --agent nope` prints
      one friendly stderr line and exits non-zero (no traceback). Driven through the CLI.
- [x] **Working looks like:** launched `--agent plan`, asking to write a file is denied (plan mode)
      and `write` is not even offered to the model; launched `--agent build`, the full tool set is
      available; launched `--agent code-reviewer`, `git diff` runs without a prompt.
- [x] `make ci` green, 0 warnings; the M1 capstone (default `build` agent) still passes.

## Out of scope
- `/agent` and `/mode` slash commands, `--mode` flag, Shift+Tab cycle, footer (task 022).
- Subagent spawning.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §5-7. Tool restriction = per-tool `prepare=` (spike-verified on pydantic-ai
1.107; no `prepare_tools` kwarg exists). Round-2 lock: selecting an agent also loads its catalog rules
into the gate (so code-reviewer's `bash(git *)` takes effect), merged with user rules.

### [SWE] 2026-06-25 15:55 — Implementation

**Files modified**
- `src/decode/permissions/gate.py` — add `set_agent_rules(RuleSet)` + a second `_rule_sources()`
  entry (user ∪ agent; deny-from-either beats allow-from-either).
- `src/decode/agent/deps.py` — add mutable `active_agent: AgentDef` field (defaults to `build` via
  a lazy factory to dodge the deps↔tools import cycle; `AgentDef` imported under `TYPE_CHECKING`).
- `src/decode/tools/registry.py` — register each tool with a per-tool `prepare=` callback
  (`_restrict_to_active_agent`) that returns `None` to hide a tool absent from
  `ctx.deps.active_agent.tools` (verified `prepare` signature against installed pydantic-ai 1.107).
- `src/decode/agent/factory.py` — add the dynamic `@agent.instructions` hook appending
  `ctx.deps.active_agent.prompt` (alongside the static base + memory hook).
- `src/decode/agents/select.py` (new) — `select_agent(name, *, deps, gate)`: load AgentDef, set
  `deps.active_agent`, `gate.set_mode(agent.mode)`, `gate.set_agent_rules(...)`; raises ValueError
  (listing agents) on unknown name. Exported from `agents/__init__.py`.
- `src/decode/cli.py` — add `--agent NAME` (default `build`); validate via `load_agent` before the
  REPL (unknown → one friendly stderr line + non-zero exit, like the no-key guard); pass to `run_app`.
- `src/decode/tui/app.py` — `run_app(..., agent="build")`: capture the persona name before the
  `agent = build_agent()` shadow, then `select_agent(agent_name, deps=deps, gate=gate)` before the loop.
- Tests: `tests/unit/decode/permissions/test_gate.py` (+6 agent-rule cases),
  `tests/unit/decode/agent/test_deps.py` (+3), `tests/unit/decode/agents/test_select.py` (new, 8),
  `tests/unit/decode/agent/test_factory.py` (+6 visible-tools/prompt cases),
  `tests/unit/decode/tools/test_registry.py` (+1 prepare case), `tests/unit/decode/test_cli.py` (+5).

**Tests**
- Unit: 494 passing, 0 failing (`make unit-tests`).
- Integration: 1 passing — M1 capstone (default `build` agent) still green (`make integration-tests`).

**Acceptance criteria** — all met (see boxes above), verified by:
- `tests/unit/decode/agent/test_deps.py::test_agent_deps_active_agent_defaults_to_build`
- `tests/unit/decode/agent/test_factory.py::test_plan_agent_run_omits_write_edit_and_bash_from_the_visible_tools`
- `tests/unit/decode/agent/test_factory.py::test_active_agent_prompt_is_injected_into_the_run_instructions`
  and `::test_switching_active_agent_changes_the_prompt_on_the_next_turn`
- `tests/unit/decode/agents/test_select.py::test_select_plan_resets_the_gate_mode_to_plan`,
  `::test_select_code_reviewer_loads_its_git_allow_rule`
- `tests/unit/decode/test_cli.py::test_cli_with_an_unknown_agent_exits_nonzero_with_a_friendly_line`,
  `::test_cli_agent_plan_starts_the_real_repl_in_plan_mode`

**Evidence**

```
$ make ci
uv lock --check  → Resolved 166 packages
uv run ruff format --check  → 92 files already formatted
uv run ruff check  → All checks passed!
uv run pytest  → 495 passed in 6.41s
```

End-to-end through the real `build_agent()` + `select_agent` (one Agent, swapped state):
```
agent=plan mode=plan  visible_tools=[ask_user, glob, grep, read, todo_write, web_fetch]  mutating_present=[]
agent=build mode=default  visible_tools=[ask_user, bash, edit, glob, grep, read, todo_write, web_fetch, write]  mutating_present=[bash, edit, write]
code-reviewer: bash('git diff')=allow  bash('rm x')=ask
```
```
$ decode --agent nope
Decode: no such agent 'nope'; available agents: build, code-reviewer, explore, plan   (exit 1)
$ decode --help  →  --agent NAME  Start with this agent persona (build / plan / explore / code-reviewer).
```
Real `run_app` startup driven via a pipe input for `plan` / `build` / `code-reviewer`: each starts,
renders the banner, and exits cleanly on `/quit`.

**Notes**
- `deps ↔ tools` import cycle: `AgentDeps.active_agent` defaults to `build` via a lazy
  `_default_active_agent()` (imports the loader inside the function) and the `AgentDef` annotation is
  `TYPE_CHECKING`-only; a module-level import would cycle (deps → agent_def → tools → registry → deps).
- `run_app`'s `agent` param is shadowed by `agent = build_agent()`; captured as `agent_name` first.
- `enter_plan_mode`/`exit_plan_mode`/`sleep` are not in any *visible* tool set yet — task 021
  registers their functions; here they are only declared names the catalog validates against.
- No architectural forks: stayed within ADR-0003 §5-7 (per-tool `prepare=`, agent-rule source on the
  gate, persona on `deps`). `docs/adr/` and `docs/glossary.md` untouched.

### [Tester] 2026-06-25 19:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 92 files clean; `ruff check` all passed)
- Unit tests: 494 passed / 0 failed
- Integration tests: 1 passed / 0 failed (M1 capstone, default `build` agent)
- `make ci`: PASS — `uv lock --check` ok + format + lint + **495 passed**
- Warnings: 0 (`filterwarnings=["error"]` would have failed the run otherwise)

**E2E adversarial pass** (drove the REAL `build_agent()` + `select_agent` + a `TestModel` that
calls every visible tool; and the REAL `run_app` with `agent=plan/build/code-reviewer` via a
`create_pipe_input` harness)
- Happy path: `decode --agent plan/build/code-reviewer < /dev/null` → banner renders, startup +
  `select_agent` complete (the only trace is prompt_toolkit's pre-existing non-tty `add_reader`
  OSError on a `/dev/null` fd, unrelated to this task; `CliRunner` exits 0 cleanly). (PASS)
- Break path 1 (tool restriction via `prepare=`): plan run's called-tool set =
  `{ask_user, glob, grep, read, todo_write, web_fetch}` — `write`/`edit`/`bash` NEVER called; build
  run includes `{write, edit, bash}`; explore excludes all mutators. (PASS)
- Break path 1b (force a hidden tool): `TestModel(call_tools=["write"])` under plan raised
  `KeyError 'write'` — the tool is genuinely absent from the schema, not just unasserted. (PASS)
- Break path 2 (prompt injection, same Agent object): code-reviewer prompt rides instructions;
  reassigning `deps.active_agent = build` swaps to the build prompt next run with the
  code-reviewer prompt GONE — no rebuild, no leak. (PASS)
- Break path 3 (select resets mode + loads rules): plan→PLAN, build→DEFAULT; code-reviewer
  `bash('git diff')`/`bash('git log --oneline')`→ALLOW, `bash('rm x')`→ASK; USER `deny(git push)`
  beats agent `allow(git *)`→DENY while sibling `git diff` still ALLOW; switching to explore drops
  the agent git rule (→ASK) but the user deny persists. (PASS)
- Break path 4 (plan denies mutations at the gate): plan `write`/`bash` requests → DENY. (PASS)
- Break path 5 (unknown agent state-safety): `select_agent("nope-xyz")` raises ValueError listing
  agents; `deps.active_agent` + gate mode left untouched. (PASS)
- Break path 6 (boundary names): `""`, `"   "`, `"BUILD"`, `"build "`, `"plan\n"` all → ValueError
  (no silent match / no traceback). (PASS)
- CLI: `decode --agent nope`/`--agent ""` → one friendly stderr line listing the four agents +
  exit 1, no traceback; `--help` documents `--agent NAME`; no-key guard correctly precedes agent
  validation (`.env` supplies the key here so agent validation runs). (PASS)

**Acceptance criteria** — all verified
- [x] PASS — `AgentDeps.active_agent` defaults to `build`; `run_app` defaults it.
      `test_deps.py::test_agent_deps_active_agent_defaults_to_build`; `deps.py:101` factory default.
- [x] PASS — plan run omits `write`/`edit`/`bash` from the visible schema.
      `test_factory.py::test_plan_agent_run_omits_write_edit_and_bash_from_the_visible_tools` +
      adversarial break path 1/1b (forced-write → `KeyError`).
- [x] PASS — per-agent prompt via dynamic hook; switching changes it next turn, no rebuild.
      `test_factory.py::test_active_agent_prompt_is_injected_into_the_run_instructions` /
      `::test_switching_active_agent_changes_the_prompt_on_the_next_turn` + break path 2.
- [x] PASS — select resets mode (`plan`→PLAN, `build`→DEFAULT) AND loads rules
      (code-reviewer `git diff` allow, `rm x` ask). `test_select.py::test_select_plan_resets...` /
      `::test_select_code_reviewer_loads_its_git_allow_rule` + real-`run_app` integration drive.
- [x] PASS — `--agent plan` starts plan mode/tools; `--agent nope` → friendly stderr + exit 1.
      `test_cli.py::test_cli_agent_plan_starts_the_real_repl_in_plan_mode` /
      `::test_cli_with_an_unknown_agent_exits_nonzero_with_a_friendly_line` + live CLI run.
- [x] PASS — "Working looks like": verified live via real `run_app` per persona (plan→PLAN +
      mutators hidden; build→full set; code-reviewer→`git diff` auto-allow).
- [x] PASS — `make ci` green, 0 warnings; M1 capstone still passes.

**Evidence**
```
$ make ci
uv lock --check  → ok
uv run ruff format --check  → 92 files already formatted
uv run ruff check  → All checks passed!
uv run pytest  → 495 passed in 6.32s

$ <real build_agent + select_agent adversarial driver>  → ALL ADVERSARIAL CHECKS PASSED
$ <real run_app, agent=plan/build/code-reviewer>        → 3 passed (plan→PLAN, cr git-allow, build→DEFAULT)
$ GEMINI_API_KEY=test-key decode --agent nope
  Decode: no such agent 'nope'; available agents: build, code-reviewer, explore, plan   (exit 1, no traceback)
```

**Other issues found**
- None blocking. Note (not in AC, for the PR Reviewer / future tasks): the `enter_plan_mode` /
  `exit_plan_mode` / `sleep` orchestration names are listed in the `build`/`plan` catalogs but have
  no registered tool function yet (task 021 wires them). They are correctly validated names today
  and are not visible tools, so the `prepare=` allowlist never surfaces a non-existent tool — no
  defect, just the expected seam.
- Static review of the diff: no `print()` in library code, no hardcoded secrets, every changed
  function (incl. multi-line signatures) is return-annotated. `docs/` untouched (no doc AC).

**VERDICT: PASS**
