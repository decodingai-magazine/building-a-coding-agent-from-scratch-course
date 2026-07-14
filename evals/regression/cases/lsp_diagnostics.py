"""Probe 06 — a "check for type errors" ask uses the ``lsp`` tool (ADR-0007; ADR-0017 §2,6).

Code-intelligence discipline (ADR-0007): "check broken.py for type errors" should query the language
server via the ``lsp`` tool (``op=diagnostics``), not eyeball the file. A module with one deliberate
type error is seeded (``seed_type_error`` — ``add("not", "numbers")`` against ``int`` params); the run
passes when the ``lsp`` tool WAS used and the agent NAMES the seeded file in its report, i.e. it actually
surfaced the diagnostic. ``BYPASS`` gate — ``lsp`` is read-only and auto-allowed.

The full run IS exercised offline: the ``lsp`` tool drives a REAL ``ty`` language server (a dev-group
binary, no keys or network), so ``test_lsp_diagnostics_runs_green_offline`` runs this probe end-to-end
against actual diagnostics (with a ``lsp_service.shutdown_all()`` teardown to reap the subprocess),
skip-guarded only on ``ty`` being on PATH — the same pattern as
``tests/integration/test_lsp_capstone.py``.
"""

from __future__ import annotations

from pathlib import Path

from evals.harness.metrics import MaxStepsMetric, OutputContainsMetric, ToolCalledMetric
from evals.regression.fixtures import seed_type_error
from evals.regression.probe import RegressionProbe

_BROKEN = "broken.py"


def _fixture(workspace: Path) -> None:
    """Seed a module carrying one deliberate type error the language server will flag."""
    seed_type_error(workspace, filename=_BROKEN)


PROBE = RegressionProbe(
    id="06-lsp-diagnostics",
    prompt=f"Check {_BROKEN} for type errors using the language server and report what you find.",
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("lsp"),
        OutputContainsMetric(_BROKEN),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["lsp-discipline", "tool-discipline"],
)
