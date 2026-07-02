"""The sandbox executors — real :class:`~decode.tools.exec.CommandExecutor`s for ``bash`` (ADR-0011).

The reserved home for the two sandboxed executors that sit behind the ADR-0002 ``run`` seam
(``tools/exec.py::CommandExecutor``), selected by ``SANDBOX_MODE``: :class:`DockerExecutor` (one
session-persistent local container over the bind-mounted cwd, task 072) and :class:`ModalExecutor`
(one session-persistent remote empty-scratch ``modal.Sandbox``, task 073). The ``none`` default keeps
:class:`~decode.tools.exec.LocalExecutor` (a host subprocess) and never imports this package.

Importing this package pulls in **no** heavy backend SDK: :class:`ModalExecutor` imports ``modal``
only lazily on first use, and :class:`DockerExecutor` shells out to the ``docker`` CLI — so importing
``decode.sandbox`` stays off the interactive ``none``-mode REPL path's cost. The lazy selection seam
(``select_executor(mode)``) and the mode-specific ``bash`` description land in task 074.
"""

from __future__ import annotations

from decode.sandbox.docker_executor import DockerExecutor
from decode.sandbox.modal_executor import ModalExecutor

__all__ = ["DockerExecutor", "ModalExecutor"]
