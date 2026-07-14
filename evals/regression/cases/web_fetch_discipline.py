"""Probe 05 — a URL question drives ``web_fetch`` and a grounded answer (ADR-0017 §2,6,7).

Web-fetch discipline (ADR-0002): when the prompt cites a URL, the agent should ``web_fetch`` it and
answer from the fetched content, not guess. A stdlib ``http.server`` fixture serves ONE known page on a
FIXED localhost port so the static prompt can cite the exact URL — no real network is ever touched
(ADR-0017 §6; the web probe's AC). Two graders:

* :class:`ToolCalledMetric` — ``web_fetch`` WAS called;
* a G-Eval grounded-answer judge — the answer states the fact the served page actually contains.

The server is a :class:`~evals.regression.probe.RegressionProbe.context` entered around the run, so it is
alive for the fetch and torn down the moment the run ends. ``BYPASS`` gate — fetching needs no approval.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from evals.harness.judges import make_judge
from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric
from evals.regression.fixtures import serve_page
from evals.regression.probe import RegressionProbe

# A fixed high port: the prompt must cite the URL verbatim, and a static prompt cannot know an
# OS-assigned ephemeral port. The server only listens for the duration of a single probe run.
_PORT = 8477
_URL = f"http://127.0.0.1:{_PORT}/"
_RATE_LIMIT = "240 requests per minute"
_PAGE_BODY = (
    "<html><body><h1>Widget API</h1>"
    f"<p>The Widget API rate limit is {_RATE_LIMIT}.</p>"
    "</body></html>"
)

_GROUNDED_ANSWER_JUDGE = make_judge(
    task_introduction=(
        "You are grading whether a coding agent answered a question using content it fetched from a "
        "web page, rather than guessing. The agent was asked for the Widget API rate limit, which the "
        "fetched page states."
    ),
    evaluation_criteria=(
        f"Score 1.0 when the answer (`output`) states the rate limit as {_RATE_LIMIT!r} — the value "
        "the served page contains. Score 0.0 for any other number or a refusal/guess not grounded in "
        "the fetched page."
    ),
)


def _fixture(_workspace: Path) -> None:
    """No files on disk — the page lives on the local HTTP server, not the Workspace."""


def _context(_workspace: Path) -> AbstractContextManager[Any]:
    """Serve the known page on the fixed localhost port for the duration of the run."""
    return serve_page(_PAGE_BODY, port=_PORT)


PROBE = RegressionProbe(
    id="05-web-fetch-discipline",
    prompt=f"Fetch {_URL} and tell me the Widget API rate limit.",
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("web_fetch"),
        _GROUNDED_ANSWER_JUDGE,
        MaxStepsMetric(),
    ],
    context=_context,
    max_requests=6,
    tags=["web-fetch-discipline", "judge"],
)
