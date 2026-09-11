"""Real-docker end-to-end proof of the benchmark sandbox lifecycle (ADR-0017 §3,5; task 106).

One task — the ``001-greeting`` fixture — runs the full lifecycle against a REAL docker daemon: a
fresh Workspace seeded with ``environment/``, ``setup.sh`` run in the sandbox, the agent driven (a
SCRIPTED model, so no ``GEMINI_API_KEY`` and no cost) to write ``greeting.txt`` through the sandboxed
``bash``, then the hidden ``tests/`` injected through the seam and ``tests/test.sh`` run to grade it.
Proves the Verifier is honest end-to-end and that it never existed in the Workspace during the run.

Superseded by the host-side Verifier of ADR-0022 §3: task 160 replaces this in-sandbox grading path
(and this file) with the trial runner.

**Skipped, never failed, without a daemon.** A module-level ``docker info`` probe guards the file with
``@pytest.mark.skipif`` (mirroring ``test_docker_executor.py``), so ``make ci`` stays green on a
machine with no Docker. The run reaps its container via the sandbox ``finally`` (``close_executor``),
so the suite leaves no docker litter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.eval_models import bash_then_finish

from decode.permissions.types import PermissionMode
from evals.harness.driver import run_agent_once_sync
from evals.harness.sandbox import benchmark_sandbox
from evals.harness.task_loader import load_benchmark_task


def _docker_available() -> bool:
    """True if a local docker daemon answers a fast ``docker info`` probe (else the file SKIPs)."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_DOCKER_AVAILABLE = _docker_available()

pytestmark = pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")

# The committed 105 fixture task: tests/integration/ -> tests/ -> unit/evals/fixtures/tasks/001-greeting.
_GREETING_TASK = (
    Path(__file__).resolve().parents[1] / "unit" / "evals" / "fixtures" / "tasks" / "001-greeting"
)


@pytest.fixture(autouse=True)
def _reset_seam():
    """Leave the process-global ``bash`` executor seam clean after the test."""
    yield
    from decode.tools.bash import reset_executor

    reset_executor()


def test_one_benchmark_item_runs_end_to_end_through_real_docker(monkeypatch):
    """The greeting task earns reward 1: the agent writes the file in-sandbox, the Verifier confirms it."""
    # A scripted model (no key, no cost) that satisfies the task via the sandboxed bash tool.
    model = bash_then_finish("printf 'hello world\\n' > greeting.txt", "created the greeting file")
    monkeypatch.setattr("decode.agent.factory._build_model", lambda *args, **kwargs: model)
    task = load_benchmark_task(_GREETING_TASK)

    with benchmark_sandbox(task, sandbox="docker") as run:
        seeded = run.run("cat seeded.txt; ls tests 2>&1 || true")
        record = run_agent_once_sync(
            task.instruction,
            cwd=run.workspace,
            gate_mode=PermissionMode.BYPASS,
            max_requests=task.max_steps,
        )
        graded = run.grade(task)
        reward = run.run("cat .verifier/reward.txt")

    assert "seeded" in seeded.stdout  # environment/setup.sh ran inside the sandbox
    assert "test.sh" not in seeded.stdout  # the Verifier was absent while the agent worked
    assert record.agent_error is None
    assert [call.name for call in record.tool_calls] == ["bash"]
    assert graded.exit_code == 0, graded.stderr
    assert reward.stdout.strip() == "1", reward.stdout
