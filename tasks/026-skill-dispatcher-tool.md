---
id: 026-skill-dispatcher-tool
feature: skills
status: done
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

- [x] `SKILL_TOOL_NAME == "skill"`; `skill` takes `(ctx, name)` only (no `args` parameter).
- [x] `skill(ctx, "commit")` returns the **built-in** commit skill body (verified against
      `load_builtin_skills()["commit"].body`). Unit-tested (direct call, hand-built `RunContext` like
      `test_orchestration`'s direct harness).
- [x] `skill(ctx, "nope")` raises `pydantic_ai.ModelRetry` whose message **lists the available skill
      names**. Unit-tested.
- [x] `skill` respects the project override: with `<cwd>/.decode/skills/commit.md` present,
      `skill(ctx, "commit")` returns the **project** body. Unit-tested.
- [x] **Ungated, loop-driven:** a `FunctionModel` + real `build_agent()` test scripting a
      `skill("commit")` call returns the body as the tool result and emits **no** `PermissionRequested`
      event (callable even when the gate is in plan mode). Mirrors `test_orchestration.py`'s harness.
- [x] **Invariant — dispatcher ungated, induced action gated:** a loop-driven test scripting
      `skill("commit")` *then* a mutating tool call (a `bash`/`write`) shows the `skill` call produced
      **no** `PermissionRequested` while the subsequent mutating call **does** reach the gate
      (PermissionRequested emitted / denied in plan mode). This pins ADR-0004 §7 with `commit` as the
      worked example. Unit-tested.
- [x] `skill` is in `TOOL_SPECS` with `kind == ToolKind.OTHER`; `"skill" in KNOWN_TOOL_NAMES`; and
      `load_builtin_agents()` validates cleanly with `skill` now in all four agents' `tools` (every
      built-in agent lists `skill`). Unit-tested.
- [x] An agent whose `tools` omits `skill` hides the dispatcher (the `prepare=` callback returns
      `None`) — assert via the existing restriction path; all four built-ins include it.
- [x] Updated agent-catalog tests reflect the new tool (no exact-count assertion fails); `make ci`
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

### [SWE] 2026-06-26 14:10 — Implementation

**Files modified**
- `src/decode/tools/skills.py` — new ungated `skill(ctx, name)` dispatcher; `SKILL_TOOL_NAME = "skill"`;
  looks `name` up as a dict key in `load_skills(ctx.deps.cwd)` and returns the matched skill's `body`;
  unknown `name` → `pydantic_ai.ModelRetry` listing available skills. Never raises `ApprovalRequired`.
- `src/decode/tools/registry.py` — registered `ToolSpec(name="skill", func=skill, kind=ToolKind.OTHER)`
  in the ungated group; extended the module docstring's ungated-tools list to include `skill`.
- `src/decode/agents/builtin/{build,plan,explore,code-reviewer}.md` — added `skill` to each `tools:` list.
- `tests/unit/decode/tools/test_skills.py` — new, mirrors `src/decode/tools/skills.py` (14 tests).
- `tests/unit/decode/tools/test_registry.py` — added `skill` to the expected-tools set, kind map,
  `tool_kind`, and registered-on-agent set.
- `tests/unit/decode/agents/test_loader.py` — added `skill` to the build/plan/explore/code-reviewer
  expected tool sets.

**Tests**
- Unit: 622 passing, 0 failing (`make unit-tests`); `test_skills.py` adds 14.
- Integration: 1 passing (M1 capstone, `make integration-tests`); `make ci` green, 0 warnings.

**Acceptance criteria**
- [x] `SKILL_TOOL_NAME == "skill"`, `(ctx, name)` only — `test_skills.py::test_skill_tool_name_is_stable`,
      `::test_skill_takes_ctx_and_name_only_no_args`.
- [x] built-in commit body — `::test_skill_returns_the_builtin_commit_body`.
- [x] unknown name → `ModelRetry` listing names — `::test_skill_unknown_name_raises_model_retry_listing_available_names`.
- [x] project override wins — `::test_skill_respects_a_project_override`.
- [x] ungated loop-driven (incl. plan mode) — `::test_skill_through_the_loop_returns_the_body_and_is_ungated`,
      `::test_skill_is_callable_in_plan_mode`.
