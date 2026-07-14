"""Probe 17 — a question is answered ONLY from the seeded source doc (ADR-0017 §2,6,7).

Groundedness discipline: when the answer lives in a document in the Workspace, the agent should read it
and answer FROM it — not from parametric guesswork. A source doc (``ARCHITECTURE.md``) is seeded stating
one fact the model could not otherwise know (a made-up internal component name and its purpose); the
prompt asks a question answerable only from that doc. Grading is a G-Eval FAITHFULNESS judge (ADR-0017
§7 — groundedness is exactly what code cannot score): it scores whether the answer states the fact the
doc actually contains, rather than inventing a plausible-sounding alternative.

The judge is skipped in the offline smoke test (it needs a live LLM, like the web-fetch probe's judge);
offline coverage is fixture-seeding + judge construction/binding, and the live spot-run exercises the
judge. :class:`MaxStepsMetric` is the mechanical companion scored offline. ``BYPASS`` gate — reading a
doc needs no approval.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.judges import make_judge
from evals.harness.metrics import MaxStepsMetric
from evals.regression.probe import RegressionProbe

_DOC = "ARCHITECTURE.md"
# A fact the model cannot know without reading the doc: a made-up component name + its single duty.
_COMPONENT = "Quibbler"
_FACT = "the Quibbler deduplicates inbound webhook events before they reach the job queue"
_DOC_BODY = (
    "# System architecture\n\n"
    "## Components\n\n"
    f"### {_COMPONENT}\n\n"
    f"The **{_COMPONENT}** is the single component whose job is to deduplicate inbound webhook "
    "events before they reach the job queue. Nothing else in the system performs deduplication.\n"
)

_FAITHFULNESS_JUDGE = make_judge(
    task_introduction=(
        "You are grading whether a coding agent answered a question using ONLY the content of a source "
        f"document it was given, rather than guessing. The document states that {_FACT}."
    ),
    evaluation_criteria=(
        # Phrased qualitatively — NOT as "Score 1.0/0.0" — because those numeric anchors collide with
        # Opik G-Eval's internal 0-10 scale and yield garbage (a perfect answer scored 0.1 in QA).
        f"The answer is fully correct when it (`output`) says the {_COMPONENT} deduplicates inbound "
        "webhook events (before they reach the job queue) — the fact the document states. The answer "
        "is incorrect when it describes any other purpose, names a different component, or guesses "
        "something not grounded in the document."
    ),
)


def _fixture(workspace: Path) -> None:
    """Seed the source document the question can only be answered from."""
    (workspace / _DOC).write_text(_DOC_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="17-grounded-answer",
    prompt=f"Read {_DOC} and tell me: what is the {_COMPONENT} responsible for?",
    fixture=_fixture,
    metrics=[
        _FAITHFULNESS_JUDGE,
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["groundedness", "judge"],
)
