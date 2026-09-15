"""The host-side grade step: its env allow-list, its timeout and its reward parsing (ADR-0022 §3).

``grade_checkout`` runs a task's ``tests/test.sh`` — a script that, for most real tasks, EXECUTES
THE AGENT'S OWN CODE host-side (``python3 -m unittest`` over agent-edited modules). So the one
property this module pins hardest is that the Verifier's environment is an ALLOW-LIST, never the
parent's ``os.environ``: the benchmark process imports opik → litellm, whose ``load_dotenv()``
copies the repo ``.env`` into ``os.environ`` (see ``tests/conftest.py``), and a model-authored file
must never run with the operator's provider keys or GitHub PAT in its environment.

The rest is the reward contract §3 states and §4 depends on: a missing, empty or non-numeric
``reward.txt`` is ``None`` — a verifier ERROR that a caller must never read as a zero.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from support.benchmark_tasks import CANARY_LINE, DEFAULT_TABLES, write_task_dir

from evals.harness.task_loader import BenchmarkTask, load_benchmark_task
from evals.harness.verifier import grade_checkout, host_script_env, read_reward

# A verifier that dumps its whole environment next to its reward, so a test can read what it got.
ENV_DUMP_SCRIPT = f"""#!/usr/bin/env bash
# {CANARY_LINE}
set -uo pipefail
env > "$VERIFIER_DIR/env.txt"
printf '1\\n' > "$VERIFIER_DIR/reward.txt"
"""


def _task(
    tmp_path: Path, *, test_script: str, verifier_timeout_sec: float = 120.0
) -> BenchmarkTask:
    """A synthetic v2 task whose Verifier is ``test_script``."""
    tables = deepcopy(DEFAULT_TABLES)
    tables["task"]["name"] = "001-synthetic"
    tables["verifier"]["timeout_sec"] = verifier_timeout_sec
    task_dir = write_task_dir(
        tmp_path / "tasks" / "001-synthetic", tables=tables, test_script=test_script
    )
    return load_benchmark_task(task_dir)


def _checkout(tmp_path: Path) -> Path:
    """The tree being graded — a bare directory is all ``grade_checkout`` asks of its caller."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    return checkout


def _verifier_env(tmp_path: Path, checkout: Path) -> dict[str, str]:
    """Grade ``checkout`` with the env-dumping Verifier and return the env it actually ran with."""
    task = _task(tmp_path, test_script=ENV_DUMP_SCRIPT)
    result = grade_checkout(task, checkout)
    assert result.reward == 1.0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    dumped = (checkout / ".verifier" / "env.txt").read_text(encoding="utf-8")
    return dict(
        line.split("=", 1)
        for line in dumped.splitlines()
        if "=" in line and not line.startswith(" ")
    )


def test_a_parent_secret_never_reaches_the_verifier(tmp_path: Path, monkeypatch) -> None:
    """The Verifier runs agent-written code host-side — it gets none of the operator's env."""
    monkeypatch.setenv("SECRET_SENTINEL", "sk-sentinel-do-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-sentinel-gemini")
    monkeypatch.setenv("SANDBOX_GIT_TOKEN", "ghp-sentinel")
    monkeypatch.setenv("PYTHONPATH", "/sentinel/site-packages")

    env = _verifier_env(tmp_path, _checkout(tmp_path))

    assert "SECRET_SENTINEL" not in env
    assert "GEMINI_API_KEY" not in env
    assert "SANDBOX_GIT_TOKEN" not in env
    assert "PYTHONPATH" not in env
    assert "sk-sentinel-do-not-leak" not in "".join(env.values())


