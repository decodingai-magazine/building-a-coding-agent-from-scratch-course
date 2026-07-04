---
id: 087-agents-catalog-subagent-axis-explore-demotion
feature: explore-subagents
status: pending
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

- [ ] `AgentDef` carries `subagent: bool = False`; constructing a persona without the key yields
  `subagent is False`. Covered by a new case in `tests/unit/decode/entities/test_agent_def.py`
  (near :40-53).
- [ ] `load_agent("explore").subagent is True` and `explore.tools == ("read", "glob", "grep", "lsp")`
  exactly — no `ask_user` / `skill` / `web_fetch` / `todo_write`. Update
  `tests/unit/decode/agents/test_loader.py:64-70` (`test_explore_agent_is_read_only_default_mode`) to
  the new toolset + assert `subagent is True`; assert the other three built-ins have `subagent is
  False`.
- [ ] The loader raises a clear `ValueError` when `subagent` is present but not a bool (e.g. a string),
  naming the offending file — mirroring `_optional_str_tuple`'s validation. New loader test.
- [ ] All four built-ins still load and validate (`load_builtin_agents()` returns build / plan /
  explore / code-reviewer); `_BUILTIN_NAMES` at `test_loader.py:21` unchanged.
- [ ] `decode --agent explore` exits non-zero with one friendly stderr line naming only the **primary**
  agents (build, code-reviewer, plan) — no traceback. `--agent build|plan|code-reviewer` still start,
  and `--agent bogus` still errors. New cases in `tests/unit/decode/test_cli.py`.
- [ ] `/agent explore` mid-session renders a friendly inline rejection (primaries only) and leaves
  `deps`/`gate` untouched; the REPL stays alive. Update
  `tests/unit/decode/tui/test_app_e2e.py:601-611` (was asserting `agent: explore`); `/agent plan` etc.
  still switch.
- [ ] `select_agent("explore", deps=…, gate=…)` raises `ValueError` (primaries only) and does not
  mutate `deps`/`gate`. Update `tests/unit/decode/agents/test_select.py:105`.
- [ ] The default agent is unchanged (`cli.py:80` `_DEFAULT_AGENT == "build"`); a fresh `decode` still
  starts on build.
- [ ] `make pre-commit` (format + lint + unit) green; `filterwarnings=["error"]` clean.

## Out of scope

- The `agent` tool, the child runner, and the new settings (#088).
- Granting `agent` to build/plan/code-reviewer `tools:` lists (#088 — it requires the tool registered).
- A `subagent_type` selection parameter (only one subagent exists — ADR-0013 §3, out of scope).
- README / AGENTS.md prose (#089); the capstone (#090).

## Log
