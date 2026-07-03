"""The sandbox executors + the mode selection seam for ``bash`` (ADR-0011 §1,4).

The home of the two sandboxed :class:`~decode.tools.exec.CommandExecutor`s that sit behind the
ADR-0002 ``run`` seam (``tools/exec.py``), selected by ``SANDBOX_MODE``: :class:`DockerExecutor`
(one session-persistent local container over the bind-mounted cwd, task 072) and
:class:`ModalExecutor` (one session-persistent remote empty-scratch ``modal.Sandbox``, task 073).
The ``none`` default keeps :class:`~decode.tools.exec.LocalExecutor` (a host subprocess).

**Lazy by construction — ``none`` imports nothing here.** :func:`select_executor` is the single
mapping ``SANDBOX_MODE`` → executor, and it **imports each concrete executor inside its own branch**
so choosing ``docker`` never imports the modal module and vice-versa. The package ``__init__`` itself
imports **neither** executor module at import time (:func:`__getattr__` resolves the
``DockerExecutor`` / ``ModalExecutor`` names lazily on first access), so ``bash.py``'s
``_get_executor()`` — which imports ``select_executor`` only on the ``docker`` / ``modal`` branch —
keeps the ``none``-mode REPL path free of every sandbox executor module. Importing this package pulls
in **no** heavy backend SDK either: :class:`ModalExecutor` imports ``modal`` only lazily on first
:meth:`~ModalExecutor.run`, and :class:`DockerExecutor` shells out to the ``docker`` CLI.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from decode.tools.exec import CommandExecutor

logger = logging.getLogger(__name__)

__all__ = ["DockerExecutor", "ModalExecutor", "select_executor"]


def select_executor(mode: str) -> CommandExecutor:
    """Return the :class:`~decode.tools.exec.CommandExecutor` for ``mode`` (ADR-0011 §1,4).

    ``none`` → :class:`~decode.tools.exec.LocalExecutor` (a host subprocess — the default);
    ``docker`` → :class:`DockerExecutor`; ``modal`` → :class:`ModalExecutor`. Each concrete sandbox
    executor is imported **inside** its own branch, so a ``none`` / ``docker`` selection never imports
    the modal module (and vice-versa) — the laziness the ``bash`` seam relies on to keep the
    ``none``-mode REPL path free of every sandbox executor module. Construction is **inert** for all
    three: no container is started, no remote sandbox is created, no ``modal`` SDK is imported — the
    backend spins up lazily on the executor's first :meth:`run`. Any unexpected ``mode`` falls back to
    the host :class:`~decode.tools.exec.LocalExecutor` (defensive; the settings ``Literal`` blocks it
    upstream).
    """
    if mode == "docker":
        from decode.sandbox.docker_executor import DockerExecutor

        return DockerExecutor()
    if mode == "modal":
        from decode.sandbox.modal_executor import ModalExecutor

        return ModalExecutor()
    from decode.tools.exec import LocalExecutor

    return LocalExecutor()


def __getattr__(name: str) -> Any:
    """Resolve ``DockerExecutor`` / ``ModalExecutor`` lazily (PEP 562) — keep ``__init__`` import-free.

    ``from decode.sandbox import DockerExecutor`` still works, but importing the *package* (e.g. via
    ``from decode.sandbox import select_executor``) imports **neither** executor module — so a
    ``none``-mode process that only ever touches ``select_executor`` never pulls the docker/modal
    executor modules into ``sys.modules`` (the ADR-0011 §4 laziness assertion).
    """
    if name == "DockerExecutor":
        from decode.sandbox.docker_executor import DockerExecutor

        return DockerExecutor
    if name == "ModalExecutor":
        from decode.sandbox.modal_executor import ModalExecutor

        return ModalExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
