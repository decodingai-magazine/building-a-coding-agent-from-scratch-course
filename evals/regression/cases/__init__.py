"""The regression probe registry: one module per probe, each exposing ``PROBE`` (ADR-0017 §6).

:func:`evals.regression.loader.load_probes` discovers every ``*.py`` module here and reads its
module-level ``PROBE`` (or ``PROBES``). To add a behavior probe, drop a new module beside this one —
no central list to edit. The full behavior suite lands in tasks 112-114; this package ships one
reference probe (``smoke_read_tool``) that exercises the whole contract end to end and serves as the
template the real probes copy.
"""

from __future__ import annotations
