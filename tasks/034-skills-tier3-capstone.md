---
id: 034-skills-tier3-capstone
feature: skills-directory-convention
status: done
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

- [x] The capstone runs under the directory layout: the catalog tier (both built-in names +
      descriptions + the `skill("…")` cue, **no** paths), the ungated model dispatcher, the `/<skill>`
      TUI path, the project-override (now `<dir>/SKILL.md`), and the unknown-skill `ModelRetry` all stay
      green via the real `build_agent()` + loop.
- [x] **Built-ins are tier-2 only:** `skill("commit")` and `/commit` return the built-in body with **no**
      resource trailer (SKILL.md-only).
- [x] **Tier-3 proof:** a project skill at `<cwd>/.decode/skills/<name>/SKILL.md` with a sibling
      `references/<file>.md` resolves through the real loader with `resource_dir` set; `skill("<name>")`
      returns body **+ trailer** naming the cwd-relative `<dir>/`, and emits **no** `PermissionRequested`
      (ungated dispatcher).
- [x] A scripted `FunctionModel`, prompted by the trailer, calls `read("<dir>/references/<file>.md")`
      through the **real** gated `read` tool (approval granted in the test) and receives the **bundled
      file's contents** as the tool result — the bundled resource is loadable on demand end to end.
- [x] The `/<name>` TUI path injects the **same** body + trailer as the dispatcher for the tier-3 skill
      (one shared helper, both entry points).
- [x] The test needs **no `GEMINI_API_KEY` and makes no network call**; `make integration-tests` and
      `make ci` are green with 0 warnings (`filterwarnings=["error"]`). The capstone touches no
      production code.

