"""Smoke test that the pinned ``kitaru`` runtime dependency resolves and imports (ADR-0019).

Kitaru 0.22.2 removed the durable-execution surface decode was built on (``flow`` / ``checkpoint``
/ ``wait`` / ``save`` / ``ImageSettings``), which is why the Durable Flow is deleted rather than
ported. This guards the two facts that outlive it: the dependency still installs and imports, and
the retired surface stays retired — a re-appearing ``flow`` would mean the pin moved backwards, not
that the durable runtime is welcome back.
"""

import importlib


def test_kitaru_is_importable():
    module = importlib.import_module("kitaru")

    assert module is not None


def test_the_durable_execution_surface_is_gone_upstream():
    """The ImportError that made ``runtime/flow.py`` dead code, pinned as a fact (ADR-0019)."""
    module = importlib.import_module("kitaru")

    for retired in ("flow", "checkpoint", "wait", "save", "ImageSettings"):
        assert not hasattr(module, retired), (
            f"kitaru re-exposes {retired!r}: the durable runtime was deleted on the premise that "
            "upstream removed it (ADR-0019) — re-read the ADR before building on this again."
        )
