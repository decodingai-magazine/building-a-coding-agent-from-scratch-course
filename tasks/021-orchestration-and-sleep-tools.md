---
id: 021-orchestration-and-sleep-tools
feature: permission-system-agents-catalog
status: done
---

# Orchestration tools: enter_plan_mode, exit_plan_mode (HITL), sleep

Implements [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md) §8.
Depends on: 017 · Blocks: 022

## Scope

Three small model-callable tools. They use/mutate session state via `ctx.deps`; they touch no
filesystem and are **ungated** (never raise `ApprovalRequired`, never reach the permission gate).

- **`tools/orchestration.py`** —
  - `enter_plan_mode(ctx)`: calls `ctx.deps.gate.set_mode(PermissionMode.PLAN)`; returns a short
    confirmation ("Entered plan mode: read-only. Present your plan, then call exit_plan_mode.").
  - `exit_plan_mode(ctx, plan: str)`: **asks the human to approve leaving plan mode** via the existing
    Decision Channel (reuse `deps.resolve_user_question` — render the plan + an "Approve this plan and
    start editing? [y/N]" cue; parse the typed line as y/N). On **approve** → `gate.set_mode(EDIT)`
    and return "Plan approved — entering edit mode." On **deny** → stay in `PLAN` and return "Plan not
    approved — refine it and call exit_plan_mode again." Ungated like `ask_user` (it IS a HITL tool,
    so routing it through the permission gate would double-prompt). A cancelled request
    (`asyncio.CancelledError`) maps to a clean model-readable message, never a hang.
- **`tools/sleep.py`** — `sleep(ctx, seconds: float)`: `await asyncio.sleep(min(seconds,
  settings.sleep_max_s))`; returns a confirmation ("Slept N s."). Add `settings.sleep_max_s: float`
  (default e.g. 60.0) to `config/settings.py` (+ `.env.example`). Reject negative input with
  `ModelRetry` ("seconds must be ≥ 0").
