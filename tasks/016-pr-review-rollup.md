---
status: done
feature: m1-vanilla-agent
---

# [PR review rollup] Milestone 1 — vanilla on-device coding agent (decode)

Tags: `rollup`, `pr-review`
Refs: PR #8 (branch: `feat/m1-vanilla-agent`)

## Scope

PR Reviewer found **1 Blocker** and **4 Nits** in the diff. The SWE must fix the Blocker
(and may fix Nits at their discretion) in a single coordinated pass, then hand back to the
Tester. Pipeline re-runs from QA → PA acceptance → push → re-review.

The milestone is otherwise in excellent shape: 347 tests pass, `ruff format --check` and
`ruff check` are both clean, `tests/` mirrors `src/` 1:1, the capstone integration test drives
the real stack with meaningful (non-green-only) assertions, and every security-hardened area
called out for verification is sound (path containment in `tools/files.py`, process-group kill +
partial-output-on-timeout in `tools/exec.py`, the `web_fetch` RecursionError guard, the
single-input `DecisionChannel`, and the deferred-approval deny path). The known-deferred
`web_fetch` SSRF-to-localhost is documented and intentionally out of scope — not flagged.

## Acceptance Criteria

- [x] Blocker 1: the do-nothing `noop` tool is no longer registered on the production agent
      (`build_agent()` exposes only the real M1 tools: `read`, `glob`, `grep`, `write`, `edit`,
      `bash`, `todo_write`, `web_fetch`, `ask_user`). The model's tool schema contains no `noop`.
- [x] The registry/factory tests are updated to assert `noop` is **absent** from the production
      tool set (they currently assert its presence), and the gated-flow-in-isolation test keeps
      working via `register_noop` (moved to a test fixture/helper if `noop.py` is removed from
      `src/`).