- [x] invariant (dispatcher ungated, induced action gated) — `::test_skill_ungated_but_the_induced_commit_is_gated_in_default_mode`
      (skill emits no `PermissionRequested`; the induced `bash` git commit emits exactly one, `name == "bash"`),
      `::test_skill_ungated_but_the_induced_commit_is_denied_in_plan_mode` (plan mode denies the commit).
- [x] in `TOOL_SPECS` (`OTHER`) + `KNOWN_TOOL_NAMES` + all four agents — `::test_skill_is_registered_as_an_other_kind_spec`,
      `::test_skill_is_a_known_tool_name`, `::test_all_four_builtin_agents_list_skill`.
- [x] agent omitting `skill` hides the dispatcher — `::test_agent_omitting_skill_hides_the_dispatcher`.
- [x] catalog tests updated, `make ci` green — test_loader.py / test_registry.py updated.

**Evidence**
```
$ make ci
... 623 passed in 6.79s ...

$ DECODE_GEMINI_API_KEY=unused uv run python  (runtime smoke, no network)
SKILL_TOOL_NAME = skill
signature       = ['ctx', 'name']
in TOOL_SPECS   = True
in KNOWN_TOOL_NAMES = True
agents listing skill = {'build': True, 'code-reviewer': True, 'explore': True, 'plan': True}
skill('commit') -> "You commit the work in the **current working tree** autonomously: ..."
skill('nope')   -> ModelRetry: No skill named 'nope'. Available skills: commit, review-diff.
project override skill('commit') -> 'Project commit ritual.'
E2E OK
```

**Notes**
- The invariant is pinned two ways with `commit` as the worked example: default mode shows the
  induced `bash` git commit emits exactly one `PermissionRequested` (name `bash`) while the skill
  dispatch emits none; plan mode shows the induced commit is denied (reason reaches the model) while
  the dispatcher still returns its body. Tester: scrutinize these two
  (`test_skill_ungated_but_the_induced_commit_is_*`).
- `name` is used as a dict key only (never interpolated into a path/shell command), per the task-025
  Tester forward-note; the `ModelRetry`-on-unknown handles a bad name safely.
- `KNOWN_TOOL_NAMES` derives `skill` automatically from the registered spec (`frozenset(TOOL_KIND)`),
  so the four agents validate without touching `tools/__init__.py`.
- No commit/push — handing off to the Tester.

### [Tester] 2026-06-26 15:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 104 files clean; `ruff check` clean)
- Unit tests: 622 passed / 0 failed (`make unit-tests`); `test_skills.py` = 14 passed
- Integration tests: 1 passed / 0 failed (M1 capstone)
- `make ci`: 623 passed (lock check + format + lint + full suite)
- Warnings: 0 (`filterwarnings=["error"]` in effect; none surfaced)

