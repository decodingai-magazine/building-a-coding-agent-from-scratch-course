"""The Headless Runtime — a plain ``asyncio.run`` around ``build_agent()`` for ``decode run`` (ADR-0019 §1).

One entrypoint, :func:`~decode.runtime.headless.run_headless_task`: bypass permissions, ``ask_user``
as a no-op, the sandbox Workspace + host-side Hand-back, and Opik tracing. Beside it sits the
Recording Seam (:mod:`decode.runtime.recording`) — the one function that decides whether a built
agent runs wrapped in ``kitaru_pydantic_ai.KitaruAgent``, and decode's only kitaru import site
outside the Environment Bucket (ADR-0019 §3). The Kitaru Durable Flow
this package used to host (checkpoints, HITL waits, ``decode replay``) is deleted — upstream removed
the primitives. The REPL records too (ADR-0019 §3), so :mod:`decode.tui.app` imports the seam and —
package ``__init__`` being what it is — this module with it; what the REPL still never pulls in is
kitaru, because the seam's kitaru imports live inside its configured branch (proved in a fresh
interpreter by ``tests/unit/decode/test_cli.py``).
"""

from __future__ import annotations

from decode.runtime.headless import RUN_SPAN_NAME, run_headless_task

__all__ = [
    "RUN_SPAN_NAME",
    "run_headless_task",
]
