"""Probe 20 — an "answer ONLY as JSON" contract is honored (ADR-0017 §2,6).

Structured-output discipline: told to answer ONLY as JSON matching a named schema, the agent must emit
raw JSON of that exact shape — no prose, no ```` ```json ```` fence, no trailing commentary. A small
module is seeded so the requested summary has real content, and the prompt spells out the required JSON
schema. Two mechanical graders (no judge — this is fully code-decidable):

* Opik's ``IsJson`` built-in — the ``output`` parses as JSON at all (used directly per ADR-0017 §4);
* :class:`JsonSchemaMetric` — the parsed JSON validates against the :class:`_ReviewSummary` pydantic
  model declared here, so a well-formed-but-wrong-shape answer still fails.

Both are scored fully offline (a scripted model emits conforming raw JSON); a malformed / prose answer
grades ``0.0`` gracefully. ``BYPASS`` gate.
"""

from __future__ import annotations

from pathlib import Path

from opik.evaluation.metrics import IsJson
from pydantic import BaseModel

from evals.harness.metrics import JsonSchemaMetric, MaxStepsMetric
from evals.regression.probe import RegressionProbe

_MODULE = "inventory.py"
_MODULE_BODY = '''\
"""Inventory helpers."""


def restock(sku, quantity):
    return {"sku": sku, "quantity": quantity}
'''


class _ReviewSummary(BaseModel):
    """The required JSON shape the answer must validate against (the probe's output contract)."""

    file: str
    summary: str
    issue_count: int


# The schema description embedded in the prompt so the model knows the exact required shape.
_SCHEMA_HINT = '{"file": <string>, "summary": <string>, "issue_count": <integer>}'


def _fixture(workspace: Path) -> None:
    """Seed the small module the JSON summary describes."""
    (workspace / _MODULE).write_text(_MODULE_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="20-json-output-contract",
    prompt=(
        f"Summarize {_MODULE}. Answer ONLY with a single JSON object matching this schema, and nothing "
        f"else — no prose, no code fence:\n{_SCHEMA_HINT}"
    ),
    fixture=_fixture,
    metrics=[
        IsJson(track=False),
        JsonSchemaMetric(_ReviewSummary, name="json_matches_review_summary"),
        MaxStepsMetric(),
    ],
    max_requests=5,
    tags=["structured-output", "json-contract"],
)
