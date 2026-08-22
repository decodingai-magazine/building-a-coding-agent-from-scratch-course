"""The Headless Runtime — a plain ``asyncio.run`` around ``build_agent()`` for ``decode run`` (ADR-0019 §1).

One entrypoint, :func:`~decode.runtime.headless.run_headless_task`: bypass permissions, ``ask_user``
as a no-op, the sandbox Workspace + host-side Hand-back, and Opik tracing. Beside it sits the
Recording Seam (:mod:`decode.runtime.recording`) — the one function that decides whether a built
agent runs wrapped in ``kitaru_pydantic_ai.KitaruAgent``, and decode's only kitaru import site
outside the Environment Bucket (ADR-0019 §3). The Kitaru Durable Flow
this package used to host (checkpoints, HITL waits, ``decode replay``) is deleted — upstream removed
the primitives. :mod:`decode.cli` still imports this package lazily inside the ``run`` subcommand, so
the REPL path loads no headless machinery.
"""

from __future__ import annotations

from decode.runtime.headless import RUN_SPAN_NAME, run_headless_task

__all__ = [
    "RUN_SPAN_NAME",
    "run_headless_task",
]
