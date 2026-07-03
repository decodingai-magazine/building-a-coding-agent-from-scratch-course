"""The sandbox executor + the mode selection seam for ``bash`` (ADR-0012 §2; ADR-0011 §1,4 retained).

The home of the sandboxed :class:`~decode.tools.exec.CommandExecutor`s that sit behind the ADR-0002
``run`` seam (``tools/exec.py``), selected by ``SANDBOX_MODE``. ADR-0012 collapses the two
ADR-0011 executors into **one** :class:`~decode.sandbox.executor.SandboxExecutor` over a thin
:class:`~decode.sandbox.executor.SandboxBackend`: ``docker`` →
``SandboxExecutor(DockerBackend())`` (one session container, fresh ``docker exec`` per call, file ops
on the bind mount). ``modal`` keeps its existing :class:`~decode.sandbox.modal_executor.ModalExecutor`
this task (rewired onto a backend in 080). The ``none`` default keeps
:class:`~decode.tools.exec.LocalExecutor` (a host subprocess).

**Lazy by construction — ``none`` imports nothing here.** :func:`select_executor` is the single
mapping ``SANDBOX_MODE`` → executor, and it **imports each concrete backend inside its own branch** so
choosing ``docker`` never imports the modal module and vice-versa. The package ``__init__`` itself
imports no executor/backend module at import time (:func:`__getattr__` resolves the names lazily on
first access), so ``bash.py``'s ``_get_executor()`` — which imports ``select_executor`` only on the
``docker`` / ``modal`` branch — keeps the ``none``-mode REPL path free of every sandbox executor
module. Importing this package pulls in **no** heavy backend SDK either: :class:`ModalExecutor`
imports ``modal`` only lazily on first ``run``, and :class:`DockerBackend` shells out to the ``docker``
CLI.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from decode.tools.exec import CommandExecutor

logger = logging.getLogger(__name__)

__all__ = ["DockerBackend", "ModalExecutor", "SandboxExecutor", "select_executor"]


def select_executor(mode: str) -> CommandExecutor:
    """Return the :class:`~decode.tools.exec.CommandExecutor` for ``mode`` (ADR-0012 §2; ADR-0011 §1,4).

    ``none`` → :class:`~decode.tools.exec.LocalExecutor` (a host subprocess — the default);
    ``docker`` → ``SandboxExecutor(DockerBackend())`` (ADR-0012); ``modal`` →
    :class:`~decode.sandbox.modal_executor.ModalExecutor` (unchanged this task — rewired in 080). Each
    concrete backend is imported **inside** its own branch, so a ``none`` / ``docker`` selection never
    imports the modal module (and vice-versa) — the laziness the ``bash`` seam relies on to keep the
    ``none``-mode REPL path free of every sandbox module. Construction is **inert** for all three: no
    container is started, no remote sandbox is created, no ``modal`` SDK is imported — the backend spins
    up lazily on the executor's first :meth:`~decode.sandbox.executor.SandboxExecutor.run`. Any
    unexpected ``mode`` falls back to the host :class:`~decode.tools.exec.LocalExecutor` (defensive; the
    settings ``Literal`` blocks it upstream).
    """
    if mode == "docker":
        from decode.sandbox.docker_backend import DockerBackend
        from decode.sandbox.executor import SandboxExecutor

        return SandboxExecutor(DockerBackend())
    if mode == "modal":
        from decode.sandbox.modal_executor import ModalExecutor

        return ModalExecutor()
    from decode.tools.exec import LocalExecutor

    return LocalExecutor()


def __getattr__(name: str) -> Any:
    """Resolve ``SandboxExecutor`` / ``DockerBackend`` / ``ModalExecutor`` lazily (PEP 562).

    ``from decode.sandbox import DockerBackend`` still works, but importing the *package* (e.g. via
    ``from decode.sandbox import select_executor``) imports **no** executor/backend module — so a
    ``none``-mode process that only ever touches ``select_executor`` never pulls the docker/modal
    modules into ``sys.modules`` (the ADR-0011 §4 laziness assertion, retained).
    """
    if name == "SandboxExecutor":
        from decode.sandbox.executor import SandboxExecutor

        return SandboxExecutor
    if name == "DockerBackend":
        from decode.sandbox.docker_backend import DockerBackend

        return DockerBackend
    if name == "ModalExecutor":
        from decode.sandbox.modal_executor import ModalExecutor

        return ModalExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
