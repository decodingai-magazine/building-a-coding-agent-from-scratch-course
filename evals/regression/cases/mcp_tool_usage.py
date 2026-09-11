"""Case 12 — an MCP-server tool is used when the task needs it (SKIP-GUARDED; ADR-0017 §10; task 113).

MCP-tool discipline: once decode can connect to MCP servers, a task that a connected server's tool
solves should drive THAT tool rather than a hand-rolled shell-out. This case is DECLARED so it lands
in the registry and activates the instant its blocker clears — but it is **skip-guarded**: decode has
no MCP tool factory yet (no ``tools/mcp*.py``, and ``fastmcp`` is not a dependency — ``AGENTS.md`` lists
it as a future step, and ADR-0017 §10 explicitly defers this case). ``skip_reason`` keeps it out of
live runs (:func:`evals.harness.regression.run_regression` logs and excludes it) without hiding it.

The fixture is a documented stub (:func:`~evals.regression.fixtures.mcp.seed_mcp_note`) and the
metric names the MCP tool the future server exposes. When MCP lands: wire the real ``fastmcp`` stdio
server (reference shape in ``evals/regression/fixtures/mcp.py``) as the case's ``context`` and drop
``skip_reason``.
"""

from __future__ import annotations

from evals.harness.metrics import MaxStepsMetric, ToolCalledMetric
from evals.regression.case import RegressionCase
from evals.regression.fixtures import seed_mcp_note

# The reason surfaced when the suite skips this case — the exact phrase task 113 asks for.
SKIP_REASON = "decode MCP tools not yet shipped"

# The tool name the future fixture MCP server exposes (see the fixture module's reference shape).
_MCP_TOOL = "echo"


CASE = RegressionCase(
    id="12-mcp-tool-usage",
    prompt="Use the echo MCP tool to echo back the text 'ping'.",
    fixture=seed_mcp_note,
    difficulty="medium",
    symptom=(
        "harness invariant: a task an MCP server's tool solves drives that tool, not a hand-rolled "
        "shell-out."
    ),
    assertion="The response echoes back exactly the text the user asked to have echoed.",
    metrics=[
        ToolCalledMetric(_MCP_TOOL),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["mcp", "skipped"],
    skip_reason=SKIP_REASON,
)
