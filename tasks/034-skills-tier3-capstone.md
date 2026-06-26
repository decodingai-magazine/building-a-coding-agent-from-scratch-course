---
id: 034-skills-tier3-capstone
feature: skills-directory-convention
status: pending
---

# Skills: tier-3 capstone — directory layout, no-trailer built-ins, and a bundled-resource project skill

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (full three-tier flow, end to end).
Depends on: 033 · Blocks: (none)

## Scope

Extend the M3 capstone integration test so it proves the **three-tier** progressive-disclosure flow
through the **real** `build_agent()` + `Runner`/`AgentTurnHandler` + the real TUI slash path, swapping
only the network boundary (a scripted `FunctionModel`, faked key — no `GEMINI_API_KEY`, no network),
mirroring `tests/integration/test_milestone1_capstone.py`. Builds on 032 (directory layout) and 033
(trailer): this task adds the **tier-3 proof** and the **built-in-no-trailer** assertion.

### `tests/integration/test_milestone3_skills_capstone.py`
- **Directory layout (from 032):** all project-skill fixtures already write `<dir>/SKILL.md` under a
  `tmp_path` working tree; keep the catalog / model-dispatcher / TUI-slash / project-override / unknown
  assertions green under the new layout.
- **Built-ins get no trailer (tier-2 only):** assert `skill("commit")` (and the `/commit` TUI path)
  returns the built-in body with **no** resource trailer — built-ins are SKILL.md-only (ADR-0004 §3),
  so progressive disclosure stops at tier 2 for them.
- **Tier-3 PROJECT-skill example (the new proof):** under `<cwd>/.decode/skills/<name>/` write a
  `SKILL.md` whose body references a bundled file by relative path **plus** a sibling
  `references/<file>.md` with known contents. Then drive a scripted `FunctionModel` through the real
  loop and assert the full three-tier chain:
  1. **Tier 1:** the skill's `name` + `description` appear in the run's injected instructions (the
     catalog menu), and the catalog carries **no** path.
  2. **Tier 2 + surfacing:** `skill("<name>")` (ungated; no `PermissionRequested`) returns the body
     **plus the trailer** naming the skill's `<dir>/` (cwd-relative, `read`-resolvable).
  3. **Tier 3:** prompted by the trailer, the scripted model calls `read("<dir>/references/<file>.md")`
     (a gated read — approved via the resolver) and gets the **bundled file's contents** back as the
     tool result, proving the bundled resource is loadable on demand through the real `read` tool.
  - Also assert the `/<name>` TUI path injects the same body + trailer (second entry point, one helper).
- The tier-3 example lives **only in the test** (a `tmp_path` fixture) — no example skill is checked in
  under `src/`. No production code changes here (032 + 033 shipped it all); this task is integration
  coverage only.

## Acceptance criteria

- [ ] The capstone runs under the directory layout: the catalog tier (both built-in names +
      descriptions + the `skill("…")` cue, **no** paths), the ungated model dispatcher, the `/<skill>`
      TUI path, the project-override (now `<dir>/SKILL.md`), and the unknown-skill `ModelRetry` all stay
      green via the real `build_agent()` + loop.
- [ ] **Built-ins are tier-2 only:** `skill("commit")` and `/commit` return the built-in body with **no**
      resource trailer (SKILL.md-only).
- [ ] **Tier-3 proof:** a project skill at `<cwd>/.decode/skills/<name>/SKILL.md` with a sibling
      `references/<file>.md` resolves through the real loader with `resource_dir` set; `skill("<name>")`
      returns body **+ trailer** naming the cwd-relative `<dir>/`, and emits **no** `PermissionRequested`
      (ungated dispatcher).
- [ ] A scripted `FunctionModel`, prompted by the trailer, calls `read("<dir>/references/<file>.md")`
      through the **real** gated `read` tool (approval granted in the test) and receives the **bundled
      file's contents** as the tool result — the bundled resource is loadable on demand end to end.
- [ ] The `/<name>` TUI path injects the **same** body + trailer as the dispatcher for the tier-3 skill
      (one shared helper, both entry points).
- [ ] The test needs **no `GEMINI_API_KEY` and makes no network call**; `make integration-tests` and
      `make ci` are green with 0 warnings (`filterwarnings=["error"]`). The capstone touches no
      production code.

## Out of scope
- The manual real-Gemini e2e pass (the Tester's adversarial half / the AGENTS.md manual QA table) —
  exercising a real bundled-resource skill against live Gemini is a manual check, not this test.
- Tier-3 for built-in skills (ADR-0004 §3) and the deferred ADR-0004 §10 items.
- Any production code change (loader/entity in 032; trailer helper + wiring in 033).
- A checked-in example skill under `src/` (the tier-3 example is a test-only `tmp_path` fixture).

## Log
