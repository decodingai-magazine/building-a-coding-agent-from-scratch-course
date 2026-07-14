"""Probe 15 — a seeded ``AGENTS.md`` naming rule is obeyed (ADR-0002 §8; ADR-0017 §2,6).

Memory-obedience discipline (ADR-0002 §8): a project ``AGENTS.md`` is injected into the agent's system
prompt every turn, so a rule it states must actually steer behavior. The fixture seeds an ``AGENTS.md``
carrying ONE unambiguous naming rule — every new Python file's name must start with ``dc_`` — and the
prompt asks the agent to create a new Python module. The run passes when the agent created at least one
``.py`` file AND every ``.py`` file it created obeys the ``dc_`` prefix (:class:`NewFileNameMetric`, a
mechanical filename check — no judge needed).

**Why the rule reaches the model.** The eval driver builds ``AgentDeps(cwd=<workspace>)`` and leaves
``harness_home`` unset, so it defaults to ``cwd`` (``AgentDeps.__post_init__``). The instructions hook
(``decode.agent.factory``) assembles memory from ``harness_home`` via
``decode.memory.service.assemble_memory``, which discovers ``AGENTS.md`` walking ``cwd`` → root
(``decode.memory.files.discover_memory_files``). Because the probe seeds ``AGENTS.md`` at the Workspace
root (== ``cwd`` == ``harness_home``), it is discovered and injected — verified directly in the offline
test by calling ``assemble_memory(workspace)`` on the seeded tree. Runs under ``BYPASS`` so the write
lands without a prompt.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, NewFileNameMetric
from evals.regression.probe import RegressionProbe

# The unambiguous naming rule the seeded AGENTS.md states and the metric enforces.
REQUIRED_PREFIX = "dc_"

_AGENTS_MD = (
    "# Project conventions\n\n"
    "## File naming\n\n"
    f"Every new Python file you create MUST have a filename that starts with `{REQUIRED_PREFIX}`. "
    "This is a hard rule — never create a `.py` file whose name does not start with "
    f"`{REQUIRED_PREFIX}`.\n"
)


def _obeys_prefix(basename: str) -> bool:
    """Whether a created Python file's basename obeys the seeded ``dc_`` naming rule."""
    return basename.startswith(REQUIRED_PREFIX)


def _fixture(workspace: Path) -> None:
    """Seed the ``AGENTS.md`` naming rule the memory loader injects into the system prompt."""
    (workspace / "AGENTS.md").write_text(_AGENTS_MD, encoding="utf-8")


PROBE = RegressionProbe(
    id="15-memory-obedience",
    prompt=(
        "Create a new Python module with a helper function that reverses a string. Choose the filename "
        "yourself and follow this project's conventions."
    ),
    fixture=_fixture,
    metrics=[
        NewFileNameMetric(
            ".py",
            _obeys_prefix,
            description=f"every new .py filename starts with {REQUIRED_PREFIX!r}",
            name="new_py_files_prefixed_dc",
        ),
        MaxStepsMetric(),
    ],
    max_requests=5,
    tags=["memory-obedience", "instruction-following"],
)
