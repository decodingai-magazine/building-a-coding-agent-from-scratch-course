"""The sandbox executor + the mode selection seam for ``bash`` (ADR-0012 §2; ADR-0011 §1,4 retained).

``SANDBOX_MODE`` selects the executor: ``none`` → :class:`~decode.tools.exec.LocalExecutor`;
``docker`` / ``modal`` → one :class:`~decode.sandbox.executor.SandboxExecutor` over the matching
backend. Lazy by construction: each backend is imported inside its own :func:`select_executor`
branch and the package resolves executor/backend names via PEP 562 :func:`__getattr__`, so a
``none``-mode process never imports any sandbox executor module or heavy backend SDK.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from decode.tools.exec import CommandExecutor

logger = logging.getLogger(__name__)

__all__ = ["DockerBackend", "ModalBackend", "SandboxExecutor", "select_executor"]


def select_executor(mode: str) -> CommandExecutor:
    """Return the :class:`~decode.tools.exec.CommandExecutor` for ``mode`` (ADR-0012 §2; ADR-0011 §1,4).

    Each backend is imported **inside** its own branch (the laziness the ``bash`` seam relies on) and
    construction is inert — nothing spins up until the executor's first ``run``. An unexpected
    ``mode`` falls back to the host :class:`~decode.tools.exec.LocalExecutor` (defensive; the
    settings ``Literal`` blocks it upstream).
    """
    if mode == "docker":
        from decode.sandbox.docker_backend import DockerBackend
        from decode.sandbox.executor import SandboxExecutor

        return SandboxExecutor(DockerBackend())
    if mode == "modal":
        from decode.sandbox.executor import SandboxExecutor
        from decode.sandbox.modal_backend import ModalBackend

        return SandboxExecutor(ModalBackend())
    from decode.tools.exec import LocalExecutor

    return LocalExecutor()


def __getattr__(name: str) -> Any:
    """Resolve ``SandboxExecutor`` / ``DockerBackend`` / ``ModalBackend`` lazily (PEP 562).

    Importing the package pulls in no executor/backend module (the ADR-0011 §4 laziness assertion).
    """
    if name == "SandboxExecutor":
        from decode.sandbox.executor import SandboxExecutor

        return SandboxExecutor
    if name == "DockerBackend":
        from decode.sandbox.docker_backend import DockerBackend

        return DockerBackend
    if name == "ModalBackend":
        from decode.sandbox.modal_backend import ModalBackend

        return ModalBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