## Out of scope
- The manual real-Gemini e2e pass (the Tester's adversarial half / the AGENTS.md manual QA table) —
  exercising a real bundled-resource skill against live Gemini is a manual check, not this test.
- Tier-3 for built-in skills (ADR-0004 §3) and the deferred ADR-0004 §10 items.
- Any production code change (loader/entity in 032; trailer helper + wiring in 033).
- A checked-in example skill under `src/` (the tier-3 example is a test-only `tmp_path` fixture).

## Log

### [SWE] 2026-06-26 12:10 — Implementation

**Files modified**
- `tests/integration/test_milestone3_skills_capstone.py` — extended the M3 capstone with the two
  task-034 guarantees (built-ins are tier-2/no-trailer; the full tier-3 bundled-resource chain); added
  the approving resolver, `_read_call` / `_write_tier3_skill` / `_seen_tool_returns` helpers, and
  generalized `_make_runner` to accept a resolver. No production code touched.
- `tasks/034-skills-tier3-capstone.md` — status `pending` → `in-progress`; acceptance boxes checked.

**Tests**
- Integration: 8 passing, 0 failing — the M3 capstone now has **7** tests (5 existing + 2 new), plus
  the M1 capstone. `make integration-tests` green.
- Unit: 690 passing (unchanged — no production code changed). Full `make ci`: **698 passed, 0 warnings**
  (`filterwarnings=["error"]`).

**Acceptance criteria** — all verified by
`tests/integration/test_milestone3_skills_capstone.py`:
- [x] Directory-layout catalog/dispatcher/TUI/override/unknown stay green — the 5 pre-existing tests.
- [x] Built-ins tier-2 only (no trailer, both entry points) —
      `::test_builtin_skills_are_tier_2_only_with_no_resource_trailer`.
- [x] Tier-3 proof (body + trailer, ungated; no path in catalog) —
      `::test_tier3_project_skill_drives_the_full_three_tier_flow`.
- [x] Scripted model reads the bundled `references/checklist.md` through the real gated `read` and
      gets its contents back — same test.
- [x] `/pdf-export` TUI path injects the identical body+trailer payload — same test.
- [x] No `GEMINI_API_KEY`, no network; `make integration-tests` + `make ci` green, 0 warnings.

**Evidence**
```
$ env -u GEMINI_API_KEY uv run pytest tests/integration/test_milestone3_skills_capstone.py -v
...
test_skills_catalog_rides_every_real_run_instructions PASSED            [ 14%]
test_model_dispatcher_returns_the_builtin_body_ungated PASSED           [ 28%]
test_tui_slash_command_submits_the_skill_body_not_the_literal_slash PASSED [ 42%]
test_project_override_wins_for_both_entry_points_and_the_catalog PASSED  [ 57%]
test_unknown_skill_surfaces_a_model_retry_listing_available_names PASSED [ 71%]
test_builtin_skills_are_tier_2_only_with_no_resource_trailer PASSED      [ 85%]
test_tier3_project_skill_drives_the_full_three_tier_flow PASSED          [100%]
============================== 7 passed in 1.67s ===============================

$ make ci
============================= 698 passed in 7.51s ==============================
```

**Notes**
- **One architectural reality the Tester should scrutinize:** the tier-3 `read` is read-only, and the
  permission gate auto-allows read-only tools under **every** mode (ADR-0003 §1; see
  `PermissionGate._decide_by_mode`). So the `read` never reaches the resolver — there is no mode/rule
  that makes a read-only tool *ask*. I honored the task's "approval granted in the test" by wiring an
  **approving** resolver (`_approve_permission`) as the standing would-be human verdict and asserting
  the read returns the bundled file's contents; the test asserts **no** `PermissionRequested` is
  surfaced (auto-allow), consistent with the M1 capstone's framing of read-only auto-allow. The
  proof that the gated read executed and was allowed is the bundled file's contents arriving as the
  tool result (a denied read would return a denial string instead). This is the only faithful reading
  without a production change — flagging it explicitly rather than silently.
- No production code changed (032 shipped the directory loader + `resource_dir`; 033 shipped the
  trailer + `format_skill_payload`). `git diff --stat` touches only the test + this task file.

### [Tester] 2026-06-26 12:01 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make ci` → `uv lock --check` clean, ruff "All checks passed!")
- Unit tests: 690 passed / 0 failed (`make pre-commit`)
- Integration tests: 8 passed / 0 failed (`make integration-tests`; M3 capstone = 7, M1 capstone = 1)
- Full gate `make ci`: 698 passed / 0 failed
- Warnings: 0 (`filterwarnings=["error"]` — any warning would have failed the run)
- No-key / no-network: `env -u GEMINI_API_KEY uv run pytest …test_milestone3_skills_capstone.py -v`
  → 7 passed (key faked only for `build_agent` construction; no network call)

**E2E adversarial pass** — the headline duty for this integration-only task: confirm the tier-3
proof is genuine, not tautological or over-mocked. The "user" here is the model + TUI driving the
real stack; I attacked the test's claims by mutating the inputs it depends on.

- Happy path: `env -u GEMINI_API_KEY uv run pytest …::test_tier3_project_skill_drives_the_full_three_tier_flow`
  → PASS (catalog name+desc, no path; `skill("pdf-export")` body+trailer ungated; `read(...)`
  returns the bundled `references/checklist.md` contents; `/pdf-export` TUI injects the same payload).
- Break path 1 (mutation — read must hit disk): dropped the marker line from the on-disk
  `references/checklist.md` contents (`_TIER3_REF_CONTENTS`) → tier-3 assertion
  `any(_TIER3_REF_MARKER in r …)` FAILED as required. Proves the `read` tool returns the REAL file
  contents, not a stub/mock that echoes the marker. (PASS — fails when it should.)
- Break path 2 (mutation — path resolution must be real): pointed the scripted `read` at
  `references/NONEXISTENT.md` → the real `read` tool errored, marker absent, tier-3 assertion FAILED
  as required. Proves the surfaced cwd-relative dir is genuinely `read`-resolvable through
  `_resolve_in_cwd` + `target.read_text` (files.py:139), not a hardcoded happy path. (PASS.)
- Break path 3 (over-mock audit, read-only): traced test 7 against production — REAL `build_agent()`
  (real `@agent.instructions` catalog hook → `assemble_skills_catalog`, real flat registry, real
  ungated `skill` dispatcher → `format_skill_payload`), REAL `load_skills`/`discover_project_skills`
  (sets `resource_dir` because the sibling `references/` makes the dir resource-bearing), REAL
  `Runner`/`AgentTurnHandler`, REAL gated `read`. Only the model (FunctionModel) and the key are
  swapped — nothing stubs the loader, payload, or read tool. (PASS.)

**Adversarial findings on the SWE's flagged nuance (read-only auto-allow):** Confirmed faithful and
non-tautological. The `read` is read-only so the gate auto-allows it under default mode and the
resolver is never invoked; the test wires an *approving* resolver as the standing would-be verdict
and asserts `not _permission_requests(events_seen)` (empirically zero — proving auto-allow) with the
bundled contents arriving as proof the read executed and was allowed (a denied read returns a denial
string, not the contents). This is consistent with the M1 capstone's read-only framing and is the
only faithful reading without a production change. The test docstring states it honestly.

**Acceptance criteria**
- [x] PASS — Capstone runs under the directory layout (catalog both built-in names+descriptions+cue,
      no paths; ungated dispatcher; `/<skill>` TUI; project override via `<dir>/SKILL.md`; unknown →
      ModelRetry) via real `build_agent()` + loop — tests 1–5 green (5 pre-existing).
- [x] PASS — Built-ins are tier-2 only — `::test_builtin_skills_are_tier_2_only_with_no_resource_trailer`;
      `_TRAILER_MARKER` ("Bundled files for this skill") matches production payload.py:51 exactly, and
      `commit_body in returns` byte-for-byte for both the dispatcher and the `/commit` TUI path.
- [x] PASS — Tier-3 proof: project skill `<cwd>/.decode/skills/pdf-export/SKILL.md` + sibling
      `references/checklist.md` resolves through the real loader with `resource_dir` set; `skill(...)`
      returns body+trailer naming the cwd-relative dir (`f"{rel_dir}/"` computed independently from
      `settings.skills_dir`, asserted present in the actual dispatcher return) and emits no
      `PermissionRequested` for the `skill` call — `::test_tier3_project_skill_drives_the_full_three_tier_flow`.
- [x] PASS — Scripted model reads `read("<dir>/references/checklist.md")` through the real gated
      `read` and receives the bundled file's contents — verified by mutation (break paths 1 & 2: the
      assertion fails when the on-disk contents or the path is wrong, so it is bound to real disk I/O).
