---
id: 029-skills-capstone-e2e
feature: skills
status: done
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

- [x] The run's injected instructions contain the Skills Catalog: both built-in skill names
      (`commit`, `review-diff`), their descriptions, and the `skill("…")` cue.
- [x] **Model path:** a real `build_agent()` turn (scripted `FunctionModel`, faked `GEMINI_API_KEY`,
      no network) where the model calls `skill("commit")` returns the built-in commit body as the tool
      result; **no `PermissionRequested`** event is emitted (ungated).
- [x] **TUI path:** driving `/commit` through the app submits the **commit skill body** as the turn
      input (asserted on what reaches `runner.submit` / the resulting user message), proving the second
      entry point resolves to the same body.
- [x] **Project override:** with `<cwd>/.decode/skills/commit.md` present, both `skill("commit")` and
      `/commit` return/submit the **project** body, and the catalog shows the project description.
- [x] **Unknown skill:** `skill("does-not-exist")` produces a `ModelRetry` whose message lists the
      available skill names.
- [x] The test needs **no `GEMINI_API_KEY` and makes no network call**; `make integration-tests` and
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

### [SWE] 2026-06-26 12:00 — Implementation

**Files modified**
- `tests/integration/test_milestone3_skills_capstone.py` — new M3 capstone integration test: the
  whole Skills flow (both entry points + both progressive-disclosure tiers) through the real
  `build_agent()` + `Runner`/`AgentTurnHandler`, network faked with a scripted `FunctionModel`.
- `tasks/029-skills-capstone-e2e.md` — status `pending → in-progress`; acceptance criteria checked.

**Tests**
- Unit: 652 passing, 0 failing (`make pre-commit`).
- Integration: 6 passing (`make integration-tests`) — the new file adds 5; M1 capstone still green.
- Full gate: `make ci` → 658 passing, 0 warnings (`filterwarnings=["error"]`).

**Acceptance criteria**
- [x] Catalog injected (names + descriptions + `skill("…")` cue) — `test_skills_catalog_rides_every_real_run_instructions`
- [x] Model dispatcher returns the built-in body, no `PermissionRequested` — `test_model_dispatcher_returns_the_builtin_body_ungated`
- [x] TUI `/commit` submits the body (not literal `/commit`) — `test_tui_slash_command_submits_the_skill_body_not_the_literal_slash`
- [x] Project override wins for `skill()`, `/commit`, and the catalog — `test_project_override_wins_for_both_entry_points_and_the_catalog`
- [x] Unknown skill → `ModelRetry` listing available names, no crash — `test_unknown_skill_surfaces_a_model_retry_listing_available_names`
- [x] No `GEMINI_API_KEY` / no network; `make integration-tests` + `make ci` green, 0 warnings

**Evidence**
```
$ make ci
... 658 passed in 7.13s ...

$ make integration-tests
tests/integration/test_milestone1_capstone.py .                          [ 16%]
tests/integration/test_milestone3_skills_capstone.py .....               [100%]
============================== 6 passed in 1.41s ===============================

$ env -u GEMINI_API_KEY uv run pytest tests/integration/test_milestone3_skills_capstone.py -q
.....                                                                    [100%]
5 passed in 1.25s
```

**Notes**
- The capstone drives the real harness via `Runner`/`AgentTurnHandler` (catching `PermissionRequested`
  and `AgentError` through the single event sink) and the real `parse_skill_command` →
  `_handle_skill_command` → `runner.submit` chain for the TUI entry point — no production code
  changed; tasks 024-028 already shipped it all.
