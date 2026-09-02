---
status: done
feature: remote-headless-article-6
---

# evals/harness: retire the four `runtime/flow.py` docstring references

Tags: `docs`, `hygiene`
Depends on: None
Blocks: —

`runtime/flow.py` died in task 131 (ADR-0019 §1); `evals/harness/driver.py` (2×) and
`evals/harness/sandbox.py` (2×) still cited it as the pattern they mirror.

## Acceptance Criteria

- [x] All four now name `runtime/headless.py` (`_prepare_headless_tool_scope` / `run_headless_task`); `grep -rn "flow\.py" src evals` is empty.

## Log
### [SWE] 2026-09-02 20:00 — Done
Docstring-only change.
