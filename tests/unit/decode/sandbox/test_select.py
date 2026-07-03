"""Unit tests for the executor selection seam (``decode.sandbox.select_executor``, ADR-0011 §1,4).

``select_executor(mode)`` is the single mapping ``SANDBOX_MODE`` → :class:`CommandExecutor` that
``bash.py``'s ``_get_executor()`` calls on the ``docker`` / ``modal`` branch. These prove the mapping
returns the right type per mode and — critically — that construction is **inert**: no container is
started and no remote sandbox is created (the backend spins up lazily on the executor's first
``run()``), so the seam is safe to build with no Docker daemon and no Modal credentials.
"""

from __future__ import annotations

from decode.sandbox import select_executor
from decode.tools.exec import LocalExecutor


def test_select_none_returns_the_host_local_executor():
    assert isinstance(select_executor("none"), LocalExecutor)


def test_select_unknown_mode_falls_back_to_the_host_local_executor():
    # Defensive: the settings Literal blocks this upstream, but a stray value must not crash.
    assert isinstance(select_executor("bogus"), LocalExecutor)


def test_select_docker_returns_an_inert_sandbox_executor_over_a_docker_backend():
    from decode.sandbox.docker_backend import DockerBackend
    from decode.sandbox.executor import SandboxExecutor

    executor = select_executor("docker")

    assert isinstance(executor, SandboxExecutor)
    assert isinstance(executor._backend, DockerBackend)
    # Inert construction: the keeper container spins up lazily on the first run() (ADR-0012 §2).
    assert executor._created is False
    assert executor._backend._container_id is None


def test_select_modal_returns_an_inert_modal_executor():
    from decode.sandbox.modal_executor import ModalExecutor

    executor = select_executor("modal")

    assert isinstance(executor, ModalExecutor)
    # Inert construction: the remote sandbox is created (and modal imported) lazily on first run() (§3).
    assert executor._sandbox is None
