"""The Headless Runtime — Kitaru Durable Flow + ``decode run`` (ADR-0008, step 7).

decode's second entry path: a Kitaru ``@flow`` that runs the same ``build_agent()`` autonomously
with checkpoints + replay, for unattended runs (``decode run "<task>"`` today; a deployed endpoint
later). The interactive TUI is untouched.

Importing this package pulls in ``kitaru`` (a heavy zenml/temporalio stack), so :mod:`decode.cli`
imports it **lazily** inside the ``run`` subcommand — the REPL path never imports kitaru.
"""

from __future__ import annotations

from decode.runtime.flow import RUNTIME_AGENT_NAME, run_agent_task

__all__ = ["RUNTIME_AGENT_NAME", "run_agent_task"]
