"""Names of the orchestration + sleep control tools (ADR-0003 §8).

These three tools steer the *session* rather than touch the filesystem: ``enter_plan_mode`` /
``exit_plan_mode`` flip the gate's mode (with a human-in-the-loop approval on exit) and
``sleep`` is a bounded ``await asyncio.sleep(...)``. All three are **ungated** — they reach no
permission gate (ADR-0003 §8) — so they only need their *names* declared here for now.

Task 021 registers the actual tool functions; task 019 (the agents catalog loader) needs only the
names so the built-in agents' ``tools`` allowlists (``build``/``plan`` list these) validate against
the known-name set **regardless of task ordering**. Declaring the names here — the one place the
``tools`` package owns tool-name constants — keeps that validation honest without pulling the
unimplemented tool bodies forward.
"""

from __future__ import annotations

ENTER_PLAN_MODE_TOOL_NAME = "enter_plan_mode"
EXIT_PLAN_MODE_TOOL_NAME = "exit_plan_mode"
SLEEP_TOOL_NAME = "sleep"

# The orchestration tool names as a frozenset — the agents-catalog loader unions these with the
# registry's tool names to form the allowlist-validation set (task 021 registers the functions).
ORCHESTRATION_TOOL_NAMES: frozenset[str] = frozenset(
    {ENTER_PLAN_MODE_TOOL_NAME, EXIT_PLAN_MODE_TOOL_NAME, SLEEP_TOOL_NAME}
)
