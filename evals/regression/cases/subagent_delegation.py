"""Probe 09 — a scoped investigation is delegated to the Explore subagent (ADR-0013; ADR-0017 §2,6).

Delegation discipline (ADR-0013): asked to investigate how something works across a codebase — a
read-only, self-contained question — the agent should spawn the read-only Explore subagent via the
``agent`` tool rather than crawl the tree inline. A small multi-module tree is seeded (a config loader
plus its caller) and the prompt asks how the configuration is loaded. The run passes when the ``agent``
tool WAS spawned. Runs under the default ``BYPASS`` gate — spawning a read-only child needs no
approval, and the child itself runs BYPASS with a narrowed read-only toolset (ADR-0013 §5).

The request cap is generous because the parent AND its child both draw from the same model-request
budget (the ``agent`` tool re-enters the same agent, ADR-0013 §6): a parent spawn leg + a parent
finish leg + the child's own legs.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric
from evals.regression.probe import RegressionProbe

_CONFIG = "src/app/config.py"
_CONFIG_BODY = '''\
"""Load the application configuration from the environment."""

import os


def load_config() -> dict[str, str]:
    """Read HOST and PORT from the environment, with defaults."""
    return {
        "host": os.environ.get("APP_HOST", "localhost"),
        "port": os.environ.get("APP_PORT", "8000"),
    }
'''

_MAIN = "src/app/main.py"
_MAIN_BODY = '''\
"""The application entrypoint — reads config and starts up."""

from app.config import load_config


def main() -> None:
    config = load_config()
    print(f"starting on {config['host']}:{config['port']}")
'''


def _fixture(workspace: Path) -> None:
    """Seed a small two-module tree the subagent can read to answer the config question."""
    for relative, body in ((_CONFIG, _CONFIG_BODY), (_MAIN, _MAIN_BODY)):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


PROBE = RegressionProbe(
    id="09-subagent-delegation",
    prompt=(
        "Explore this codebase and report how the application configuration is loaded. Delegate the "
        "investigation to a subagent."
    ),
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("agent"),
        MaxStepsMetric(),
    ],
    max_requests=8,
    tags=["delegation", "subagent"],
)
