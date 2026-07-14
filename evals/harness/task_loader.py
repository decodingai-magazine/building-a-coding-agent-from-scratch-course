"""The benchmark task-folder contract + loader (ADR-0017 §2,5; task 105).

A benchmark task is a folder ``evals/benchmark/tasks/<NNN>-<slug>/`` with four parts:

* ``task.yaml`` — the agent-facing spec (:class:`BenchmarkTask`): ``id``, ``prompt`` (what the agent
  sees; it never names the verify assets), ``max_steps``, ``difficulty``, ``tags`` and optional
  G-Eval ``judges``.
* ``setup/`` — files copied verbatim into a fresh Workspace before the run, plus an optional
  ``setup/setup.sh`` executed after seeding (git history, sqlite DBs, mixed encodings — state that
  cannot live as committed files).
* ``verify/`` — the hidden oracle: ``verify.sh`` (exit 0 / prints ``PASS`` = success) + optional
  hidden test files, injected only at grade time.
* ``solution/`` — a committed gold overlay, used ONLY by the oracle-sanity harness
  (:mod:`evals.harness.oracle_sanity`), never in an agent run.

:func:`load_benchmark_task` validates one folder and :func:`load_benchmark_tasks` scans a root.
Both fail loudly with a :class:`BenchmarkTaskError` naming the offending folder — a missing
``verify.sh``, an empty ``prompt`` or a bad ``difficulty`` never loads silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# The real benchmark tasks live here (``evals/benchmark/tasks/``); the 20 authored tasks land in
# tasks 108-110. ``parents[1]`` climbs ``evals/harness/task_loader.py`` -> ``evals/``.
BENCHMARK_TASKS_DIR = Path(__file__).resolve().parents[1] / "benchmark" / "tasks"

# The three difficulty tiers, declared once so the loader and any reader share one source.
Difficulty = Literal["easy", "medium", "hard"]

# The verify script the oracle contract requires in every task's ``verify/`` folder.
VERIFY_SCRIPT_NAME = "verify.sh"


class BenchmarkTaskError(Exception):
    """A benchmark task folder violates the contract (bad yaml, missing verify.sh, …).

    Always carries the offending ``task_dir`` in its message so a broken authored task is trivial
    to locate.
    """


class JudgeSpec(BaseModel):
    """One optional G-Eval judge add-on declared in ``task.yaml`` (ADR-0017 §7).

    ``task_introduction`` frames what the judge grades and ``evaluation_criteria`` is the rubric —
    the exact pair :func:`evals.harness.judges.make_judge` consumes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    task_introduction: str = Field(min_length=1)
    evaluation_criteria: str = Field(min_length=1)


class BenchmarkTask(BaseModel):
    """The parsed, validated ``task.yaml`` of one benchmark task (ADR-0017 §2).

    ``task_dir`` is injected by the loader (not present in the yaml) so callers — the oracle-sanity
    harness and the runner — can reach the folder's ``setup/`` / ``verify/`` / ``solution/`` assets.
    ``extra="forbid"`` turns a typo'd yaml key into a loud failure rather than a silently ignored
    field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    max_steps: int = Field(gt=0)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list)
    judges: list[JudgeSpec] = Field(default_factory=list)
    task_dir: Path

    @field_validator("prompt", "id")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """A whitespace-only ``prompt`` / ``id`` is as unusable as an empty one — reject both."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @property
    def verify_script(self) -> Path:
        """The oracle's entrypoint: ``<task_dir>/verify/verify.sh``."""
        return self.task_dir / "verify" / VERIFY_SCRIPT_NAME

    @property
    def setup_dir(self) -> Path:
        """The seed folder copied into the Workspace: ``<task_dir>/setup/``."""
        return self.task_dir / "setup"

    @property
    def solution_dir(self) -> Path:
        """The gold overlay used only by the oracle-sanity harness: ``<task_dir>/solution/``."""
        return self.task_dir / "solution"


def load_benchmark_task(task_dir: Path) -> BenchmarkTask:
    """Load and validate one task folder into a :class:`BenchmarkTask` (ADR-0017 §2,5).

    Reads ``<task_dir>/task.yaml``, requires ``<task_dir>/verify/verify.sh`` to exist, and lets the
    pydantic model enforce the field contract (non-empty ``prompt``/``id``, positive ``max_steps``,
    a valid ``difficulty``, no unknown keys). Any violation raises :class:`BenchmarkTaskError`
    naming ``task_dir``.
    """
    yaml_path = task_dir / "task.yaml"
    if not yaml_path.is_file():
        raise BenchmarkTaskError(f"{task_dir}: missing task.yaml")

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BenchmarkTaskError(f"{task_dir}: task.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise BenchmarkTaskError(
            f"{task_dir}: task.yaml must be a mapping, got {type(raw).__name__}"
        )

    verify_script = task_dir / "verify" / VERIFY_SCRIPT_NAME
    if not verify_script.is_file():
        raise BenchmarkTaskError(f"{task_dir}: missing hidden oracle verify/{VERIFY_SCRIPT_NAME}")

    try:
        return BenchmarkTask(task_dir=task_dir, **raw)
    except ValidationError as exc:
        raise BenchmarkTaskError(f"{task_dir}: task.yaml violates the contract: {exc}") from exc
    except TypeError as exc:
        # Non-string keys in the mapping (e.g. a yaml list where a dict was expected) reach here.
        raise BenchmarkTaskError(f"{task_dir}: task.yaml is malformed: {exc}") from exc


def load_benchmark_tasks(root: Path = BENCHMARK_TASKS_DIR) -> list[BenchmarkTask]:
    """Scan ``root`` for task folders and load each, sorted by folder name (ADR-0017 §2).

    A "task folder" is any immediate subdirectory of ``root`` that contains a ``task.yaml``; other
    entries (a ``README.md``, stray files) are ignored so the contract docs can live beside the
    tasks. A missing ``root`` yields an empty list — the real benchmark set lands in tasks 108-110.
    Every folder that does declare a ``task.yaml`` must pass the contract or the whole scan fails
    loudly (via :func:`load_benchmark_task`).
    """
    if not root.is_dir():
        return []
    task_dirs = sorted(
        child for child in root.iterdir() if child.is_dir() and (child / "task.yaml").is_file()
    )
    return [load_benchmark_task(task_dir) for task_dir in task_dirs]
