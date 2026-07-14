"""Probe 10 — a task matching a skill's description dispatches that skill by name (ADR-0004; §2,6).

Skill-dispatch discipline (ADR-0004): the catalog advertises each skill's name + description cheaply,
and when a request matches one, the agent should call the ``skill`` tool with that skill's name to pull
its full body — progressive disclosure, not guessing the procedure. A project skills directory carrying
ONE aptly-described skill is seeded, and the prompt describes exactly that skill's job. The run passes
when the ``skill`` tool WAS called AND with the RIGHT ``name`` — :class:`ToolArgsMetric` inspects the
recorded ``name`` argument, so calling some other skill (or the tool with a wrong name) fails. Runs
under ``BYPASS``; ``skill`` is ungated (loading instructions grants no authority, ADR-0004 §7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.harness.metrics import MaxStepsMetric, ToolArgsMetric, ToolCalledMetric
from evals.regression.fixtures import seed_skills_dir
from evals.regression.probe import RegressionProbe

# A distinctive skill name (no built-in collides) with a description the prompt mirrors.
_SKILL_NAME = "release-notes"
_SKILL_DESCRIPTION = "Draft release notes for a version from its merged changelog entries."
_SKILL_BODY = "Summarise the changelog entries for the version into grouped release notes."


def _dispatched_the_skill(args: dict[str, Any]) -> bool:
    """Whether the ``skill`` call named the seeded skill."""
    return args.get("name") == _SKILL_NAME


def _fixture(workspace: Path) -> None:
    """Seed a project skills catalog with the one aptly-described skill the prompt matches."""
    seed_skills_dir(
        workspace,
        name=_SKILL_NAME,
        description=_SKILL_DESCRIPTION,
        body=_SKILL_BODY,
    )


PROBE = RegressionProbe(
    id="10-skill-dispatch",
    prompt=(
        "Draft the release notes for version 2.1 from the changelog. Use the skill that fits this "
        "task."
    ),
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("skill"),
        ToolArgsMetric(
            "skill",
            _dispatched_the_skill,
            description=f"the {_SKILL_NAME!r} skill by name",
            name="skill_named_release_notes",
        ),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["skill-dispatch", "progressive-disclosure"],
)
