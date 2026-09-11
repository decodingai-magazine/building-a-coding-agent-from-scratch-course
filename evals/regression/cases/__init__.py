"""The regression case registry: one module per case, each exposing ``CASE`` (ADR-0017 §6).

:func:`evals.regression.loader.load_cases` discovers every ``*.py`` module here and reads its
module-level ``CASE`` (or ``CASES``). To add a behavior case, drop a new module beside this one —
no central list to edit (a MINED case is the same drop, named ``mined_<slug>.py``). The reference
case (``smoke_read_tool``) exercises the whole contract end to end and is the template every other
case copies.
"""

from __future__ import annotations
