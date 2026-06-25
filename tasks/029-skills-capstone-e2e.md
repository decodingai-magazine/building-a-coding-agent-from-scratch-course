---
id: 029-skills-capstone-e2e
feature: skills
status: pending
---

# Skills: end-to-end capstone (both entry points → same skill body, progressive disclosure)

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (full flow, both entry points).
Depends on: 028 · Blocks: (none)

## Scope

A capstone-style integration test proving the whole Skills flow hangs together through the **real**
`build_agent()` + loop (and the real TUI slash path), swapping only the network boundary
(`FunctionModel`, faked key — no API key, no network). Mirror
`tests/integration/test_milestone1_capstone.py`'s harness.

- **`tests/integration/test_milestone3_skills_capstone.py`** — assert both entry points and the two
  tiers of progressive disclosure:
  1. **Catalog (always injected, cheap):** both built-in skills' `name` + `description` are present in
     a real `build_agent()` run's instructions — the "menu" rides every prompt.
  2. **Model dispatcher (body on demand):** a scripted `FunctionModel` that calls `skill("commit")`
     gets the **full body** back as the tool result, and **no `PermissionRequested`** is emitted for
     the `skill` call (ungated dispatcher).
  3. **User TUI slash path (second entry point):** drive `/commit` through the app's input handling
     (the `parse_skill_command` → `_handle_skill_command` → `runner.submit` path) and assert the
     **skill body became the turn input** (not the literal `/commit`).
  4. **Project override:** with `<cwd>/.decode/skills/commit.md` (under a `tmp_path` working tree)
     present, both `skill("commit")` and `/commit` resolve to the **project** body, and the catalog
     line reflects the project description (intentional same-name override).
  5. **Unknown skill:** `skill("does-not-exist")` surfaces a `ModelRetry` listing the available names
     (the model adapts; no crash). (The unknown-TUI-slash discovery line is covered in 028.)

## Acceptance criteria

- [ ] The run's injected instructions contain the Skills Catalog: both built-in skill names
      (`commit`, `review-diff`), their descriptions, and the `skill("…")` cue.
- [ ] **Model path:** a real `build_agent()` turn (scripted `FunctionModel`, faked `GEMINI_API_KEY`,
      no network) where the model calls `skill("commit")` returns the built-in commit body as the tool
      result; **no `PermissionRequested`** event is emitted (ungated).
- [ ] **TUI path:** driving `/commit` through the app submits the **commit skill body** as the turn
      input (asserted on what reaches `runner.submit` / the resulting user message), proving the second
      entry point resolves to the same body.
- [ ] **Project override:** with `<cwd>/.decode/skills/commit.md` present, both `skill("commit")` and
      `/commit` return/submit the **project** body, and the catalog shows the project description.
- [ ] **Unknown skill:** `skill("does-not-exist")` produces a `ModelRetry` whose message lists the
      available skill names.
- [ ] The test needs **no `GEMINI_API_KEY` and makes no network call**; `make integration-tests` and
      `make ci` are green, 0 warnings.

## Out of scope
- The manual real-Gemini e2e pass (the Tester's adversarial half / the AGENTS.md manual QA table) —
  exercising a real `/commit` that actually stages + commits is a manual check, not this automated test.
- A `~/.decode/skills` source and per-agent skill allowlists (deferred, ADR-0004).

## Log
### [PA] 2026-06-25 — Grooming
The living proof for M3, mirroring `test_milestone1_capstone.py`: real `build_agent()` + loop +
`FunctionModel`, only the network boundary faked. Round-2: it now pins **both** entry points —
the model's `skill("commit")` dispatcher AND the user's `/commit` TUI command — resolving through
`load_skills` to the same body, plus the two progressive-disclosure tiers (catalog always in the
prompt; body only on demand), the intentional project override, and the `ModelRetry` unknown-name
path — all without a key or network, so it runs in CI. Renumbered to 029 (last) after the new TUI
task (028).
