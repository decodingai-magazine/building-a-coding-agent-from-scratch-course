"""Fixtures local to the LSP Service tests."""

from __future__ import annotations

import pytest

from decode.services.lsp import service as lsp_service


@pytest.fixture(autouse=True)
def _clear_lsp_cache():
    """Isolate the module-level per-root cache between tests (it persists across calls by design)."""
    lsp_service._CLIENTS.clear()
    yield
    lsp_service._CLIENTS.clear()
