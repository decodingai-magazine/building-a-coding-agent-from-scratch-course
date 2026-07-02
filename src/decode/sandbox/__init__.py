"""The sandbox executors — real :class:`~decode.tools.exec.CommandExecutor`s for ``bash`` (ADR-0011).

The reserved home for the two sandboxed executors that sit behind the ADR-0002 ``run`` seam
(``tools/exec.py::CommandExecutor``), selected by ``SANDBOX_MODE``: :class:`DockerExecutor` (one
session-persistent local container over the bind-mounted cwd, task 072) and — later — ``ModalExecutor``
(a remote empty-scratch ``modal.Sandbox``, task 073). The ``none`` default keeps
:class:`~decode.tools.exec.LocalExecutor` (a host subprocess) and never imports this package.

The lazy selection seam (``select_executor(mode)``) and the mode-specific ``bash`` description land in
task 074; importing this package must stay off the interactive ``none``-mode REPL path.
"""

from __future__ import annotations

from decode.sandbox.docker_executor import DockerExecutor

__all__ = ["DockerExecutor"]