- [x] Tester re-runs full QA suite and PASSES.
- [ ] PA re-runs acceptance review and ACCEPTS.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS`.

## Blockers (detail)

### 1. [Clean code / Simplicity] — src/decode/tools/registry.py:63 (+ src/decode/tools/noop.py, src/decode/agent/factory.py:70)
- **What's wrong:** The trivial `noop` echo tool — scaffolding added in task 005 to make the
  permission-gate-via-deferred-tools path real *before any real tool existed* — is still
  registered on the **production** agent. `TOOL_SPECS[0]` lists it, `register_tools()` registers
  every spec, and `build_agent()` calls `register_tools()`, so the live Gemini model is handed a
  tool whose entire behaviour is to return `"noop: <text>"`. Its own module docstring confirms it
  "does nothing but echo its `text` argument"; the real tools delivered in 006–011 fully
  supersede its purpose. The `register_noop` standalone helper (the documented "minimal one-tool
  agent for tests" path) is the only part that still has a legitimate use — and it is used by
  exactly one test (`tests/unit/decode/tui/test_app_e2e.py`).
- **Why it's a Blocker:** Dead/scaffolding code being shipped (an explicit Blocker criterion) and
  over-engineering that hurts maintainability — it pollutes the model-facing tool schema with a
  useless tool the model can be lured into calling, and ships a permanent no-op into production.
  AGENTS.md is explicit: "favour the simplest thing that works", "no abstraction without a second
  concrete caller", and remove scaffolding once the real thing lands.
- **Suggested fix:** Drop the `noop` `ToolSpec` from `TOOL_SPECS` so `noop` is never registered on
  the production agent. Keep the gated-flow-in-isolation capability for the e2e test by retaining
  `register_noop` as a **test** helper (move `noop.py` under `tests/` support, or leave it in
  `src/` but unreferenced by the registry — moving it to tests is cleaner). Then flip the
  registry/factory tests that currently assert `noop ∈ TOOL_SPECS` to assert its absence from the
  production set. The PR description's "Open item for review" does not mention this — worth a line
  there too.
- **Regression test (if applicable):** A registry test asserting the exact production tool-name
  set excludes `noop`; the existing `test_app_e2e` gated-flow test continues to pass via the
  test-scoped `register_noop`.

## Nits (non-blocking; will be appended to PR description if pipeline advances)

### 1. [Clean code] — src/decode/entities/events.py:62,76 + src/decode/tui/render.py:32-34,61-71
- **Suggestion:** `ToolCallStarted` and `ToolResult` are defined in the events union and have
  render branches, but nothing in production emits them — the loop only emits text/thinking/
  permission/ask-user/task/turn events, so a tool call never renders a panel in the live REPL.
  They're forward-looking contract scaffolding (and the events docstring says the union holds
  "only the events M1 actually produces"). Either start emitting them from the loop's gate/tool
  path (so tool calls visibly render, matching ADR-0002 §6 "tool calls render on completion"), or
  trim them until the milestone that needs them. Not blocking — no user-facing regression is
  claimed in the E2E docs.

### 2. [Performance] — src/decode/tools/files.py:417-452 (`_fuzzy_unique_span`)
- **Suggestion:** The fuzzy `edit` fallback scans every `(start, end)` substring pair of the file
  and calls `_normalize_ws` on each slice — roughly O(n²) span pairs × O(n) per normalize on file
  length. On a large source file with no exact `old_string` match this could spin noticeably.
  It's a cold path (only when the exact match misses), runs on bounded files, and every `edit` is
  human-gated, so it doesn't block — but a tighter fuzzy matcher (e.g. normalize once and scan
  with a rolling window) would remove the cliff if `edit` ever sees big files.

### 3. [Documentation] — PR #8 description, "Open item for review"
- **Suggestion:** The open-item paragraph says "ADR-0002 §8 says **repo-root**", but the ADR
  shipped in this diff actually states **filesystem-root** and documents that choice as deliberate
  (deferring a `.git`-marker stop to M3). Code (`memory/files.py`), the ADR, and the module
  docstring all agree on filesystem-root — there is no contradiction to ratify. Update or drop the
  stale open-item note so it doesn't send PA chasing a non-issue.

### 4. [Documentation discipline] — docs/glossary.md
- **Suggestion:** M1 introduces the `DecisionChannel` (the single mid-turn HITL input surface) and
  the steering-vs-follow-up distinction. The canonical concept ("Priority Gate") is already
  glossed and nothing contradicts the glossary, so this isn't blocking — but a short row for
  "Decision Channel" (or folding steering/follow-up under Priority Gate's Notes) would keep the
  ubiquitous-language file current with the code the milestone landed.

---

Refs: PR #8

## Log

### [SWE] 2026-06-20 — Implementation (Blocker + 2 worthwhile Nits)

**Files modified**
- `src/decode/tools/registry.py` — dropped the `noop` `ToolSpec` from `TOOL_SPECS` (and its
  import); the production agent no longer exposes `noop`. Added a comment explaining the
  deliberate absence.
- `src/decode/tools/noop.py` — kept as a TEST-ONLY helper; docstrings updated to say it is NOT
  in the production registry (only `register_noop` survives for tests).
- `src/decode/tools/__init__.py`, `src/decode/agent/factory.py` — docstrings updated to drop the
  "noop is registered" claims and list the 9 real M1 tools.
- `src/decode/agent/loop.py` — emit `ToolCallStarted` / `ToolResult` from the call-tools node
  (`Agent.is_call_tools_node` + `node.stream`), mapping pydantic-ai's `FunctionToolCallEvent` /
  `FunctionToolResultEvent`. `ToolCallStarted` is deduped per `tool_call_id` (a gated call is
  replayed on the resume leg) so the panel renders once on completion (ADR-0002 §6).
- `src/decode/tools/files.py` — one-line cold-path note on `_fuzzy_unique_span` (no behavior
  change; not fixing the O(n³) per the task).
- `docs/glossary.md` — added `Steering`, `Follow-up`, `Decision Channel`, `Deferred Approval`,
  `AskUser`; cross-linked `Priority Gate`.
- `tests/unit/decode/tools/test_registry.py` — flipped the `noop`-present assertions to
  `noop`-**absent**; kept the real-tools-registered assertions; new
  `test_registry_does_not_expose_the_scaffolding_noop_tool`.
- `tests/unit/decode/agent/test_loop.py` — new `gated_agent` fixture registers `noop` locally
  on the test agent (production no longer does); moved the 5 permission/loop tests onto it. Added
  `test_tool_call_emits_started_and_result_events_and_renders_a_panel` and
  `test_gated_tool_is_announced_once_across_the_deferred_resume`.

**Tests**
- Unit + integration: 350 passing, 0 failing (was 347; +3). `make ci` green.
- Integration: capstone unchanged and passing (real `build_agent()` stack, no network).

**Acceptance criteria**
- [x] `noop` no longer registered on the production agent — verified by
      `tests/unit/decode/tools/test_registry.py::test_registry_does_not_expose_the_scaffolding_noop_tool`
      and `::test_register_tools_registers_every_spec_on_the_agent`, plus an e2e `build_agent()`
      schema check (9 real tools, no `noop`).
- [x] Registry/factory tests assert `noop` absent; gated-flow tests keep working via
      `register_noop` (test-scoped) — `test_loop.py` gated tests + `test_app_e2e.py`.
- [ ] Tester re-runs full QA suite and PASSES — pending Tester.
- [ ] PA re-runs acceptance review and ACCEPTS — pending PA.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS` — pending re-review.

