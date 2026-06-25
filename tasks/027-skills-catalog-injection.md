---
id: 027-skills-catalog-injection
feature: skills
status: pending
---

# Skills: the Skills Catalog injected into the prompt (progressive disclosure)

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (catalog injection / progressive disclosure).
Depends on: 026 · Blocks: 028

## Scope

Inject the lightweight **Skills Catalog** (each skill's `name` + one-line `description`) into the
system prompt via a dynamic `@agent.instructions` hook — always present, cheap. This is the "menu"
half of progressive disclosure; the dispatcher (026) loads a body on demand. Mirror the existing
`_register_memory_instructions` / `_register_agent_prompt_instructions` hooks and the `assemble_memory`
"return `""` when empty" contract.

- **`skills/catalog.py`** — `assemble_skills_catalog(cwd) -> str`: read `load_skills(cwd)`, format
  each skill as a markdown list item `- <name> — <description>` under a one-line cue telling the model
  to call `skill("<name>")` to load the full instructions. Stable, sorted-by-name ordering. Returns
  `""` when there are no skills, so the hook contributes nothing (no empty header) — same contract as
  `assemble_memory`. (Built-ins always ship, so `""` is the defensive/edge path.)
- **`agent/factory.py`** — add `_register_skills_catalog_instructions(agent)`: a `@agent.instructions`
  function that reads `ctx.deps.cwd` and returns `assemble_skills_catalog(ctx.deps.cwd)`. Wire it in
  `build_agent()` alongside the agent-prompt + memory hooks. The catalog is injected for **every**
  agent (not gated on `active_agent`) — ADR-0004 §4: all agents see all skills.

## Acceptance criteria

- [ ] `assemble_skills_catalog(cwd)` returns a block listing both built-in skills by
      `name — description`, plus a cue instructing the model to call `skill("<name>")` to load the full
      instructions. Unit-tested.
- [ ] The catalog reflects a **project override**: a `<cwd>/.decode/skills/commit.md` with a changed
      `description` changes the line shown for `commit`. Unit-tested.
- [ ] `assemble_skills_catalog(cwd)` returns `""` when `load_skills` yields no skills (patched empty),
      so the instructions hook adds nothing. Unit-tested.
- [ ] The `@agent.instructions` skills hook is registered in `build_agent()`; a run's assembled
      instructions **include** the catalog text (the skill names + the `skill("…")` cue). Verified via
      `build_agent()` (capture the instructions / assert the catalog string is present), no network.
- [ ] The catalog is injected regardless of the active agent (verified with at least two personas).
- [ ] `make ci` green, 0 warnings — including the existing `test_milestone1_capstone.py` (the extra
      catalog prompt text must not break it); `tests/unit/decode/skills/test_catalog.py` mirrors
      `src/decode/skills/catalog.py`.

## Out of scope
- The dispatcher tool itself — task 026 (done).
- The `/<skill-name>` TUI invocation — task 028.
- The dedicated skills capstone integration test — task 029.
- A body-size cap on skills and a `~/.decode/skills` source (deferred, ADR-0004).

## Log
### [PA] 2026-06-25 — Grooming
The catalog hook is a near-copy of the memory hook — `@agent.instructions` reading `ctx.deps.cwd`,
`""`-when-empty so no empty header. Split from the dispatcher (026) deliberately: this is the
prompt-injection + formatting concern, the dispatcher is the tool concern; each is independently
testable. Called out that injecting the catalog into *every* run means the M1 capstone now carries the
catalog in its prompt — must re-run `make ci` to confirm it stays green (the capstone asserts tool
results + rendered transcript, not exact prompt text, so it should).