def test_the_verifier_still_gets_what_it_needs_to_run(tmp_path: Path, monkeypatch) -> None:
    """An empty env would "pass" the leak test and break every real task — pin the allow-list too."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_CTYPE", "en_US.UTF-8")
    checkout = _checkout(tmp_path)

    env = _verifier_env(tmp_path, checkout)

    assert env["PATH"] == os.environ["PATH"]
    assert env["VERIFIER_DIR"] == str(checkout / ".verifier")
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_CTYPE"] == "en_US.UTF-8"
    assert "HOME" in env


def test_host_script_env_is_the_allow_list_plus_the_callers_own_names(monkeypatch) -> None:
    """The seeder and the oracle share this helper, so it is pinned on its own."""
    monkeypatch.setenv("SECRET_SENTINEL", "sk-sentinel-do-not-leak")
    monkeypatch.setenv("TMPDIR", "/tmp/sentinel")

    env = host_script_env(VERIFIER_DIR="/somewhere/.verifier")

    assert "SECRET_SENTINEL" not in env
    assert env["TMPDIR"] == "/tmp/sentinel"
    assert env["VERIFIER_DIR"] == "/somewhere/.verifier"


def test_a_verifier_that_runs_past_its_timeout_is_a_verifier_error(tmp_path: Path) -> None:
    """No reward, ``timed_out`` — and the exception never escapes into the caller's trial."""
    script = f"#!/usr/bin/env bash\n# {CANARY_LINE}\nsleep 30\n"
    task = _task(tmp_path, test_script=script, verifier_timeout_sec=0.5)

    result = grade_checkout(task, _checkout(tmp_path))

    assert result.timed_out is True
    assert result.reward is None


def test_a_verifier_that_writes_nothing_earns_no_reward(tmp_path: Path) -> None:
    """A silent Verifier is an ERROR, never a zero (ADR-0022 §3,§4)."""
    script = f"#!/usr/bin/env bash\n# {CANARY_LINE}\nexit 0\n"
    task = _task(tmp_path, test_script=script)

    result = grade_checkout(task, _checkout(tmp_path))

    assert result.reward is None
    assert result.timed_out is False


def test_a_non_numeric_reward_earns_no_reward(tmp_path: Path) -> None:
    """``reward.txt`` holding prose is unreadable, so it grades nothing."""
    script = (
        f"#!/usr/bin/env bash\n# {CANARY_LINE}\nprintf 'PASS\\n' > \"$VERIFIER_DIR/reward.txt\"\n"
    )
    task = _task(tmp_path, test_script=script)

    assert grade_checkout(task, _checkout(tmp_path)).reward is None


def test_a_stale_reward_from_an_earlier_run_is_never_read_as_this_runs_verdict(
    tmp_path: Path,
) -> None:
    """``$VERIFIER_DIR`` is emptied, not just created — a leftover 1 would be a silent pass."""
    checkout = _checkout(tmp_path)
    stale = checkout / ".verifier"
    stale.mkdir()
    (stale / "reward.txt").write_text("1\n", encoding="utf-8")
    script = f"#!/usr/bin/env bash\n# {CANARY_LINE}\nexit 1\n"
    task = _task(tmp_path, test_script=script)

    assert grade_checkout(task, checkout).reward is None


def test_the_exit_code_is_informational_only(tmp_path: Path) -> None:
    """Only ``reward.txt`` grades: a Verifier may exit non-zero and still award 1 (ADR-0022 §3)."""
    script = (
        f"#!/usr/bin/env bash\n# {CANARY_LINE}\n"
        "printf '1\\n' > \"$VERIFIER_DIR/reward.txt\"\nexit 3\n"
    )
    task = _task(tmp_path, test_script=script)

    assert grade_checkout(task, _checkout(tmp_path)).reward == 1.0


def test_read_reward_reads_the_single_float(tmp_path: Path) -> None:
    (tmp_path / "reward.txt").write_text(" 0.5 \n", encoding="utf-8")

    assert read_reward(tmp_path) == 0.5


def test_read_reward_is_none_for_a_missing_or_empty_file(tmp_path: Path) -> None:
    assert read_reward(tmp_path) is None
    (tmp_path / "reward.txt").write_text("", encoding="utf-8")
    assert read_reward(tmp_path) is None


def test_read_reward_is_none_for_a_non_finite_reward(tmp_path: Path) -> None:
    """``nan`` / ``inf`` parse as floats but grade nothing — a verifier ERROR, not a score."""
    for raw in ("nan", "inf", "-inf", "1e400"):
        (tmp_path / "reward.txt").write_text(f"{raw}\n", encoding="utf-8")
        assert read_reward(tmp_path) is None, raw
