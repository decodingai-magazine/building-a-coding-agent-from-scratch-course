---
id: 021-orchestration-and-sleep-tools
feature: permission-system-agents-catalog
status: pending
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

- [ ] `enter_plan_mode` sets the gate to `PLAN` and returns a confirmation; afterwards a `bash`/
      `write` call is DENY (plan mode). Driven through the real loop.
- [ ] `exit_plan_mode` with an **approve** answer sets the gate to `EDIT` and returns the approved
      message; a subsequent file-edit auto-allows (edit mode). With a **deny** answer it stays `PLAN`
      and a subsequent file-edit is DENY. Driven through the loop with a scripted decision resolver.
- [ ] `enter_plan_mode` / `exit_plan_mode` are **ungated** (never emit `PermissionRequested`) and are
      callable while in plan mode (plan mode does not block them). Loop-tested.
- [ ] `sleep(0.01)` returns a confirmation; `sleep(10_000)` is capped at `settings.sleep_max_s`
      (asserted via a patched/short cap so the test is fast); `sleep(-1)` raises `ModelRetry`.
      Unit-tested with no real long wait.
- [ ] `settings.sleep_max_s` exists, is mirrored in `.env.example`, read only via the settings
      singleton.
- [ ] **Working looks like:** in default mode the model calls `enter_plan_mode`, its `write` attempt
      is auto-denied with the plan-mode reason; it calls `exit_plan_mode(plan=…)`, the human approves,
      and a `write` then auto-allows (edit mode).
- [ ] `make ci` green, 0 warnings; `tests/unit/decode/tools/` covers all three tools.

## Out of scope
- The Shift+Tab keybind / `/mode` command (task 022) — these tools are the *model-driven* path; the
  human-driven path is task 022.
- Rich plan rendering beyond presenting the plan text + the approve cue.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §8. Round-2 lock: `exit_plan_mode` does a HITL approval via the Decision Channel
and, on approve, lands in **EDIT** mode (not default) so the agent can implement the approved plan; on
deny it stays in plan mode. All three tools are ungated.
