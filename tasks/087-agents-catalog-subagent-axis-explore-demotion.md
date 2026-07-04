---
id: 087-agents-catalog-subagent-axis-explore-demotion
feature: explore-subagents
status: done
---

# Catalog primary/subagent axis + demote Explore to subagent-only

Tags: `agents`, `catalog`
Depends on: None (ADR-0013 + glossary land in the grooming commit)
Blocks: #088

## Scope

Add a minimal **primary/subagent axis** to the Agents Catalog and demote **explore** to a
subagent-only persona, so a later task can spawn it via the `agent` tool while it can no longer be
selected as the main agent (ADR-0013 §3). No `agent` tool yet — this task only touches the catalog,
its loader, the explore persona file, and the two selection surfaces.

- **`AgentDef` gains `subagent: bool = False`** (`entities/agent_def.py:30-85`) — a deliberately
  non-colliding name (`AgentDef.mode` at :47 is the permission mode). Frozen/slotted like the rest;
  default `False`, so every existing persona stays a primary with no other change. No validation
  beyond "is a bool".
- **Loader parses an optional `subagent` bool** (`agents/loader.py:70-90` `parse_agent_file`): add an
  `_optional_bool(meta, key)` helper mirroring `_optional_str_tuple` (:129-136) — returns `False` when
  absent, raises a clear `ValueError` when present-but-not-a-bool. Unknown frontmatter keys stay
  ignored (forward-compat, ADR-0003 §5). Pass `subagent=` into the `AgentDef(...)` call.
- **`explore.md` demotion** (`agents/builtin/explore.md`): set `tools:` to **exactly**
  `read`, `glob`, `grep`, `lsp` (drop `web_fetch`, `todo_write`, `ask_user`, `skill`); add
  `subagent: true`; keep `mode: default`. Rewrite the body to (a) drop the removed tools, (b) state
  plainly that its **final message IS the compressed report** handed back to the calling agent (the
  subagent contract 088 relies on — ADR-0013 §8), (c) keep the read-only / go-to-source / cite-files /
  trace-the-call-path guidance. Remove the `ask_user` clarify step (a subagent cannot ask — ADR-0013 §2).
- **Reject subagents at both selection surfaces** via one shared helper
  `load_primary_agent(name)` in `agents/loader.py`: it loads by name and raises `ValueError` — listing
  only the **primary** agent names (personas with `subagent is False`, i.e. build / code-reviewer /
  plan) — when the name is unknown *or* the loaded persona has `subagent is True`. Wire it at:
  - the CLI startup `--agent` guard (`cli.py:485-490`, currently `load_agent(agent)`); and
  - `select_agent` (`agents/select.py:33-57`, used by the mid-session `/agent` command at
    `tui/app.py:342-366`) — call `load_primary_agent` in place of `load_agent`, so the load (and its
    rejection) still happens **before** any `deps`/`gate` mutation.

## Acceptance Criteria

- [x] `AgentDef` carries `subagent: bool = False`; constructing a persona without the key yields
  `subagent is False`. Covered by a new case in `tests/unit/decode/entities/test_agent_def.py`
  (near :40-53). — `test_agent_def_subagent_defaults_to_false` / `test_agent_def_carries_the_subagent_flag_when_set`.
- [x] `load_agent("explore").subagent is True` and `explore.tools == ("read", "glob", "grep", "lsp")`
  exactly — no `ask_user` / `skill` / `web_fetch` / `todo_write`. Update
  `tests/unit/decode/agents/test_loader.py:64-70` (`test_explore_agent_is_read_only_default_mode`) to
  the new toolset + assert `subagent is True`; assert the other three built-ins have `subagent is
  False`. — `test_explore_agent_is_a_read_only_default_mode_subagent` + `test_only_explore_is_a_subagent`.
- [x] The loader raises a clear `ValueError` when `subagent` is present but not a bool (e.g. a string),
  naming the offending file — mirroring `_optional_str_tuple`'s validation. New loader test. —
  `_optional_bool` helper + `test_parse_agent_file_rejects_a_non_bool_subagent`.
- [x] All four built-ins still load and validate (`load_builtin_agents()` returns build / plan /
  explore / code-reviewer); `_BUILTIN_NAMES` at `test_loader.py:21` unchanged. —
  `test_load_builtin_agents_returns_the_four_personas` (unchanged, passing).
- [x] `decode --agent explore` exits non-zero with one friendly stderr line naming only the **primary**
  agents (build, code-reviewer, plan) — no traceback. `--agent build|plan|code-reviewer` still start,
  and `--agent bogus` still errors. New cases in `tests/unit/decode/test_cli.py`. —
  `test_cli_with_the_explore_subagent_exits_nonzero_listing_primaries` + `test_cli_each_primary_agent_still_starts`; verified live (see Evidence).
