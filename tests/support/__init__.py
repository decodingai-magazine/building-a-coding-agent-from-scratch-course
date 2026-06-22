"""Shared test helpers (not shipped in the ``decode`` package).

Importable as ``support.<module>`` from any test because ``tests/support`` is on
``pythonpath`` (see ``[tool.pytest.ini_options]`` in ``pyproject.toml``). Modules here are
scaffolding the production package must not carry — e.g. the gated ``noop`` tool that the
permission / loop / e2e tests drive in isolation but that no real agent ever registers.
"""
