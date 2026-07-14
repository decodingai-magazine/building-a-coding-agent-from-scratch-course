"""Probe 19 — a required report template is followed (ADR-0017 §2,6,7).

Template-compliance discipline: when the prompt embeds a required output template, the agent must
follow its structure — every named section, in order — not answer in free-form prose. A small source
file is seeded so the "review" has real content to report on, and the prompt embeds a report template
with three required section headers. Two grading surfaces (the ADR-0017 §7 split): a mechanical
:class:`OutputContainsMetric` per required header confirms each heading is present verbatim, and a
G-Eval ADHERENCE judge confirms the report actually FILLS the template (each section carries relevant
content, in the template's order) rather than just echoing the headers.

The mechanical header checks are scored offline (a scripted model emits a report carrying every header);
the adherence judge is skipped offline and exercised by the live spot-run. ``BYPASS`` gate.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.judges import make_judge
from evals.harness.metrics import MaxStepsMetric, OutputContainsMetric
from evals.regression.probe import RegressionProbe

_MODULE = "payments.py"
_MODULE_BODY = '''\
"""Payment helpers."""


def charge(amount, currency="usd"):
    # NOTE: no validation that amount is positive
    return {"amount": amount, "currency": currency, "status": "charged"}
'''

# The section headers the embedded template requires — each must appear verbatim in the report.
# Paired with a clean metric name (the header text carries ``#`` / spaces the metric name should not).
REQUIRED_HEADERS: tuple[tuple[str, str], ...] = (
    ("## Summary", "output_has_summary_header"),
    ("## Findings", "output_has_findings_header"),
    ("## Recommendations", "output_has_recommendations_header"),
)

_TEMPLATE = "\n".join(f"{header}\n<your content here>\n" for header, _ in REQUIRED_HEADERS)

_ADHERENCE_JUDGE = make_judge(
    task_introduction=(
        "You are grading whether a coding agent followed a REQUIRED report template. The user asked for "
        "a code review returned under three sections, in this order: Summary, Findings, "
        "Recommendations."
    ),
    evaluation_criteria=(
        # Phrased qualitatively — NOT as "Score 1.0/0.0" — because those numeric anchors collide with
        # Opik G-Eval's internal 0-10 scale and yield garbage (a perfect answer scored 0.1 in QA).
        "The report fully adheres when the answer (`output`) uses all three section headings (## "
        "Summary, ## Findings, ## Recommendations) in that order AND each section is filled with "
        "relevant content for the reviewed code (not left empty or a placeholder). It fails to adhere "
        "when a section is missing, out of order, or left unfilled, or when the answer ignores the "
        "template and replies in free-form prose."
    ),
)


def _fixture(workspace: Path) -> None:
    """Seed the small module the report reviews."""
    (workspace / _MODULE).write_text(_MODULE_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="19-template-compliance",
    prompt=(
        f"Review {_MODULE} and report your findings using EXACTLY this template, keeping every section "
        f"heading verbatim and in order:\n\n{_TEMPLATE}"
    ),
    fixture=_fixture,
    metrics=[
        *(OutputContainsMetric(header, name=name) for header, name in REQUIRED_HEADERS),
        _ADHERENCE_JUDGE,
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["template-compliance", "instruction-following", "judge"],
)