- [x] `/agent explore` mid-session renders a friendly inline rejection (primaries only) and leaves
  `deps`/`gate` untouched; the REPL stays alive. Update
  `tests/unit/decode/tui/test_app_e2e.py:601-611` (was asserting `agent: explore`); `/agent plan` etc.
  still switch. — `test_run_app_agent_slash_switches_and_rejections_stay_alive`.
- [x] `select_agent("explore", deps=…, gate=…)` raises `ValueError` (primaries only) and does not
  mutate `deps`/`gate`. Update `tests/unit/decode/agents/test_select.py:105`. —
  `test_select_explore_subagent_is_rejected_and_leaves_state_untouched` (+ rule-replacement test now switches to `build`).
- [x] The default agent is unchanged (`cli.py:80` `_DEFAULT_AGENT == "build"`); a fresh `decode` still
  starts on build. — untouched; `test_cli_defaults_to_the_build_agent` passing.
- [x] `make pre-commit` (format + lint + unit) green; `filterwarnings=["error"]` clean. —
  repo-wide `make format-check` + `make lint-check` green; full unit suite 1426 passing (see Evidence).

## Out of scope

- The `agent` tool, the child runner, and the new settings (#088).
- Granting `agent` to build/plan/code-reviewer `tools:` lists (#088 — it requires the tool registered).
- A `subagent_type` selection parameter (only one subagent exists — ADR-0013 §3, out of scope).
- README / AGENTS.md prose (#089); the capstone (#090).

## Log

### [SWE] 2026-07-04 23:53 — Implementation

**Files modified**
- `src/decode/entities/agent_def.py` — add `subagent: bool = False` (frozen/slotted; placed after `deny`, before the `init=False` rule fields); doc the primary/subagent axis. No new validation (trusted like `mode`).
- `src/decode/agents/loader.py` — new `_optional_bool(meta, key)` helper (mirrors `_optional_str_tuple`: absent → `False`, present-but-not-bool → `ValueError`); wire `subagent=` into the `AgentDef(...)` call; new `load_primary_agent(name)` (rejects unknown names AND subagents, listing only primaries); doc-refresh on `load_agent` (no longer the selection guard).
- `src/decode/agents/builtin/explore.md` — demotion: `tools:` → exactly `read, glob, grep, lsp`; add `subagent: true`; keep `mode: default`; body rewritten (final message IS the compressed report to the calling agent; dropped web_fetch/todo_write/skill mentions and the ask_user clarify step; kept read-only / go-to-source / cite-files / trace-call-path).
- `src/decode/agents/select.py` — `select_agent` now calls `load_primary_agent` (load + rejection still precede any `deps`/`gate` mutation).
- `src/decode/cli.py` — `--agent` startup guard uses `load_primary_agent`; `--agent` help text drops explore (`build / plan / code-reviewer`).
- `src/decode/tui/app.py` — `_AGENT_USAGE` (the `/agent` no-arg hint) drops explore.
- `tests/unit/decode/entities/test_agent_def.py` — subagent default-False + carries-True cases.
- `tests/unit/decode/agents/test_loader.py` — rewrote the explore case (exact toolset + `subagent is True`); new `test_only_explore_is_a_subagent`, three `load_primary_agent` cases, three `parse_agent_file` subagent cases.
- `tests/unit/decode/agents/test_select.py` — rule-replacement test switches to `build` (was explore); new explore-rejection-leaves-state-untouched case.
- `tests/unit/decode/test_cli.py` — parametrized each-primary-starts case; explore-subagent-rejected case.
- `tests/unit/decode/tui/test_app_e2e.py` — `/agent` test now switches to a primary (`plan`) and asserts inline rejection of both the explore subagent and an unknown name; REPL stays alive.
- `tests/unit/decode/tools/test_skills.py` — cross-cutting: `test_all_four_builtin_agents_list_skill` → `test_all_primary_builtin_agents_list_skill` (primaries list `skill`; explore deliberately excludes it — ADR-0013 §2). This was the one non-obvious ripple the full suite surfaced.

**Tests**
- Unit: 1426 passing, 0 failing — full `make pre-commit` (format-check + lint-check + unit) green, `filterwarnings=["error"]` clean. Output below.
- Integration: N/A — no infra changes (catalog/loader/CLI/TUI only). The M1 capstone runs inside the unit gate and passed.

**Acceptance criteria** — all 9 checked above; each maps to a named test (AC lines annotated with the verifying test). #5 also verified live (Evidence).

**Evidence**
```
$ make pre-commit
... (ruff format --check: 171 files already formatted; ruff check: All checks passed!) ...
======================= 1426 passed in 596.44s (0:09:56) =======================
```
Live e2e (real CLI; dummy GEMINI_API_KEY so the provider guard passes and the agent guard fires):
```
$ uv run decode --help | grep -A1 -- --agent
  --agent NAME   Start with this agent persona (build / plan / code-reviewer).

$ GEMINI_API_KEY=test uv run decode --agent explore </dev/null ; echo exit=$?
Decode: agent 'explore' is a subagent and cannot be selected as a main agent; available agents: build, code-reviewer, plan
exit=1

$ GEMINI_API_KEY=test uv run decode --agent bogus </dev/null ; echo exit=$?
Decode: no such agent 'bogus'; available agents: build, code-reviewer, plan
exit=1

# --agent build reaches the REPL banner ("Decode - gemini:gemini-2.5-flash - type a line; /quit exits.")
# — persona selected, loop launched — before the known prompt_toolkit non-TTY crash when stdin is /dev/null
# (crash is entirely in prompt_toolkit's vt100 add_reader; a real TTY / the CliRunner + app_e2e tests exit clean).
```

**Notes**
- Rejection messages are differentiated for clarity (teaching codebase): unknown name → `no such agent {name!r}; available agents: …`; subagent → `agent {name!r} is a subagent and cannot be selected as a main agent; available agents: …`. Both raise `ValueError` and list only the primaries (sorted: `build, code-reviewer, plan`), satisfying the ADR-0013 §3 "primaries-only line".
- Scope note (flag for review): beyond the task's named files I also updated two user-facing selection hints that advertised explore — `cli.py` `--agent` help and `app.py` `_AGENT_USAGE` — plus the `test_skills.py` cross-cutting invariant. All three are direct consequences of the demotion (a hint offering explore would advertise a now-rejected choice); no test asserted the old hint strings. Left the explore `description:` frontmatter unchanged (still accurate; #088 wires the `agent` tool description).
- `_optional_bool` uses `isinstance(value, bool)`, which correctly rejects a YAML int like `1` (bool is a strict subtype of int). A YAML `true`/`false` is the only accepted form.
- Tester probes worth a look: (a) the `select_agent` rejection truly leaves `deps`/`gate` untouched (mutation happens strictly after the load); (b) the mid-session `/agent explore` renders inline and the REPL survives (covered by the real-`run_app` pipe test); (c) explore still loads as one of the four built-ins (`_BUILTIN_NAMES` unchanged) — it is demoted, not removed.
- No commit made — handing to the Tester first, per workflow.

### [Tester] 2026-07-05 00:28 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` → 171 files formatted; `make lint-check` → all checks passed; unit gate below)
- Unit tests: 1426 passed / 0 failed (`make unit-tests`, 750s)
- Integration tests: 96 passed / 1 failed (`make integration-tests`, 1361s) — the 1 failure is an **environmental docker-daemon flake**, not a regression (see below)
- Warnings: 0 (`filterwarnings=["error"]` at `pyproject.toml:108`; 1426 passing with warnings-as-errors ⇒ zero warnings)

**E2E adversarial pass** (live `uv run decode`, real CLI, dummy `GEMINI_API_KEY=test`)
- Happy path — `decode --agent build|plan|code-reviewer </dev/null` → each renders the REPL banner `Decode - gemini:gemini-2.5-flash - type a line; /quit exits.` on stdout, no rejection on stderr (guard passed). Exit 1 is the pre-existing prompt_toolkit non-TTY `OSError [Errno 22]` in `_add_reader` (fires *after* the banner, `/dev/null` stdin), unrelated to this task. Default `decode` (no flag) → same banner (starts on build). (PASS)
- Break path 1 (subagent selection) — `decode --agent explore` → exit 1, stderr `Decode: agent 'explore' is a subagent and cannot be selected as a main agent; available agents: build, code-reviewer, plan`, 0 tracebacks. (PASS)
- Break path 2 (unknown name) — `decode --agent bogus` → exit 1, stderr `Decode: no such agent 'bogus'; available agents: build, code-reviewer, plan`, 0 tracebacks. (PASS)
- Break path 3 (frontmatter abuse) — `parse_agent_file` with `subagent: "yes"` (str), `subagent: 1` (int), `subagent: 0` (int) each → `ValueError: 'subagent' must be a boolean when present` (not a silent False); via `load_builtin_agents` the wrapper names the file: `invalid built-in agent file 'explore.md': 'subagent' must be a boolean when present`. `subagent: yes` (unquoted) → `True` (standard YAML 1.1 bool, correct). Unknown key `futurekey` → ignored (forward-compat intact). (PASS)
- Break path 4 (mid-session state integrity) — select code-reviewer, then `select_agent("explore")` → raises primaries-only ValueError; persona stays `code-reviewer`, gate mode unchanged, agent rules unchanged (`rules_before == rules_after` True); `select_agent("nope")` also rejected without mutation; a subsequent `select_agent("plan")` still switches (persona=plan, mode=plan). deps/gate genuinely untouched by a rejection. (PASS)
- Stale-reference sweep — grepped src/docs for selectable-explore claims: `cli.py --agent` help and `app.py _AGENT_USAGE` both drop explore; `SlashCompleter` never completes the `/agent` arg (only command tokens), so it can't offer explore. Remaining "explore" hits are correct subagent docstrings / immutable historical ADRs (0003 marked superseded, 0004 historical) / the already-groomed glossary. (PASS)

**Acceptance criteria**
- [x] PASS — `AgentDef.subagent: bool = False`; absent key ⇒ False — `entities/agent_def.py:55`; `test_agent_def_subagent_defaults_to_false` + `test_agent_def_carries_the_subagent_flag_when_set` pass; live `_optional_bool({}, "subagent") → False`.
- [x] PASS — `load_agent("explore").subagent is True`, `explore.tools == ("read","glob","grep","lsp")` exact, no ask_user/skill/web_fetch/todo_write; others `subagent is False` — live catalog probe + `test_explore_agent_is_a_read_only_default_mode_subagent`, `test_only_explore_is_a_subagent`.
- [x] PASS — loader raises clear `ValueError` naming the file on non-bool `subagent` — `loader.py:165` `_optional_bool`; `test_parse_agent_file_rejects_a_non_bool_subagent`; wrapper `load_builtin_agents` names `explore.md` (live probe).
- [x] PASS — all four built-ins load; `_BUILTIN_NAMES` at `test_loader.py:21` unchanged (`{build, plan, explore, code-reviewer}`) — live `load_builtin_agents()` → `['build','code-reviewer','explore','plan']`.
- [x] PASS — `decode --agent explore` exits non-zero, friendly stderr, primaries only, no traceback; primaries start; `bogus` errors — live (above) + `test_cli_with_the_explore_subagent_exits_nonzero_listing_primaries`, `test_cli_each_primary_agent_still_starts`.
- [x] PASS — `/agent explore` mid-session inline rejection (primaries only), REPL alive, deps/gate untouched; `/agent plan` still switches — `test_run_app_agent_slash_switches_and_rejections_stay_alive` (real `run_app`).
- [x] PASS — `select_agent("explore")` raises (primaries only), no mutation — `test_select_explore_subagent_is_rejected_and_leaves_state_untouched`; live state-integrity probe (break path 4).
- [x] PASS — default agent unchanged (`cli.py:80 _DEFAULT_AGENT == "build"`); fresh `decode` starts on build — live banner; `test_cli_defaults_to_the_build_agent`.
- [x] PASS — `make pre-commit` green; `filterwarnings=["error"]` clean — format-check + lint-check green, 1426 unit passed / 0 warnings.

**Evidence**
```
$ make format-check && make lint-check
171 files already formatted
All checks passed!

$ make unit-tests
======================= 1426 passed in 750.71s (0:12:30) =======================

$ make integration-tests
================== 1 failed, 96 passed in 1361.29s (0:22:41) ===================
FAILED tests/integration/test_credential_proxy.py::test_worker_request_arrives_with_injected_header_but_worker_holds_no_secret
  RuntimeError: docker network failed (exit 1): request returned 500 Internal Server Error
  ... src/decode/sandbox/proxy.py:267 in start → _docker("network", "create", ...)

# Proven environmental (not a regression): re-run in isolation passes, daemon healthy now.
$ uv run pytest tests/integration/test_credential_proxy.py::test_worker_request_arrives_... -q
1 passed in 60.45s (0:01:00)
```

**Other issues found** (non-blocking — orchestrator's call)
- `src/decode/agents/__init__.py` module docstring still reads "Subagent spawning is out of scope this milestone (the catalog is main-agent only)." — slightly stale now that explore is subagent-only. Not asserted by any test; subagent *spawning* (the Agent tool) is genuinely still out of scope until #088; prose is #089's lane. Suggest a one-line refresh when #089 lands.
- `load_primary_agent` is not re-exported in `agents/__init__.py __all__`; both callers import it directly from `agents.loader`, which works. Style choice, not a defect.
- Integration flake: `test_credential_proxy.py::test_worker_request_arrives_...` failed once mid-suite on a transient docker-daemon HTTP 500 to `network create` (in the untouched `sandbox/proxy.py`); passes on isolated re-run. Pre-existing real-docker timing sensitivity, unrelated to this task's diff.

**VERDICT: PASS**
