"""Smoke test that the pinned ``kitaru`` runtime dependency resolves and imports (ADR-0009).

Task 063 downgraded pydantic-ai 2.0 → 1.x and added ``kitaru[local,pydantic-ai,llm]`` so the
durable runtime (ADR-0008, tasks 058-062) can build in-process against the same ``build_agent()``.
The runtime package itself does not exist yet, so this guards only the thing this task lands: the
dependency is installed and the durability surface decode will use (``flow`` / ``checkpoint`` /
``wait``) is importable. If a future kitaru/resolution change drops one of these names, this fails
loudly here instead of deep inside an unbuilt ``runtime/`` module.
"""

import importlib


def test_kitaru_is_importable():
    module = importlib.import_module("kitaru")

    assert module is not None


def test_kitaru_exposes_the_durability_surface():
    from kitaru import checkpoint, flow, wait

    assert callable(flow)
    assert callable(checkpoint)
    assert callable(wait)
