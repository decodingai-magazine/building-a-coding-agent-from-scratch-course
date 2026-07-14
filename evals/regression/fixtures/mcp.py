"""A DOCUMENTED STUB for the (skip-guarded) MCP-tool-usage probe (ADR-0017 §10; task 113).

decode has no MCP tool factory yet — ``AGENTS.md`` lists ``fastmcp`` as a future step and it is
deliberately NOT a project dependency, so we do NOT add it just to seed a skipped probe. This module
therefore ships the SHAPE the probe will use once MCP lands, not a live server:

* :func:`seed_mcp_note` is the probe's fixture today — it writes a small note documenting the intended
  MCP server into the Workspace, so the probe has a concrete, assertable artifact while it is skipped;
* :func:`mcp_stdio_server_stub` documents the ``fastmcp`` stdio server the probe's ``context`` will
  become. It raises if entered — the probe is skip-guarded, so nothing enters it today; the raise is a
  loud guard against wiring it in before MCP actually ships.

When decode's MCP factory arrives: add the ``fastmcp`` dependency, turn :func:`mcp_stdio_server_stub`
into a real stdio server context manager (the reference shape is in its body), point the probe's
``context`` at it, and drop the probe's ``skip_reason``. The intended server:

    from fastmcp import FastMCP

    server = FastMCP("regression-fixture")

    @server.tool
    def echo(text: str) -> str:
        return text

    # served over stdio for decode's MCP factory to connect to:
    # server.run(transport="stdio")
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_MCP_NOTE = "MCP_FIXTURE.md"
_MCP_NOTE_BODY = (
    "# MCP tool-usage probe (skipped)\n\n"
    "This probe activates once decode ships its MCP tool factory (ADR-0017 §10). It will connect the "
    "agent to a local `fastmcp` stdio server exposing an `echo` tool and assert the agent calls it. "
    "See `evals/regression/fixtures/mcp.py` for the intended server shape.\n"
)


def seed_mcp_note(workspace: Path) -> Path:
    """Write the documentation note for the (skipped) MCP probe and return its path."""
    path = workspace / _MCP_NOTE
    path.write_text(_MCP_NOTE_BODY, encoding="utf-8")
    return path


@contextmanager
def mcp_stdio_server_stub(_workspace: Path) -> Iterator[str]:
    """Placeholder for the future ``fastmcp`` stdio server — raises if ever entered.

    The MCP probe is skip-guarded, so its ``context`` is never entered today. This guard makes wiring
    it in prematurely (before ``fastmcp`` and decode's MCP factory ship) fail loudly rather than serve
    a fake. Replace the raise with a real stdio server once MCP lands (reference shape in the module
    docstring).
    """
    raise NotImplementedError(
        "the MCP fixture server is a stub — decode has no MCP tool factory yet (ADR-0017 §10). "
        "Ship fastmcp + decode's MCP factory, then implement this and drop the probe's skip_reason."
    )
    yield ""  # unreachable; makes this a valid generator-based context manager