**E2E adversarial pass** (direct-call harness + independent loop-driven triangulation)
- Happy path: `skill(ctx,"commit")` → built-in commit body; `skill(ctx,"review-diff")` → its body (PASS)
- Unknown name: `skill(ctx,"nope")` → `ModelRetry` "No skill named 'nope'. Available skills: commit, review-diff." — message LISTS both names (PASS)
- Boundary (empty/whitespace/case): `""`, `"   "`, `"\t\n"`, `"COMMIT"`, `"commit "`, `" commit"`, `"commit\n"` → clean `ModelRetry`, no crash (PASS)
- Hostile (path traversal / shell / injection): `"../../etc/passwd"`, `"commit; rm -rf /"`, `"commit && echo hi"`, `"$(whoami)"`, "`id`", `"commit\x00"`, `"%2e%2e%2fcommit"`, `"commit'"`, `'commit"'` → all clean `ModelRetry`; `/etc/passwd` content NOT leaked; confirms `name` is a dict key only, never a path/shell string (025-Tester forward-note honored) (PASS)
- Dict-internal names: `"get"`, `"items"`, `"__class__"`, `"keys"`, `"update"` → `ModelRetry` (proves `catalog.get(name)`, not attribute access) (PASS)
- Unicode / non-ASCII: `"commıt"`, fullwidth `"ｃｏｍｍｉｔ"`, zero-width/BOM suffixes, `"🚀"` → clean `ModelRetry` (PASS)
- Override + project-only: `<cwd>/.decode/skills/commit.md` → `skill("commit")` returns PROJECT body; project-only `deploy` dispatchable; a malformed project skill is skipped (WARNING) and the dispatcher still works (PASS)
- **Invariant (ADR-0004 §7), independently triangulated with `write` (SWE used `bash`):**
  - `skill("commit")` alone, DEFAULT and PLAN → 0 `PermissionRequested`, body returned (ungated)
  - `skill("commit")` then `write`, DEFAULT → exactly 1 `PermissionRequested` named `write`; file not created (denied); skill emitted none
  - `skill("commit")` then `write`, PLAN → 0 prompts (auto-deny), denial reason mentions "plan mode"; verified the commit skill body itself contains NO "plan mode" string, so the SWE's `any("plan mode" in r)` assertion is genuinely testing the bash denial, not the skill body — **non-tautological** (PASS)
  - SWE's two invariant tests scrutinized: `len(prompts)==1` + `prompts[0].name=="bash"` would fail if `skill` were gated (would be 2 prompts), so it genuinely pins ungated-dispatch; the plan-mode test's "plan mode" match comes from the bash denial result, not the body — both genuine (PASS)

**Acceptance criteria**
- [x] PASS — `SKILL_TOOL_NAME == "skill"`, `(ctx, name)` only — `skills.py:37,42`; `test_skill_tool_name_is_stable`, `test_skill_takes_ctx_and_name_only_no_args`
- [x] PASS — `skill(ctx,"commit")` returns built-in body — adv direct call + `test_skill_returns_the_builtin_commit_body`
- [x] PASS — unknown name → `ModelRetry` listing names — adv call lists `commit, review-diff`; `test_skill_unknown_name_raises_model_retry_listing_available_names`
- [x] PASS — project override wins — adv call returns "PROJECT BODY HERE."; `test_skill_respects_a_project_override`
- [x] PASS — ungated loop-driven incl plan mode — independent triangulation 0 prompts both modes; `test_skill_through_the_loop_returns_the_body_and_is_ungated`, `test_skill_is_callable_in_plan_mode`
- [x] PASS — invariant (dispatcher ungated, induced action gated) — triangulated with `write`; SWE bash tests confirmed non-tautological
- [x] PASS — in `TOOL_SPECS` (`OTHER`) + `KNOWN_TOOL_NAMES` + all four agents validate — `registry.py:133`; `test_skill_is_registered_as_an_other_kind_spec`, `test_skill_is_a_known_tool_name`, `test_all_four_builtin_agents_list_skill`
- [x] PASS — agent omitting `skill` hides it (`prepare=` → None) — `test_agent_omitting_skill_hides_the_dispatcher`
- [x] PASS — catalog tests updated (build 12→13 confirmed: `load_agent("build")` lists 13 tools incl `skill`), `make ci` green 0 warnings, `test_skills.py` mirrors `src/decode/tools/skills.py`

**Evidence**
```
$ make ci
... 623 passed in 6.74s ...   (lock check + format-check + lint-check + unit + integration; 0 warnings)
$ build:13 plan:9 explore:7 code-reviewer:8 tools — all four list skill, validate cleanly
```

**Other issues found**
- PASS-with-note (non-blocking): `skill` calls `load_skills(ctx.deps.cwd)` on every invocation (re-reads built-in package files + rescans the project skills dir per call). Correct and fine for lazy v1 disclosure / the teaching-codebase simplicity rule; a possible future follow-up if the dispatcher ever lands on a hot path. No defect.
- Note: the dispatcher does not strip `name` before lookup (e.g. `"commit "` → `ModelRetry`), while catalog keys are stripped. This is correct by design — the model passes the exact catalog name, and a stray-whitespace name yields a guiding `ModelRetry` rather than a crash. Not a defect.

**VERDICT: PASS**