- Adjacent issue spotted while authoring (NOT in scope here, no fix applied): a project skill whose
  YAML frontmatter is malformed by a *YAML scanner* error (e.g. an unquoted `:` in `description`)
  raises `yaml.YAMLError`, which `decode.skills.loader.discover_project_skills` does **not** catch —
  it only catches `(ValueError, OSError)`. Its docstring promises malformed project skills are
  skipped, so this is a latent gap (a user's typo could crash a session rather than be skipped).
  Worth a follow-up task to widen the except to include `yaml.YAMLError`. The capstone uses valid
  frontmatter, so it is unaffected.

### [Tester] 2026-06-26 01:17 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 107 files; `ruff check` clean)
- Unit tests: 652 passed / 0 failed
- Integration tests: 6 passed / 0 failed (M1 capstone + 5 new M3)
- Full gate `make ci`: 658 passed, exit 0, 0 warnings (`filterwarnings=["error"]` ⇒ a warning would fail)
- No-key independent check: `GEMINI_API_KEY` absent from the shell entirely; `env -u GEMINI_API_KEY uv run pytest tests/integration/test_milestone3_skills_capstone.py -v` → 5 passed. No network call.

**E2E adversarial pass** (this task ships a test, so the adversarial duty is: is each assertion
genuine through the REAL stack, or tautological/over-mocked? Plus the assigned malformed-YAML probe.)
- Real wiring confirmed: every test calls the real `build_agent()` (real registry, real `@agent.instructions`
  skills-catalog hook, real `Runner`/`AgentTurnHandler`, real `PermissionGate()`), overriding **only** the
  model with a scripted `FunctionModel`. No `TestModel`-only shortcut bypasses production wiring. Each test
  uses its own `tmp_path` as cwd, so the repo's `.decode/skills` is never read/written (isolated).
- Assertion 1 (catalog): GENUINE. Reads `captured[0][0].instructions` — the instructions pydantic-ai
  actually assembled and handed the model on a real run leg — not a direct `assemble_skills_catalog()` call.
  Also pins `set(builtins) == {"commit","review-diff"}` so catalog drift fails the test. PASS.
- Assertion 2 (model dispatcher ungated): GENUINE, double-locked. The resolver is `_deny_permission`, so if
  `skill` were gated it would emit `PermissionRequested` AND return a denial instead of the body — the test
  asserts the body return AND no `PermissionRequested`, so a regression to "gated" fails both ways. Confirmed
  `skill` is registered ungated (`registry.py:133-137`, kind OTHER, never raises `ApprovalRequired`). PASS.
- Assertion 3 (TUI slash path): GENUINE. Drives the exact production chain `parse_skill_command` →
  `_handle_skill_command(cwd=…)` → `runner.submit` (matches `app.py:736-741`), then asserts the body reached
  the model as a `UserPromptPart` and the literal `/commit` did not. It reconstructs the three production
  functions rather than driving `run_app`'s prompt_toolkit input loop — which the AC explicitly permits
  ("asserted on what reaches runner.submit / the resulting user message"); the M1 capstone bypasses
  prompt_toolkit identically. Minor scope note, not a defect. PASS.
- Assertion 4 (project override): GENUINE. Writes a real `<tmp>/.decode/skills/commit.md`; asserts all three
  surfaces (catalog line, `skill("commit")` tool return, `/commit` turn input) resolve to the PROJECT body and
  that the built-in description is GONE from the catalog. Exercises the real `load_skills` merge/override. PASS.
- Assertion 5 (unknown skill): GENUINE. Asserts the `ModelRetry` reached the model as a `RetryPromptPart` on
  the next captured leg (proves the loop fed it back, not just that the dispatcher raised), the message names
  the bad skill + every available name, and no `AgentError` was emitted (retry, not crash). PASS.
- Verdict on test quality: none of the five are tautological or over-mocked; all flow through the production
  loop with only the network boundary faked.

**Malformed-YAML loader probe (assigned; confirm only, do NOT fix)**
3-line repro: `discover_project_skills(cwd)` against a tmp cwd holding one `.decode/skills/oops.md` whose
frontmatter is YAML-scanner-malformed. Three variants tried:
- description with unquoted `: ` + stray quote → `yaml.scanner.ScannerError` (`ValueError? False`)
- unterminated quoted scalar → `yaml.scanner.ScannerError`
- tab-indented mapping → `yaml.scanner.ScannerError`
In all three, `discover_project_skills` **RAISED** (propagated) the `ScannerError` instead of skipping it.
`loader.py:109` catches only `(ValueError, OSError)`; `yaml.scanner.ScannerError` subclasses `yaml.YAMLError`,
not `ValueError`, so it escapes. This contradicts the docstring (`loader.py:97-98`: "logged at WARNING and
**skipped** so a user's typo never crashes the agent") and ADR-0004 §3. Because `load_skills` → the
skills-catalog instructions hook calls this on every turn, a single typo'd project skill would crash a live
session. **CONFIRMED REAL DEFECT.** It is OUT OF SCOPE for task 029 (the capstone uses valid frontmatter; no
029 AC requires malformed-YAML handling) — flagged here for the follow-up task, not failed against 029.

**Acceptance criteria**
- [x] PASS — Catalog (names + descriptions + `skill("…")` cue) rides a real run's instructions —
      `test_skills_catalog_rides_every_real_run_instructions` (reads assembled `ModelRequest.instructions`)
- [x] PASS — Model `skill("commit")` returns the built-in body, no `PermissionRequested` (ungated) —
      `test_model_dispatcher_returns_the_builtin_body_ungated`; `registry.py:133-137`
- [x] PASS — TUI `/commit` submits the body (not literal `/commit`) via parse→handle→submit —
      `test_tui_slash_command_submits_the_skill_body_not_the_literal_slash`; matches `app.py:736-741`
- [x] PASS — Project override wins for `skill()`, `/commit`, and the catalog line —
      `test_project_override_wins_for_both_entry_points_and_the_catalog`
- [x] PASS — Unknown skill → `ModelRetry`/`RetryPromptPart` listing available names, no crash —
      `test_unknown_skill_surfaces_a_model_retry_listing_available_names`
- [x] PASS — No `GEMINI_API_KEY` / no network; `make integration-tests` + `make ci` green, 0 warnings —
      ran with key unset from env (5 passed); `make ci` 658 passed exit 0

**Evidence**
```
$ make ci
... 658 passed in 7.18s ...
$ make ci >/dev/null 2>&1; echo $?
0
$ printenv GEMINI_API_KEY   # NOT set
$ env -u GEMINI_API_KEY uv run pytest tests/integration/test_milestone3_skills_capstone.py -v
... 5 passed in 1.21s ...
$ make integration-tests
tests/integration/test_milestone1_capstone.py .                          [ 16%]
tests/integration/test_milestone3_skills_capstone.py .....               [100%]
============================== 6 passed in 1.34s ===============================
```

**Other issues found**
- (out of 029 scope, follow-up) `discover_project_skills` does not catch `yaml.YAMLError` → a malformed
  project-skill frontmatter crashes the session rather than being skipped (confirmed repro above). Real defect;
  fix belongs to a separate task per the orchestrator's plan.
- (nit, non-blocking) The catalog test asserts the raw `skill.description` substring, while the catalog code
  whitespace-collapses each field (`" ".join(...split())`). It holds because the built-in descriptions are
  clean single lines; a future multi-space/newline description fixture could make the raw-substring assertion
  brittle. No change needed now.
- Diff is clean: only `tasks/029-skills-capstone-e2e.md` (modified) and the new test file (untracked); no
  unrelated files, no `print()` in library code (test-only change).

**VERDICT: PASS**

### [PA] 2026-06-26 — Acceptance Review (Milestone-3 Skills feature, PR #10, tasks 024–030)

**VERDICT: ACCEPT**

Reviewed the whole Skills feature from the user's POV against each task's ACs and ADR-0004's locked
decisions. All seven locked product decisions hold in the shipped code:

- Progressive disclosure: cheap catalog always injected (`agent/factory.py:75` →
  `assemble_skills_catalog`, `skills/catalog.py:45-68`), body on demand only via the dispatcher /
  `/<skill>` — confirmed by `test_milestone3_skills_capstone.py` reading assembled
  `ModelRequest.instructions`.
- Two entry points, one resolver: model `skill(name)` (`tools/skills.py:42-65`) and user
  `/<skill-name>` (`tui/app.py:308-334`) both resolve through `load_skills(cwd)`
  (`skills/loader.py:123-137`); project-over-built-in override pinned on all three surfaces.
- Ungated dispatcher, gated induced action: `skill` is `ToolKind.OTHER`, never raises
  `ApprovalRequired` (`tools/registry.py:128-137`); the `commit` skill's `git add`/`git commit` ride
  gated `bash` (default asks, plan denies).
- Two built-ins match their descriptions and are a sensible active/advisory teaching pair
  (`skills/builtin/commit.md`, `skills/builtin/review-diff.md`).
- Malformed-YAML project skill skipped-with-WARNING, never crashes a session
  (`skills/loader.py:109-115`, task 030).
- Docs accurate: ADR-0004 (single feature ADR, Accepted) + four glossary terms describe what shipped.

No user-facing defects found. The footer not advertising `/<skill>` is an intentional, ADR-documented
v1 scope decision (task 028 Out of scope; discovery served by the unknown-slash available-skills line).
Hand off to the PR Reviewer.
