---
id: 008-tools-bash
feature: m1-vanilla-agent
status: pending
---

# Tools: bash execution

## Scope
Gated shell execution with a local-executor seam (M8 swaps it for a sandbox).

## Acceptance criteria
- [ ] `tools/exec.py` defines the executor seam; `tools/bash.py` runs via `asyncio` subprocess.
- [ ] `settings.bash_timeout_s` enforced; output truncated via `tools/truncate.py` (2000 lines / 50 KB) with overflow spilled to a temp-file path in the result.
- [ ] Gated (no dangerous-command classifier in v1 — human approves every call).

## Out of scope
- Background jobs; OS sandbox / dangerous-command classifier (M8).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Executor seam is the M8 sandbox insertion point.
