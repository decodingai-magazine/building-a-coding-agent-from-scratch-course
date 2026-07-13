"""Offline tests for the per-task benchmark sandbox lifecycle (ADR-0017 §3,5; task 106).

No infra: ``decode.sandbox.select_executor`` is patched to hand back an in-memory
:class:`~support.fake_sandbox.FakeExecutor`, so ``warm_executor`` wires it into the ``decode.tools.bash``
seam exactly as a real backend would. The tests assert the lifecycle ORDER (seed → run → inject →
verify → teardown), the load-bearing invariant that ``verify.sh`` is absent while the agent runs and
present only at grade time, backend selection for the modal rung, and teardown on failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.fake_sandbox import FakeExecutor

from decode.config.settings import settings
from evals.harness.sandbox import benchmark_sandbox
from evals.harness.task_loader import load_benchmark_task


def test_lifecycle_order_is_seed_run_inject_verify_teardown(greeting_task_dir, install_fake):
    """One benchmark run walks start → setup.sh → (grade) inject verify → verify.sh → aclose in order."""
    task = load_benchmark_task(greeting_task_dir)
    fake = FakeExecutor()
    install_fake(fake)

    with benchmark_sandbox(task, sandbox="docker") as run:
        result = run.grade(task)

    kinds = [op[0] for op in fake.ops]
    assert kinds == ["start", "run", "inject", "run", "aclose"]
    # The two runs are setup.sh (before grade) then verify.sh (at grade).
    assert "setup.sh" in fake.ops[1][1]
    assert "verify.sh" in fake.ops[3][1]
    assert result.exit_code == 0


def test_verify_is_absent_during_the_run_present_only_at_grade(greeting_task_dir, install_fake):
    """The hidden oracle never exists in the Workspace until grade time (the core ADR-0017 §5 invariant)."""
    task = load_benchmark_task(greeting_task_dir)
    fake = FakeExecutor()
    install_fake(fake)

    with benchmark_sandbox(task, sandbox="docker") as run:
        # An agent-style command mid-run must not see verify.sh.
        run.run("ls -a")
        run.grade(task)

    runs = [op for op in fake.ops if op[0] == "run"]
    # setup.sh and the agent ``ls`` ran with verify absent; only the verify.sh run saw it present.
    assert [command_saw_verify for _, _, command_saw_verify in runs] == [False, False, True]


def test_setup_script_is_skipped_when_absent(valid_task_dir, install_fake):
    """A task shipping no ``setup/setup.sh`` produces no setup run — only the grade-time verify run."""
    (valid_task_dir / "setup" / "setup.sh").unlink()
    task = load_benchmark_task(valid_task_dir)
    fake = FakeExecutor()
    install_fake(fake)

    with benchmark_sandbox(task, sandbox="docker") as run:
        run.grade(task)

    run_commands = [op[1] for op in fake.ops if op[0] == "run"]
    assert run_commands == ["bash verify.sh"]


def test_workspace_is_a_fresh_temp_dir_seeded_with_setup(greeting_task_dir, install_fake):
    """The yielded Workspace is a fresh temp dir carrying the copied ``setup/`` files, gone after exit."""
    task = load_benchmark_task(greeting_task_dir)
    install_fake(FakeExecutor())

    with benchmark_sandbox(task, sandbox="docker") as run:
        workspace = run.workspace
        assert workspace.is_dir()
        assert (workspace / "README.md").is_file()  # a committed setup/ file, seeded in
        assert not (workspace / "verify.sh").exists()  # the oracle is never seeded

    assert not workspace.exists()  # teardown removed the temp Workspace


def test_modal_rung_selects_the_modal_backend(greeting_task_dir, install_fake):
    """``--sandbox modal`` drives ``select_executor('modal')`` — the modal backend construction path."""
    task = load_benchmark_task(greeting_task_dir)
    captured = install_fake(FakeExecutor())

    with benchmark_sandbox(task, sandbox="modal") as run:
        run.grade(task)

    assert captured["mode"] == "modal"


def test_select_executor_modal_really_builds_a_modal_backend():
    """The real seam yields a ``SandboxExecutor`` over a ``ModalBackend`` — inert, no creds needed."""
    from decode.sandbox import select_executor
    from decode.sandbox.executor import SandboxExecutor
    from decode.sandbox.modal_backend import ModalBackend

    executor = select_executor("modal")

    assert isinstance(executor, SandboxExecutor)
    assert isinstance(executor._backend, ModalBackend)


def test_teardown_and_mode_restore_run_on_failure(greeting_task_dir, install_fake):
    """A raise inside the ``with`` body still reaps the executor and restores ``SANDBOX_MODE``."""
    task = load_benchmark_task(greeting_task_dir)
    fake = FakeExecutor()
    install_fake(fake)
    previous_mode = settings.sandbox_mode
    captured_workspace: dict[str, Path] = {}

    with (
        pytest.raises(RuntimeError, match="boom"),
        benchmark_sandbox(task, sandbox="docker") as run,
    ):
        captured_workspace["path"] = run.workspace
        raise RuntimeError("boom")

    assert fake.closed  # aclose ran despite the failure
    assert settings.sandbox_mode == previous_mode
    assert not captured_workspace["path"].exists()  # temp Workspace cleaned up
