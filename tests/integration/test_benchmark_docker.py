"""Real-docker end-to-end proof of the benchmark runner (ADR-0017 §3,5; task 106).

One benchmark item — the 105 ``001-greeting`` fixture task — runs the full lifecycle against a REAL
docker daemon: a fresh Workspace seeded with ``setup/``, ``setup.sh`` run in the sandbox, the agent
driven (a SCRIPTED model, so no ``GEMINI_API_KEY`` and no cost) to write ``greeting.txt`` through the
sandboxed ``bash``, then the hidden ``verify/`` injected through the seam and ``verify.sh`` run to
grade it. Proves the oracle is honest end-to-end and that ``verify.sh`` never existed in the Workspace
during the run.

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

from evals.harness.benchmark import make_benchmark_task_fn
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
    """The greeting task graded PASS: the agent writes the file in-sandbox, the hidden oracle confirms it."""
    # A scripted model (no key, no cost) that satisfies the task via the sandboxed bash tool.
    model = bash_then_finish("printf 'hello world\\n' > greeting.txt", "created the greeting file")
    monkeypatch.setattr("decode.agent.factory._build_model", lambda *args, **kwargs: model)
    task = load_benchmark_task(_GREETING_TASK)
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox="docker")

    payload = task_fn({"task_id": task.id})

    assert payload["agent_error"] is None
    assert payload["tool_calls"][0]["name"] == "bash"
    assert payload["verify"]["exit_code"] == 0, payload["verify"]
    assert "PASS" in payload["verify"]["stdout"]
    assert payload["max_steps"] == task.max_steps