- [x] PASS — `/pdf-export` TUI path injects the identical body+trailer
      (`tui_input == expected_payload`, one shared `format_skill_payload` helper) — same test.
- [x] PASS — No `GEMINI_API_KEY`, no network; `make integration-tests` + `make ci` green, 0 warnings;
      capstone touches no production code (`git status` → only the test + this task file modified; no
      `pdf-export` example checked in under `src/decode/skills/builtin/`).

**Evidence**
```
$ env -u GEMINI_API_KEY uv run pytest tests/integration/test_milestone3_skills_capstone.py -v
... 7 passed in 1.81s

$ make pre-commit         # format-check + lint-check + unit-tests
============================= 690 passed in 7.22s ==============================

$ make integration-tests
============================== 8 passed in 1.56s ===============================

$ make ci                 # uv lock --check + format-check + lint-check + full suite
uv lock --check
All checks passed!
============================= 698 passed in 7.40s ==============================

# Adversarial mutation (temp copies, reverted): both fail at the tier-3 read assertion —
$ # Mutation A: on-disk checklist contents changed → FAILED (read returns real disk contents)
$ # Mutation B: scripted read path → references/NONEXISTENT.md → FAILED (path resolution is real)
```

**Other issues found**
- None. Test-only change; no production code touched, so no library `print()`, type, or security
  surface to regress. Test quality is high: cross-checking assertions (independent `rel_dir` vs the
  real `format_skill_payload` output), an honest docstring on the read-only auto-allow nuance, and
  helper reuse shared with the M1 capstone style.
- Note (not blocking): the `code-review` plugin is enabled in `.claude/settings.json`; it is a
  slash-command plugin with no programmatic tool surface from this agent. The manual checklist plus
  the adversarial mutation pass stand in, and the diff is test-only (no production surface to lint).

**VERDICT: PASS**
