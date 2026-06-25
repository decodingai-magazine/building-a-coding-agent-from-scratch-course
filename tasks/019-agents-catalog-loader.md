---
id: 019-agents-catalog-loader
feature: permission-system-agents-catalog
status: done
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

- [x] `pyyaml` is a declared direct runtime dependency in `pyproject.toml` + `uv.lock`;
      `uv lock --check` is current.
- [x] `AgentDef` validates: unknown tool name → clear error naming the bad tool; bad `mode` → clear
      error; malformed `allow`/`deny` rule → clear error; a well-formed file loads with the right
      `name`/`tools`/`mode`/`allow`/`prompt`. Unit-tested.
- [x] `load_builtin_agents()` returns exactly the four built-ins keyed by name
      (`build`/`plan`/`explore`/`code-reviewer`) with the tool allowlists, default modes, and
      (code-reviewer) the `bash(git *)` allow rule from ADR-0003 §5. Unit-tested.
- [x] `load_agent("nope")` raises a clear error listing the four available agent names. Unit-tested.
- [x] The four `*.md` files load via the installed package (packaged data), not a hard-coded repo
      path — a test loads them through the package.
- [x] **Working looks like:** `load_agent("plan").mode is PermissionMode.PLAN` and its `tools`
      contain no `write`/`edit`/`bash`; `load_agent("code-reviewer").allow` contains `bash(git *)`.
- [x] `make ci` green, 0 warnings; `tests/unit/decode/agents/` mirrors `src/decode/agents/`.

