"""The Headless Runtime — Kitaru Durable Flow + ``decode run`` (ADR-0008).

Two flows, one ``build_agent()``: :func:`run_agent_task` (bypass — every tool inline) and
:func:`run_hitl_agent_task` (durable HITL waits resolved out-of-band). Importing this package pulls
in ``kitaru`` (a heavy zenml/temporalio stack), so :mod:`decode.cli` imports it lazily inside the
``run`` subcommand — the REPL path never imports kitaru.
"""

from __future__ import annotations

from decode.runtime.flow import (
    RUNTIME_AGENT_NAME,
    HitlRunResult,
    ReplayResult,
    is_hitl_execution,
    replay_agent_task,
    run_agent_task,
    run_agent_task_hitl,
    run_hitl_agent_task,
)

__all__ = [
    "RUNTIME_AGENT_NAME",
    "HitlRunResult",
    "ReplayResult",
    "is_hitl_execution",
    "replay_agent_task",
    "run_agent_task",
    "run_agent_task_hitl",
    "run_hitl_agent_task",
]
