"""The Headless Runtime — Kitaru Durable Flow + ``decode run`` (ADR-0008, step 7).

decode's second entry path: a Kitaru ``@flow`` that runs the same ``build_agent()`` autonomously
with checkpoints + replay, for unattended runs (``decode run "<task>"`` today; a deployed endpoint
later). The interactive TUI is untouched.

Two flows, one ``build_agent()``: :func:`run_agent_task` is the **bypass** run (task 058) — every
tool inline, no human; :func:`run_hitl_agent_task` is the **HITL** run (task 059) — a gating gate so
mutating tools and ``ask_user`` pause on durable Kitaru waits resolved out-of-band.

Importing this package pulls in ``kitaru`` (a heavy zenml/temporalio stack), so :mod:`decode.cli`
imports it **lazily** inside the ``run`` subcommand — the REPL path never imports kitaru.
"""

from __future__ import annotations

from decode.runtime.flow import (
    RUNTIME_AGENT_NAME,
    HitlRunResult,
    run_agent_task,
    run_agent_task_hitl,
    run_hitl_agent_task,
)

__all__ = [
    "RUNTIME_AGENT_NAME",
    "HitlRunResult",
    "run_agent_task",
    "run_agent_task_hitl",
    "run_hitl_agent_task",
]
