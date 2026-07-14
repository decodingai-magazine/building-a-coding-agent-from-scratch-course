"""decode eval suite — demos, benchmark, regression probes, Opik harness (ADR-0017).

Top-level package on purpose: eval code is course material *about* the agent, not part of it, so
it lives beside ``src/`` and is never shipped in the wheel (``[tool.hatch.build.targets.wheel]``
packages only ``src/decode``). Unit tests for the harness mirror this tree under
``tests/unit/evals/`` — one pytest root, so ``make ci`` covers the harness's offline logic.
"""

from __future__ import annotations