**Evidence**
```
$ make ci
uv lock --check          → Resolved 166 packages
ruff format --check      → 79 files already formatted
ruff check               → All checks passed!
pytest                   → 350 passed in 6.18s

$ uv run python -c "...build_agent()..."
production tool schema: ['ask_user','bash','edit','glob','grep','read','todo_write','web_fetch','write']
OK: noop absent; exactly the 9 real M1 tools registered

$ uv run python -c "...loop + render_event..."
-> demo_tool {"text":"a"}
╭─ demo_tool ───────────────────────────────╮
│ tool output for: a                        │
╰───────────────────────────────────────────╯
```

**Notes**
- Per the task: did NOT fix the `_fuzzy_unique_span` O(n³) cold path (added the one-line note
  only) and did NOT touch the PR-body note (orchestrator owns the PR description).
- `noop.py` left under `src/` (task said keep it as a TEST-ONLY helper, not move it); the
  registry simply never references it.
- Glossary edit made under the explicit task instruction (Nit 4); no ADR or term invented beyond
  what the milestone code already uses.
- NOT committed (awaiting Tester PASS).

### [Tester] 2026-06-20 03:30 — QA

**Test summary**
- `uv lock --check` + format-check + lint-check: PASS (166 pkgs resolved; 79 files formatted; all
  ruff checks passed).
- Unit + integration (`make ci` → `pytest`): 350 passed / 0 failed.
- Warnings: 0 (`filterwarnings=["error"]` in effect; a non-zero warning count would have errored
  the run). `.decode/` stays absent; `git diff` touches exactly the 10 expected files + the new
  task file (no `git add -A` smell).
- `code-review` plugin is enabled in `.claude/settings.json` (advisory) — manual checklist below
  is the authority; folded into this verdict.

**E2E adversarial pass** (drove the real loop via `TestModel` / `FunctionModel` + approval)
- Happy path (ADV-1): two distinct non-gated tool calls in one turn → each emits one deduped
  `ToolCallStarted` + a correlated `ToolResult(ok=True)`; ids correlate. PASS.
- Break path 1 — state edge: gated tool, **denied** (`PermissionDecision.deny`) → emits
  `ToolResult` with **`ok=True`** and `output="user said no"`, rendering a GREEN success panel.
  Expected `ok=False` + RED "(failed)" panel (events.py:80 "ok is False … or was denied"; SWE
  hand-off "denied tool … ok=False"). **FAIL.**
- Break path 2 — failure mode: tool raises `ModelRetry` → `RetryPromptPart` → `ToolResult(ok=False)`,
  renders RED "(failed)" panel. PASS.
- Break path 3 — boundary/hostile args: unicode + emoji + ~10k-char arg string → one started/one
  result, `render_event` does not crash. PASS.

**Acceptance criteria**
- [x] PASS — Blocker 1: `noop` no longer registered on the production agent. Evidence:
      `build_agent()._function_toolset.tools` == exactly the 9 real tools
      `{read,glob,grep,write,edit,bash,todo_write,web_fetch,ask_user}`, `noop` absent;
      `grep -rn noop src/` shows only comments/docstrings — no `ToolSpec`/import/`register_*`
      call in the production path (`registry.py`, `factory.py`).
      `test_registry_does_not_expose_the_scaffolding_noop_tool` +
      `test_register_tools_registers_every_spec_on_the_agent` pass.
- [x] PASS — Registry/factory tests assert `noop` absent; gated-flow tests keep working via
      test-scoped `register_noop`. Evidence: `register_noop` referenced only by test files
      (`test_loop.py` `gated_agent` fixture, `test_app_e2e.py`), each registering locally on a
      test agent; 32 registry/loop/e2e tests pass.
