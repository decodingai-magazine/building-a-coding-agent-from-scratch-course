"""The behavior regression track: probes that assert the agent works the RIGHT way (ADR-0017 §2,6).

A benchmark asks "did it get the task done?"; a regression probe asks "did it work the way we
designed?" — right tool, gate respected, minimal steps, compaction survived (ADR-0002..0013). Each
probe (:class:`~evals.regression.probe.RegressionProbe`) declares a fixture, a prompt, a gate policy
and the metrics that grade its behavior; :func:`~evals.regression.loader.load_probes` discovers the
probe modules under :mod:`evals.regression.cases`. The Opik wiring that runs a probe host-native and
scores it lives in :mod:`evals.harness.regression`.
"""

from __future__ import annotations