- **`tools/registry.py`** — register the three tools with their `ToolKind` and the ungated path
  (mirror `ask_user`'s ungated treatment — they never raise `ApprovalRequired`). Their names are the
  constants the catalog (task 019) validates against. Include them in the relevant built-in agents'
  allowlists (build: all three; plan: enter/exit).

## Acceptance criteria

- [x] `enter_plan_mode` sets the gate to `PLAN` and returns a confirmation; afterwards a `bash`/
      `write` call is DENY (plan mode). Driven through the real loop.
- [x] `exit_plan_mode` with an **approve** answer sets the gate to `EDIT` and returns the approved
      message; a subsequent file-edit auto-allows (edit mode). With a **deny** answer it stays `PLAN`
      and a subsequent file-edit is DENY. Driven through the loop with a scripted decision resolver.
- [x] `enter_plan_mode` / `exit_plan_mode` are **ungated** (never emit `PermissionRequested`) and are
      callable while in plan mode (plan mode does not block them). Loop-tested.
- [x] `sleep(0.01)` returns a confirmation; `sleep(10_000)` is capped at `settings.sleep_max_s`
      (asserted via a patched/short cap so the test is fast); `sleep(-1)` raises `ModelRetry`.
      Unit-tested with no real long wait.
- [x] `settings.sleep_max_s` exists, is mirrored in `.env.example`, read only via the settings
      singleton.
- [x] **Working looks like:** in default mode the model calls `enter_plan_mode`, its `write` attempt
      is auto-denied with the plan-mode reason; it calls `exit_plan_mode(plan=…)`, the human approves,
      and a `write` then auto-allows (edit mode).
- [x] `make ci` green, 0 warnings; `tests/unit/decode/tools/` covers all three tools.

## Out of scope
- The Shift+Tab keybind / `/mode` command (task 022) — these tools are the *model-driven* path; the
  human-driven path is task 022.
- Rich plan rendering beyond presenting the plan text + the approve cue.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §8. Round-2 lock: `exit_plan_mode` does a HITL approval via the Decision Channel
and, on approve, lands in **EDIT** mode (not default) so the agent can implement the approved plan; on
deny it stays in plan mode. All three tools are ungated.

### [SWE] 2026-06-25 14:30 — Implementation

**Files modified**
- `src/decode/tools/orchestration.py` — added `enter_plan_mode` (sets gate `PLAN`) + `exit_plan_mode`
  (HITL approve→`EDIT` / deny→stays `PLAN`, rides the ask_user Decision Channel; headless/cancelled →
  clean `ModelRetry`, mode untouched), kept the existing name constants.
- `src/decode/tools/sleep.py` — new `sleep(ctx, seconds)`: `await asyncio.sleep(min(seconds,
  settings.sleep_max_s))`; negative → `ModelRetry`. Ungated.
- `src/decode/tools/registry.py` — registered the three tools as `ToolKind.OTHER` on the ungated path
  (mirror `ask_user`: never raise `ApprovalRequired`, never reach the gate); updated module docstring.
- `src/decode/tools/__init__.py` — refreshed the `KNOWN_TOOL_NAMES` comment (orchestration names are
  now registered specs; the union with `ORCHESTRATION_TOOL_NAMES` is belt-and-suspenders).
- `src/decode/config/settings.py` + `.env.example` — added `sleep_max_s: float = 60.0` (+ mirror).
- `tests/unit/decode/tools/test_orchestration.py` — new: direct unit tests (mode flips, surfaced
  plan+cue, y/N parsing, headless/cancelled `ModelRetry`) + loop-driven tests through real
  `build_agent`+`AgentTurnHandler`+gate (enter→write DENY; exit-approve→write ALLOW; exit-deny→write
  DENY; ungated/no `PermissionRequested`).
- `tests/unit/decode/tools/test_sleep.py` — new: cap, sub-cap, negative→`ModelRetry`, zero boundary
  (asyncio.sleep patched — no real wait).
- `tests/unit/decode/tools/test_registry.py` — extended expected tool set + kinds with the three.

**Tests**
- Unit: 524 passing, 0 failing (37 in the three touched/added tool test files) — `make ci` output below.
- Integration: 1 passing (capstone) — `make integration-tests` green; no infra changes.

**Acceptance criteria** — all met (no `[HUMAN]` items):
- [x] enter_plan_mode → PLAN, subsequent write DENY (real loop) — `test_orchestration.py::test_enter_plan_mode_through_the_loop_denies_a_subsequent_write`
- [x] exit_plan_mode approve→EDIT (file-edit auto-allows) / deny→PLAN (DENY) — `::test_exit_plan_mode_approve_through_the_loop_allows_a_subsequent_write`, `::test_exit_plan_mode_deny_through_the_loop_keeps_a_write_denied`
- [x] ungated, no PermissionRequested, callable in plan mode — `::test_exit_plan_mode_is_ungated_and_callable_in_plan_mode`
- [x] sleep confirmation / cap / negative→ModelRetry — `test_sleep.py`
- [x] `settings.sleep_max_s` exists + mirrored in `.env.example`, read via singleton
- [x] working-looks-like flow — proven by the e2e smoke (Evidence)
- [x] `make ci` green, 0 warnings; `tests/unit/decode/tools/` covers all three

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check all clean ...
============================= 524 passed in 6.53s ==============================

$ uv run python scratchpad/e2e_orchestration.py   # real build_agent+handler+gate, scripted model
[1] after enter_plan_mode: mode='plan'  blocked.txt exists? False  (expect PLAN / False)
[2] after exit_plan_mode(approve): mode='edit'  allowed.txt='hello'  (expect EDIT / 'hello')
[3] after exit_plan_mode(deny): mode='plan'  denied.txt exists? False  (expect PLAN / False)
[4] sleep(9999) capped → tool result: 'Slept 0.02 s.'  (expect 'Slept 0.02 s.')
```

**Notes**
- Ungating is structural, not a skip-list: like `ask_user`, these tools simply never raise
  `ApprovalRequired`, so the loop's deferred/gate path is never entered for them.
- `SLEEP_TOOL_NAME` intentionally stays in `orchestration.py` (the one place the `tools` package owns
  the orchestration name constants the catalog loader validates against); `sleep.py` re-exports it.
- Not committed — handing to the Tester first.

### [Tester] 2026-06-25 19:29 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 95 files clean; `ruff check` clean; pre-commit green)
- Unit tests: 523 passed / 0 failed
- Integration tests: 1 passed / 0 failed (M1 capstone)
- Warnings: 0 (`filterwarnings = ["error"]` in pyproject — any warning would have failed the run)

**E2E adversarial pass** (real `build_agent` + `AgentTurnHandler` + gate; `FunctionModel`, no network — 16 checks)
- Happy path: in PLAN, model calls `exit_plan_mode(plan="do it")` → approve `y` → gate EDIT → subsequent `write` auto-allows (file on disk) → PASS
- Break 1 (state edge — enter then mutate): `enter_plan_mode` → subsequent `bash` is auto-DENY, no `PermissionRequested`, model told "plan mode" → PASS
- Break 2 (approve path + EDIT scoping): approve → EDIT; `write` auto-allows BUT `bash` (kind OTHER) still ASKS — EDIT is not a blanket allow → PASS
- Break 3 (deny path): deny `n` → stays PLAN → subsequent `write` DENY → PASS
- Break 4 (failure mode — headless through the loop): resolver raises `NoInteractiveUserError` → clean `ModelRetry` fed back, gate mode UNCHANGED (PLAN), subsequent write still blocked → PASS
- Break 5 (failure mode — cancelled through the loop): resolver raises `asyncio.CancelledError` → no hang (completes < 5s under `wait_for`), gate mode UNCHANGED (PLAN) → PASS
- Break 6 (ungated in plan mode): `exit_plan_mode` while PLAN emits `AskUserRequested`, never `PermissionRequested` → PASS
- Break 7 (boundary input — empty plan): `plan=""` → still asks (cue `[y/N]` present), approve → EDIT → PASS
- Break 8 (large input — sleep cap): `sleep(10_000)` with cap patched to 0.02 → ungated (no prompt), returns "Slept 0.02 s." in 0.028s (no real 10000s wait) → PASS
- Break 9 (malformed input — negative): `sleep(-1)` through loop → `ModelRetry` ("seconds must be ≥ 0") fed back → PASS
- Break 10 (boundary — infinity): `sleep(inf)` → `min(inf, 60.0)=60.0`, cap holds → PASS
- Break 11 (single-flight): 2nd concurrent `DecisionChannel.request()` raises `RuntimeError` (no collision between `exit_plan_mode` HITL / permission ask / `ask_user`) → PASS
- Break 12 (tool visibility): build has all three; plan has enter/exit (NOT sleep); explore & code-reviewer have none → PASS
- Break 13 (malformed input — NaN): `sleep(nan)` → **FAIL** (see below)

**Acceptance criteria** — all 7 verified PASS:
- [x] PASS — `enter_plan_mode` → PLAN; subsequent bash/write DENY through the real loop — adversarial Break 1; `test_orchestration.py::test_enter_plan_mode_through_the_loop_denies_a_subsequent_write`
- [x] PASS — `exit_plan_mode` approve→EDIT (file-edit auto-allows) / deny→PLAN (DENY), scripted resolver — Breaks 2-3; `::test_exit_plan_mode_approve_through_the_loop_allows_a_subsequent_write`, `::test_exit_plan_mode_deny_through_the_loop_keeps_a_write_denied`
- [x] PASS — ungated (no `PermissionRequested`), callable in plan mode — Break 6; `::test_exit_plan_mode_is_ungated_and_callable_in_plan_mode`
- [x] PASS — `sleep(0.01)` confirms / `sleep(10_000)` capped / `sleep(-1)` → `ModelRetry`, no real wait — Breaks 8-9; `test_sleep.py` (all 5)
- [x] PASS — `settings.sleep_max_s` exists (`config/settings.py:36`, default 60.0), mirrored in `.env.example:39-40`, read via singleton (`sleep.py:50`)
- [x] PASS — working-looks-like flow (default → enter_plan_mode → write auto-denied → exit_plan_mode approve → write auto-allows) — adversarial happy path + Break 1
- [x] PASS — `make ci` green, 0 warnings; `tests/unit/decode/tools/` covers all three (`test_orchestration.py`, `test_sleep.py`, `test_registry.py`)

**Evidence**
```
$ make format-check && make lint-check   → 95 files already formatted; All checks passed!
$ make unit-tests                        → 523 passed in 6.42s
$ make integration-tests                 → 1 passed in 1.42s
$ uv run python <adversarial_e2e.py>     → 16 checks, 1 FAIL (sleep(nan) hang)
  [PASS] enter_plan_mode -> bash DENY (auto, no prompt)        mode=plan ...
  [PASS] approve -> EDIT: write auto-allows, bash still ASKS   mode=edit wrote=True bash_prompts=1
  [PASS] headless exit through loop -> ModelRetry, mode UNCHANGED (PLAN)
  [PASS] cancelled exit through loop -> no hang, mode UNCHANGED (PLAN)
  [PASS] sleep(10_000) capped to 0.02, no 10000s wait          elapsed=0.028s
  [PASS] sleep(inf) capped to sleep_max_s                      inf->60.0
  [FAIL] sleep(nan) hangs the turn (asyncio.sleep(nan) never returns)
```

**FAIL — adversarial break path (not an AC, but a reachable hang per the QA rubric)**
- [ ] FAIL — `sleep(nan)` hangs the turn forever.
      Reachable: pydantic-ai/pydantic_core accepts the `NaN` JSON token and validates it as
      `float` nan (confirmed: a `FunctionModel` tool call `{"seconds": NaN}` reaches `sleep` and
      returns "Slept nan s."); `json.dumps(float('nan'))` even emits a bare `NaN`.
      Cap is defeated: `min(nan, settings.sleep_max_s) == nan`, and `asyncio.sleep(nan)` never
      returns (proved: `asyncio.wait_for(asyncio.sleep(nan), 1.5)` times out). This violates the
      tool's own documented invariant ("a model can never stall a turn indefinitely", `sleep.py:3-6`)
      — the exact failure the cap exists to prevent — and the turn cannot reach an abort boundary.
      Expected: a non-finite `seconds` is rejected like a negative one (`ModelRetry`), or clamped.
      Fix (one line, `src/decode/tools/sleep.py:47`): change the guard from `if seconds < 0:` to
      `if not (seconds >= 0):` — this rejects both negatives AND `nan` (since `nan >= 0` is False)
      while still letting `inf` fall through to be capped by `min`. Add a `sleep(nan)` regression
      test alongside the existing `sleep(-1)` one in `test_sleep.py`.

**Other issues found**
- None blocking beyond the NaN hang above. Note for the PA/SWE: the negative-input AC already
  signals intent to reject nonsensical numeric input, so the NaN guard is consistent with that
  intent, not scope creep.

**Housekeeping verified**
- No `scratchpad/e2e_orchestration.py` exists in the repo tree (git status clean of it; `find` and
  `ls scratchpad/` both empty) — nothing to exclude from the commit.
- `git diff` touches only the seven expected files + three new files (no stray/unrelated changes);
  no `print()` in library code (logger used throughout); all tool signatures typed.

**VERDICT: FAIL** — 1 issue: `sleep(nan)` reachable hang (one-line fix `sleep.py:47` + regression
test). All 7 acceptance criteria and the other 15 adversarial checks pass; suite green, 0 warnings.

### [Tester] 2026-06-25 19:36 — Re-QA (fix verification)

**Fix reviewed** — `src/decode/tools/sleep.py:53`: guard broadened from `if seconds < 0:` to
`if not (seconds >= 0):` (rejects negatives AND `nan`, since `nan >= 0` is False; `inf` is `>= 0`
so it still falls through to `min(inf, sleep_max_s)`). Message now "seconds must be a non-negative
number". Two regression tests added in `test_sleep.py`
(`::test_sleep_rejects_nan_seconds_without_hanging`, `::test_sleep_caps_infinity_at_settings_sleep_max_s`).

**Test summary**
- `make pre-commit` (format + lint + unit): PASS — `ruff format --check` 95 files clean, `ruff check`
  clean, **525 unit passed**
- Integration: 1 passed (M1 capstone)
- Total: 526 (525 unit + 1 integration). Warnings: 0 (`filterwarnings = ["error"]`)
- Targeted: `test_sleep.py + test_orchestration.py + test_registry.py` → 39 passed

**Independent re-drive — REAL `sleep` tool, UN-MOCKED `asyncio.sleep`, 2s timeout (proves no hang)**
- `sleep(nan)` → `ModelRetry("seconds must be a non-negative number")` in **0.013s** (was: never
  returns) → PASS — the previously-found hang is fixed
- `sleep(inf)` (cap patched to 0.02) → `min(inf, 0.02)=0.02`, returns "Slept 0.02 s." in 0.023s → PASS
- `sleep(-5)` → `ModelRetry` → PASS
- `sleep(0.01)` (real sub-cap wait) → "Slept 0.01 s." in 0.011s → PASS
- `sleep(-inf)` (bonus edge) → `ModelRetry` → PASS

**Rest of task 021 re-confirmed green** — the fix touched only `sleep.py` + `test_sleep.py`;
`orchestration.py` / `registry.py` / allowlists are unchanged from the first QA round. The 7
acceptance criteria, ungated `exit_plan_mode` HITL (mode flips, headless/cancelled → clean
`ModelRetry`, single-flight), and tool visibility all remain verified (full suite green, 0 warnings).

**Housekeeping** — no `scratchpad/` artifact in the repo tree; `git status` shows only the expected
seven modified files + three new ones.

**Evidence**
```
$ make pre-commit        → 95 files formatted; All checks passed!; 525 passed in 6.52s
$ make integration-tests → 1 passed in 1.26s
$ uv run python <reqa_sleep.py>   # real tool, un-mocked asyncio.sleep
  [PASS] sleep(nan)  -> ModelRetry, no hang   0.013s
  [PASS] sleep(inf)  -> capped to 0.02         0.023s
  [PASS] sleep(-5)   -> ModelRetry
  [PASS] sleep(0.01) -> real sub-cap wait      0.011s
  [PASS] sleep(-inf) -> ModelRetry
  5 checks, 0 FAIL
```

**VERDICT: PASS** — the `sleep(nan)` hang is fixed and proven gone with un-mocked `asyncio.sleep`;
all 7 acceptance criteria, every adversarial break path, the full suite (526), and 0 warnings hold.
Ready for PA acceptance review.

### [SWE] 2026-06-25 20:10 — Fixes (NaN hang)

**Issue addressed**
- Tester Break 13: `sleep(nan)` hung the turn forever. pydantic_core accepts the JSON `NaN` token and
  validates it as `float` nan, so a model tool call `{"seconds": NaN}` reached `sleep`;
  `min(nan, sleep_max_s) == nan` and `asyncio.sleep(nan)` never returns — defeating the cap and the
  tool's own documented invariant.

**Files modified**
- `src/decode/tools/sleep.py` — broadened the negative guard from `if seconds < 0:` to
  `if not (seconds >= 0):`. This rejects negatives AND `nan` (`nan >= 0` is `False`) while `inf`
  (which is `>= 0`) still falls through to be clamped by `min(inf, sleep_max_s)`. `ModelRetry`
  message updated to "seconds must be a non-negative number". Module + function docstrings updated to
  state the nan rejection and why (clamping nan can't work).
- `tests/unit/decode/tools/test_sleep.py` — regression tests added next to the existing `sleep(-1)`
  case:
  - `test_sleep_rejects_nan_seconds_without_hanging` — `sleep(nan)` raises `ModelRetry` and
    `asyncio.sleep` is never awaited (no hang).
  - `test_sleep_caps_infinity_at_settings_sleep_max_s` — locks in that `inf` is still clamped to the
    cap (`min(inf, 60.0) == 60.0`), so the nan fix never regresses inf.

**Tests**
- Unit: 525 passing, 0 failing (was 523; +2 regression tests). `test_sleep.py`: 7 passing.
- Integration: 1 passing (M1 capstone). `make ci` green, 0 warnings.

**Evidence**
```
$ make integration-tests && make ci
tests/integration/test_milestone1_capstone.py .                          [100%]
============================== 1 passed in 1.31s ===============================
uv lock --check        → Resolved 166 packages
ruff format --check    → 95 files already formatted
ruff check             → All checks passed!
============================= 526 passed in 6.28s ==============================

$ uv run python - (real un-mocked asyncio.sleep)
[PASS] sleep(nan) -> ModelRetry: seconds must be a non-negative number  (0.013s, no hang)
[PASS] sleep(inf) -> 'Slept 0.01 s.'  (capped, 0.012s)
[PASS] sleep(-5) -> ModelRetry
```

**Notes**
- One-line behavioral change exactly as the Tester scoped it; no other source touched. Not committed —
  handing back to the Tester for re-review.