- [ ] FAIL — Tester re-runs full QA suite and PASSES.
      Suite is green, but the e2e adversarial pass found a UX defect on the very path NIT-1
      added (see Other issues #1). Holding at FAIL until the denied-tool `ToolResult.ok` is fixed
      and regression-tested.
- [ ] PA re-runs acceptance review and ACCEPTS — pending (blocked by Tester FAIL).
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS` — pending.

**Evidence**
```
$ make ci
uv lock --check            → Resolved 166 packages
ruff format --check        → 79 files already formatted
ruff check                 → All checks passed!
pytest                     → 350 passed in 6.16s   (exit 0)

$ build_agent()._function_toolset.tools
['ask_user','bash','edit','glob','grep','read','todo_write','web_fetch','write']   (noop absent; 9 tools)

$ adversarial: gated noop, denied
RAW FunctionToolResultEvent.part = ToolReturnPart(tool_name='noop', content='user said no', outcome='denied')
emitted ToolResult.ok = True            ← WRONG (expected False)
render_event(...) → GREEN "noop" panel  ← WRONG (expected RED "noop (failed)")

$ adversarial: successful tool
ToolReturnPart.outcome == 'success'     ← distinguishes success from denial
```

**Other issues found**
1. **[BLOCKER — denied tool renders as success]** `agent/loop.py:_emit_for_tool_event`.
   `ok = isinstance(result, ToolReturnPart)` is too coarse. A gate **deny** comes back from
   pydantic-ai as a `ToolReturnPart` whose `outcome == "denied"` (content = the denial reason),
   not a `RetryPromptPart`. So a denied tool emits `ToolResult(ok=True)` and `render_tool_result`
   draws a GREEN success panel with body "user said no" — the user sees a denial rendered as if
   the tool succeeded. This contradicts the event contract (`entities/events.py:80`: "ok is False
   when the tool failed or was denied"), `render.py` (ok=False → red "(failed)"), and the SWE's
   own hand-off ("a denied tool still yields a sensible ToolResult/render (ok=False)").
   Fix: for a `ToolReturnPart`, set `ok = result.outcome != "denied"` (a successful return has
   `outcome == "success"`). Regression test: gated `noop` denied → assert the emitted
   `ToolResult.ok is False` AND `render_event` produces a "(failed)" panel (the existing
   `test_denial_feeds_a_tooldenied_result_back_to_the_model` only checks message history, never
   the emitted event's `ok`, which is how this slipped through).
2. [Note — non-blocking] Capstone integration test does not assert the new tool events; the task
   marked that "optional", so not a gate — but adding a `ToolResult` assertion there would lock in
   the contract end-to-end.

**VERDICT: FAIL**

### [SWE] 2026-06-20 04:10 — Fixes (Tester BLOCKER: denied tool rendered as success)

**Files modified**
- `src/decode/agent/loop.py` — `_emit_for_tool_event`: a `FunctionToolResultEvent` whose part is
  a `ToolReturnPart` now sets `ok = result.outcome == "success"` (was the too-coarse
  `ok = isinstance(result, ToolReturnPart)`). pydantic-ai 1.107.0 returns a gate **deny** as a
  `ToolReturnPart` with `outcome == "denied"` (content = the denial reason), NOT a
  `RetryPromptPart`, so the old check emitted `ToolResult(ok=True)` for a denial → a GREEN success
  panel. `outcome` is `Literal["success", "failed", "denied"]` (confirmed in
  `.venv/.../pydantic_ai/messages.py:1149`), default `"success"`; only `"success"` is ok.
  `RetryPromptPart` stays `ok=False`. Dedup-per-`tool_call_id` and render-on-completion behavior
  unchanged; docstring updated to explain the `outcome` keying.
- `tests/unit/decode/agent/test_loop.py` — added the regression test
  `test_denied_tool_emits_a_failed_result_and_renders_a_red_panel`: drives the real loop through a
  DENY (deny resolver + `FunctionModel` forcing the gated `noop`) and asserts the emitted
  `ToolResult.ok is False`, the tool body never ran, AND `render_event(result)` is a `Panel` with
  `border_style == "red"` whose title is `noop (failed)`. The approve-path tests
  (`test_gated_tool_is_announced_once_across_the_deferred_resume`,
  `test_tool_call_emits_started_and_result_events_and_renders_a_panel`) keep asserting `ok=True`.

**Tests**
- Unit: 350 passing, 0 failing (`make pre-commit`). Total suite incl. integration capstone: 351
  passing (was 350; +1 regression test). `make ci` green; `uv lock --check` resolves 166 pkgs.
- Red/green confirmed: with the old `isinstance`-only logic the new test fails on
  `ToolResult(...ok=True...)` for a denial; with the `outcome`-keyed fix it passes.

**Acceptance criteria**
- [x] Tester BLOCKER fixed: a denied gated tool emits `ToolResult(ok=False)` and renders the RED
      `(failed)` panel — verified by
      `tests/unit/decode/agent/test_loop.py::test_denied_tool_emits_a_failed_result_and_renders_a_red_panel`.
- [ ] Tester re-runs full QA suite and PASSES — pending re-review.
- [ ] PA re-runs acceptance review and ACCEPTS — pending.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS` — pending.

**Evidence**
```
$ make ci
uv lock --check        → Resolved 166 packages
ruff format --check    → 79 files already formatted
ruff check             → All checks passed!
pytest                 → 351 passed in 6.18s   (incl. integration capstone; 0 warnings)

$ uv run pytest .../test_denied_tool_emits_a_failed_result_and_renders_a_red_panel  (OLD logic)
FAILED — assert True is False
  ToolResult(name='noop', output='nope, not allowed', ok=True)   ← the bug
$ same test, fixed logic → 1 passed

$ e2e: real loop, DENY → render_event(ToolResult)
[emitted] name='noop' ok=False output='user said no'
╭─ noop (failed) ─────────────────╮   (border: red)
│ user said no                    │
╰─────────────────────────────────╯

$ e2e: real loop, APPROVE → render_event(ToolResult)   (no regression)
[emitted] name='noop' ok=True output='noop: hi'
╭─ noop ──────────────────────────╮   (border: green)
│ noop: hi                        │
╰─────────────────────────────────╯
```

**Notes**
- Scope kept to the one BLOCKER: only `loop.py` `_emit_for_tool_event` and the new regression
  test changed. `RetryPromptPart` (a `ModelRetry`/tool-error) still maps to `ok=False` unchanged.
- The Tester's non-blocking note #2 (assert tool events in the capstone integration test) was
  marked optional and is out of scope for this fix; not touched.
- `.decode/` stays absent; no network (`filterwarnings=["error"]` would error otherwise).
- NOT committed (awaiting Tester re-review).

### [Tester] 2026-06-20 05:05 — Re-QA (BLOCKER fix: denied tool rendered as success)

**Test summary**
- `uv lock --check` + format-check + lint-check: PASS (166 pkgs resolved; 79 files formatted; all
  ruff checks passed).
- Unit + integration (`make ci` → `pytest`): **351 passed / 0 failed** (was 350; +1 regression
  test). Exit 0.
- Warnings: 0 (`filterwarnings=["error"]` in effect — any warning would have errored the run).
- `.decode/` stays absent; `git diff` touches exactly the 10 expected files (+ untracked task 016);
  no `print()` in changed library code; no `git add -A` smell.
- `code-review` plugin enabled (advisory); manual checklist + independent adversarial drive are the
  authority and both pass.

**E2E adversarial pass** (re-drove break path 1 myself via the real `build_agent()`-shape loop +
FunctionModel + a gated `noop` that raises `ApprovalRequired` like a real tool — NOT trusting the
SWE's test alone; script at `/tmp/adversarial_qa.py`)
- Break path 1 — state edge, gated tool **DENIED** (`PermissionDecision.deny(reason=…)`): emitted
  `ToolResult.ok = False`, `output='ADVERSARIAL-DENY-REASON'`; `render_event` → `Panel`
  `border_style='red'`, title `noop (failed)`; tool body never ran (`noop: hi` absent). **PASS**
  (was the FAIL — now fixed). Regression guard: the denial reason still reaches the model on the
  resume leg (`ToolReturnPart` content == `ADVERSARIAL-DENY-REASON`; `noop: hi` absent) — deny→model
  path NOT regressed. **PASS.**
- Break path 2 — APPROVE path (regression guard): `PermissionDecision.allow()` → `ToolResult.ok =
  True`, `output='noop: hi'` (tool executed), green panel, announced **once** (`ToolCallStarted` ×1
  across the deferred-pause + resume legs — dedup holds). **PASS.**
- Break path 3 — failure mode, tool raises `ModelRetry`: → `RetryPromptPart` → `ToolResult.ok =
  False`, red panel. **PASS.**

**Acceptance criteria**
- [x] PASS — Blocker 1: `noop` not registered on the production agent. Evidence:
      `build_agent()._function_toolset.tools` == exactly the 9 real tools
      `{ask_user,bash,edit,glob,grep,read,todo_write,web_fetch,write}`, `noop` absent; every `noop`
      mention in `registry.py`/`factory.py`/`__init__.py` is a comment/docstring (no `ToolSpec`,
      import, or `register_noop` call in the production path). `register_noop` referenced only by
      `noop.py` (def), `test_loop.py:88`, `test_app_e2e.py:98`.
      `test_registry_does_not_expose_the_scaffolding_noop_tool` +
      `test_register_tools_registers_every_spec_on_the_agent` pass.
- [x] PASS — Registry/factory tests assert `noop` absent; gated-flow tests keep working via
      test-scoped `register_noop` (`gated_agent` fixture). 37 loop/registry/noop/e2e/capstone tests
      pass.
- [x] PASS — Tester re-runs full QA suite and PASSES. The BLOCKER from the prior cycle (denied tool
      emitted `ToolResult(ok=True)` → green success panel) is fixed: `_emit_for_tool_event` now keys
      `ok` off `result.outcome == "success"` for a `ToolReturnPart` (deny → `outcome=="denied"` →
      `ok=False`); `RetryPromptPart` → `ok=False`. Verified by the new regression test AND my own
      independent adversarial drive (all 3 break paths green).
- [ ] PA re-runs acceptance review and ACCEPTS — pending (hand off).
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS` — pending.

**Earlier-verified items re-confirmed (no regression)**
- noop absent from production agent (exactly 9 real tools) — re-verified via `build_agent()`.
- `ToolCallStarted` emitted exactly once per call across the gated approve/resume replay (dedup) —
  `test_gated_tool_is_announced_once_across_the_deferred_resume` + adversarial approve case.
- Capstone integration test drives the real `build_agent()` stack — passes.
- Deferred-approval approve/deny, steering-at-resume, single-flight not regressed —
  `test_approval_resumes_and_executes_the_tool`, `test_denial_feeds_a_tooldenied_result_back_to_the_model`,
  `test_steering_message_reaches_the_model_at_the_deferred_resume`,
  `test_single_flight_lock_spans_the_whole_multi_leg_deferred_turn` all pass.

**Evidence**
```
$ make ci
uv lock --check        → Resolved 166 packages
ruff format --check    → 79 files already formatted
ruff check             → All checks passed!
pytest                 → 351 passed in 6.21s   (exit 0; 0 warnings)

$ uv run python /tmp/adversarial_qa.py   (independent re-drive of the real loop)
=== BREAK PATH 1: DENIED gated tool ===
  emitted ToolResult.ok = False  output='ADVERSARIAL-DENY-REASON'
  panel.border_style = red
  denial returns to model = ['ADVERSARIAL-DENY-REASON']
  PASS: ok=False + red (failed) panel + denial reaches model
=== APPROVE path (regression guard) ===
  emitted ToolResult.ok = True  output='noop: hi'  (started x1)
  panel.border_style = green
  PASS: ok=True + green panel + tool executed + announced once (dedup holds)
=== ModelRetry path (ok=False) ===
  emitted ToolResult.ok = False  output='flaky tool wants a retry\n\nFix the errors and try again.'
  PASS: ModelRetry -> ok=False + red panel
ALL ADVERSARIAL CASES PASSED

$ build_agent()._function_toolset.tools
['ask_user','bash','edit','glob','grep','read','todo_write','web_fetch','write']   (noop absent; 9 tools)
```

**Other issues found**
- None blocking. The prior non-blocking note #2 (assert tool events inside the capstone integration
  test) was marked optional and remains a fine follow-up — not a gate.

**Rollup status**: BLOCKER 1 (denied-tool-renders-green) FIXED + regression-tested + independently
re-verified. NIT-1 (emit `ToolCallStarted`/`ToolResult` from the loop) satisfied. NIT-2 (`files.py`
fuzzy-edit cold-path note) and NIT-4 (glossary `Decision Channel`/`Steering`/`Follow-up`/`Deferred
Approval`/`AskUser` rows) satisfied. (NIT-3, the stale PR-body note, is orchestrator-owned, out of
SWE scope — not a gate.)

**VERDICT: PASS** — hand off for commit.