## Out of scope
- Wiring the active agent into the running agent (prompt + tool restriction + rules + mode) — task 020.
- A `model` frontmatter field (step 3 — providers).
- Subagent spawning (not this milestone).

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §5. Round-2 locks folded in: NO `model` field (step 3); `AgentDef` carries
optional agent-scoped `allow`/`deny` rules (reusing task 018's parser) so code-reviewer's `bash(git *)`
lives in the catalog, not the user's settings.json. Depends on 018 for the rule parser.

### [SWE] 2026-06-25 18:40 — Implementation

**Files modified**
- `pyproject.toml` + `uv.lock` — declared `pyyaml>=6.0.3` as a direct runtime dep (`uv add pyyaml`).
- `src/decode/tools/orchestration.py` — NEW: the three orchestration tool-name constants
  (`enter_plan_mode`/`exit_plan_mode`/`sleep`) + `ORCHESTRATION_TOOL_NAMES` frozenset. Names only;
  task 021 adds the functions.
- `src/decode/tools/__init__.py` — exposed `KNOWN_TOOL_NAMES` (registry names plus orchestration
  names) so the catalog validates allowlists regardless of task ordering.
- `src/decode/entities/agent_def.py` — NEW: the `AgentDef` entity (frozen+slotted), validated:
  unknown tool / empty name / empty prompt / malformed allow|deny rule each raise a clear error;
  parses allow/deny strings into `Rule` tuples via task-018's `parse_rule`. NO `model` field.
- `src/decode/agents/__init__.py`, `agents/loader.py` — NEW: `load_builtin_agents()`,
  `load_agent(name)`, `parse_agent_file(text)`. Files read via `importlib.resources` (packaged data),
  not a repo path. Unknown frontmatter keys ignored (forward-compatible).
- `src/decode/agents/builtin/{__init__.py,build.md,plan.md,explore.md,code-reviewer.md}` — NEW: the
  four built-in personas (frontmatter + system-prompt body). code-reviewer carries `allow:
  [bash(git *)]`.
- `tests/unit/decode/entities/test_agent_def.py`, `tests/unit/decode/agents/{__init__.py,test_loader.py}`
  — NEW unit tests (mirror src/ 1:1).

**Tests**
- Unit: 467 passing, 0 failing (23 new: 10 `test_agent_def.py` + 13 `test_loader.py`).
- Integration: 1 passing (capstone) — no infra changes, but re-ran to confirm no regression.
- `make ci`: green (uv lock --check current · format-check · lint-check · 468 tests · 0 warnings).

**Acceptance criteria**
- [x] pyyaml declared + `uv lock --check` current — `pyproject.toml` L35, CI top output.
- [x] `AgentDef` validation — `tests/unit/decode/entities/test_agent_def.py` (unknown tool / bad
  mode via loader / empty name / empty prompt / malformed allow+deny).
- [x] `load_builtin_agents()` returns the four built-ins with tools/modes/git-allow —
  `tests/unit/decode/agents/test_loader.py::test_load_builtin_agents_returns_the_four_personas` and
  the per-agent tests.
- [x] `load_agent("nope")` lists available names —
  `test_loader.py::test_load_agent_unknown_name_lists_the_available_agents`.
- [x] Packaged-data load (not repo path) —
  `test_loader.py::test_builtin_files_are_packaged_data_not_a_repo_path` + verified by installing the
  built wheel into a fresh venv and loading the catalog from site-packages.
- [x] Working-looks-like (plan PLAN/no mutators; code-reviewer git allow) — verified by the per-agent
  tests and a live `uv run python` exercise.
- [x] `make ci` green, 0 warnings; `tests/unit/decode/agents/` mirrors `src/decode/agents/`.

**Evidence**
```
$ make ci
uv lock --check
Resolved 166 packages in 4ms
make format-check  → 90 files already formatted
make lint-check    → All checks passed!
make test          → 468 passed in 6.10s
```
```
$ uv run python -c "from decode.agents import load_agent; ..."
plan.mode is PLAN: True
plan has no write/edit/bash: True
reviewer.allow: ('bash(git *)',)
reviewer.allow_rules: (Rule(tool_name='bash', pattern='git *'),)
build.mode: default | tool count: 12
load_agent(nope) -> no such agent 'nope'; available agents: build, code-reviewer, explore, plan
```
```
# installed-wheel packaged-data check (proves importlib.resources, not a repo path)
$ uv build --wheel && uv pip install <wheel> into fresh venv
decode loaded from: .../site-packages/decode
built-ins from installed wheel: ['build', 'code-reviewer', 'explore', 'plan']
code-reviewer allow: ('bash(git *)',)
```

**Notes**
- Hatchling ships the `builtin/*.md` files in the wheel **by default** (it includes every file under
  the package dir) — verified against `uv build`; no `force-include` needed, so `pyproject.toml`'s
  hatch config is unchanged.
- `KNOWN_TOOL_NAMES` lives in `decode.tools` (registry names plus the orchestration names) — this is
  the only new public surface beyond the catalog itself. Orchestration constants are names-only;
  task 021 registers the actual functions, task 020 wires the active agent into the running loop.
- Pure load + validate, as scoped — NO wiring into `AgentDeps`/the agent (task 020), NO `model`
  field (step 3), NO orchestration tool bodies (task 021).
- DID NOT commit — handing to the Tester first per the SWE role.

### [Tester] 2026-06-25 19:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 90 files; `ruff check` all passed; pre-commit gate green)
- `uv lock --check`: PASS (current — resolved 166 packages, no drift)
- Unit tests: 467 passed / 0 failed (23 new: 10 `test_agent_def.py` + 13 `test_loader.py`)
- Integration tests: 1 passed / 0 failed (capstone)
- Warnings: 0 (`filterwarnings=["error"]` would have errored on any)

**E2E adversarial pass**
- Happy path: `load_builtin_agents()` → 4 personas; `plan.mode is PLAN` True; `plan` excludes write/edit/bash; `code-reviewer.allow == ('bash(git *)',)`; `build` has 12 tools, mode=default (PASS)
- Break path 1 (validation: unknown tool via frontmatter): `parse_agent_file(... tools:[read,frobnicate])` → `ValueError: agent 'bad' lists unknown tool(s) ['frobnicate']; known tools: ...` — names the bad tool (PASS)
- Break path 2 (validation: bad mode): `mode: turbo` → `ValueError: unknown mode 'turbo'; valid modes: default, plan, edit, bypass` (PASS)
- Break path 3 (validation: empty name / empty prompt): whitespace name → `'name' is required and must be a non-empty string`; whitespace body → `agent 'demo' must have a non-empty prompt` (PASS)
- Break path 4 (validation: malformed allow/deny rule): `allow:['bash(']` → `agent 'demo' has a malformed permission rule 'bash(': malformed rule (unbalanced parens)`; `deny:['(pattern)']` → `... rule is missing a tool name` (PASS)
- Break path 5 (structural edges): empty string, whitespace-only, unclosed frontmatter, frontmatter-as-list/scalar/None, missing description/tools/mode, tools-not-a-list, tools-with-non-string, allow-not-a-list → each a clear `ValueError` (PASS)
- Break path 6 (boundary: empty tools list `[]`) → accepted (agent with zero tools); not forbidden by ADR/AC — noted as a follow-up, non-blocking
- Break path 7 (malformed YAML frontmatter): raises raw `yaml.YAMLError`/`ScannerError`, NOT a context-wrapped `ValueError` — non-blocking note (only reachable if a bundled file becomes malformed; never user input)
- Break path 8 (concurrency: 200 loads × 16 threads): all consistent; no shared mutable state — fresh dict per call, mutating a returned dict does not bleed into the next call (PASS)
- Break path 9 (unicode / 200KB prompt body): loads cleanly (PASS)
- `load_agent("nope")` → `ValueError: no such agent 'nope'; available agents: build, code-reviewer, explore, plan` (PASS)

**Packaged-data verification (the criterion most likely to be faked) — verified hard**
- `uv build --wheel` → `unzip -l *.whl | grep builtin` shows all four `builtin/*.md` (+ `__init__.py`) inside the wheel.
- Installed the wheel into a FRESH isolated venv, ran from cwd `/` (no repo on path): `decode.__file__` resolves under `site-packages`; `load_builtin_agents()` returns the 4 personas; `importlib.resources.files("decode.agents.builtin")` root is under `site-packages`. Loader uses `importlib.resources` only — `grep` confirms NO `Path(__file__)`/repo-walking in `agents/`.

**Acceptance criteria**
- [x] PASS — `pyyaml` declared direct dep + `uv lock --check` current — `pyproject.toml:35` (`pyyaml>=6.0.3`), `uv.lock` requires-dist entry, `uv lock --check` exit 0.
- [x] PASS — `AgentDef` validation (unknown tool / bad mode / empty name+prompt / malformed allow+deny) — `tests/unit/decode/entities/test_agent_def.py` (10 tests pass) + adversarial break paths 1-5 above.
- [x] PASS — `load_builtin_agents()` returns the four built-ins with tools/modes + code-reviewer `bash(git *)` — `test_loader.py::test_load_builtin_agents_returns_the_four_personas` + per-agent tests; live load confirmed.
- [x] PASS — `load_agent("nope")` lists available names — `test_loader.py::test_load_agent_unknown_name_lists_the_available_agents`; live run confirmed.
- [x] PASS — `.md` files load via installed package (packaged data) — `test_loader.py::test_builtin_files_are_packaged_data_not_a_repo_path` + independent fresh-venv wheel install from `site-packages` (above).
- [x] PASS — Working-looks-like: `load_agent("plan").mode is PermissionMode.PLAN` and no write/edit/bash; `load_agent("code-reviewer").allow` contains `bash(git *)` — confirmed live and by `test_plan_agent_is_plan_mode_and_read_only` / `test_code_reviewer_carries_the_git_allow_rule`.
- [x] PASS — `make ci` green, 0 warnings; `tests/unit/decode/agents/` mirrors `src/decode/agents/` — full gate green; test dir mirrors src 1:1 (`tests/unit/decode/agents/test_loader.py`, `tests/unit/decode/entities/test_agent_def.py`).

**Evidence**
```
$ make unit-tests        → 467 passed in 5.77s
$ make integration-tests → 1 passed in 1.40s
$ uv lock --check        → Resolved 166 packages (exit 0)
$ unzip -l dist/*.whl | grep builtin
  decode/agents/builtin/{__init__.py,build.md,code-reviewer.md,explore.md,plan.md}
$ (fresh venv, cwd=/) decode loaded from: .../site-packages/decode/__init__.py
  built-ins from installed wheel: ['build', 'code-reviewer', 'explore', 'plan']
  PACKAGED-DATA CHECK: PASS (loaded from installed wheel, not a repo path)
```

**Other issues found** (non-blocking — not in the acceptance criteria)
- `parse_agent_file` lets a malformed-YAML frontmatter raise a raw `yaml.YAMLError`/`ScannerError` instead of a context-wrapped `ValueError`; `load_builtin_agents`' `except ValueError` would not add the `invalid built-in agent file '...'` context for that case. Only reachable if a *bundled* file becomes malformed (authored data, never user input) — all four ship well-formed. Worth a one-line `except yaml.YAMLError` in a follow-up.
- An empty `tools: []` allowlist is accepted (agent with zero callable tools). Not forbidden by ADR-0003 §5 or the ACs; flag for a possible future guard.
- `code-review` plugin (enabled in `.claude/settings.json`) is PR-scoped (`gh pr diff`/`pr comment`) and bails on step-1 eligibility for an uncommitted local branch with no PR — not applicable here. Performed its substance manually: CLAUDE.md/AGENTS.md-adherence audit, diff bug-scan, comments/conventions check — all clean (no `print()`, full type annotations incl. `-> None`, frozen+slotted entity, logger used, diff surgical with no unrelated files).

**VERDICT: PASS**
