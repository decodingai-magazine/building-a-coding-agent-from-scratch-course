"""Probe 02 — finding a definition uses the ``grep`` tool, not a ``bash grep`` shell-out (ADR-0017 §2,6).

Search discipline (ADR-0002): "where is ``parse_config`` defined?" should drive the ``grep`` tool over
a small source tree, not a ``bash`` shell-out. A tiny ``src/`` package with one ``parse_config``
definition is seeded; the run passes when ``grep`` WAS used and ``bash`` was NOT. Default ``BYPASS``
gate — search is read-only.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric, ToolNotCalledMetric
from evals.regression.probe import RegressionProbe

_CONFIG_SOURCE = '''\
"""Configuration loading for the sample app."""


def parse_config(path):
    """Read and parse the config file at ``path``."""
    return {"path": path}
'''

_MAIN_SOURCE = "from app.config import parse_config\n\nCONFIG = parse_config('app.toml')\n"


def _fixture(workspace: Path) -> None:
    """Seed a small ``src/app`` package with a single ``parse_config`` definition and a caller."""
    package = workspace / "src" / "app"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text(_CONFIG_SOURCE, encoding="utf-8")
    (package / "main.py").write_text(_MAIN_SOURCE, encoding="utf-8")
    (workspace / "README.md").write_text("# sample app\n", encoding="utf-8")


PROBE = RegressionProbe(
    id="02-grep-vs-bash",
    prompt="Find where the function parse_config is defined in this project.",
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("grep"),
        ToolNotCalledMetric("bash"),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["search-discipline", "tool-discipline"],
)
