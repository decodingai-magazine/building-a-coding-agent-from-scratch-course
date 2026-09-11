"""The behavior regression track: cases that assert the agent works the RIGHT way (ADR-0022 §8).

A benchmark asks "did it get the task done?"; a Regression Case asks "did it work the way we
designed?" — right tool, gate respected, minimal steps, compaction survived (ADR-0002..0013). Each
case (:class:`~evals.regression.case.RegressionCase`) declares a fixture, a prompt, a gate policy,
its Difficulty Tier, the symptom it catches, the natural-language assertion its answer must clear,
and the metrics that grade its behavior; :func:`~evals.regression.loader.load_cases` discovers the
case modules under :mod:`evals.regression.cases`. The Opik wiring that runs a case host-native and
scores it lives in :mod:`evals.harness.regression`.
"""

from __future__ import annotations
