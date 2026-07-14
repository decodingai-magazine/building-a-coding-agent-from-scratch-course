"""Extra oracle repros beyond the two-direction sanity sweep (task 108, QA round 1).

The standard oracle-sanity harness proves each oracle PASSes on ``solution/`` and FAILs on untouched
``setup/``. Two graders had blind spots the Tester found: 002 rejected a valid no-trailing-newline
answer, and 006 accepted a script that printed duplicate IPs. These cases pin those fixes: 002 must
PASS a content-correct answer regardless of a final newline; 006 must FAIL a duplicate-IP script even
though its set of IPs is right.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    VERIFY_SCRIPT_NAME,
    load_benchmark_task,
)


def _grade_workspace(
    task_slug: str, tmp_path: Path, files: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Seed a task's ``setup/``, overlay ``files``, inject ``verify/``, and run ``verify.sh``.

    Mirrors the grade-time Workspace the runner/oracle-sanity build, but lets a test drop in an
    arbitrary (possibly wrong) answer instead of the gold ``solution/`` overlay.
    """
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_slug)
    shutil.copytree(task.setup_dir, tmp_path, dirs_exist_ok=True)
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    shutil.copytree(task.verify_script.parent, tmp_path, dirs_exist_ok=True)
    return subprocess.run(
        ["bash", VERIFY_SCRIPT_NAME],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_002_passes_without_a_trailing_newline(tmp_path: Path) -> None:
    # A valid answer built as "\n".join(sorted(emails)) — content-correct, no final newline.
    emails = "alice@example.com\nbilling@corp.net\nbob@work.io\ncarol@example.com\ndave@example.com"

    result = _grade_workspace("002-regex-extraction", tmp_path, {"emails.txt": emails})

    assert result.returncode == 0, f"expected PASS, got exit {result.returncode}\n{result.stdout}"
    assert "PASS" in result.stdout


def test_006_fails_on_duplicate_ips(tmp_path: Path) -> None:
    # A script whose SET of IPs is correct but that lists 10.0.0.1 twice — the prompt requires each once.
    duplicate_script = 'import json\nprint(json.dumps(["10.0.0.1", "10.0.0.1", "10.0.0.3"]))\n'

    result = _grade_workspace("006-log-forensics", tmp_path, {"ban_ips.py": duplicate_script})

    assert result.returncode != 0, f"expected FAIL on duplicate IPs, got exit 0\n{result.stdout}"
    assert "duplicate" in result.stdout.lower()
