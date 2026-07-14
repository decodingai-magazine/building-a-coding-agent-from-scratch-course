"""Shared fixture builders the regression probes seed a fresh Workspace with (ADR-0017 §6).

Each builder is a small, offline, reusable seed a probe's ``fixture`` composes:

* :func:`seed_type_error` — a tiny Python module carrying one obvious type error (the LSP / fix-a-bug
  probes);
* :func:`seed_skills_dir` — a ``.decode/skills/<name>/SKILL.md`` layout (the skill-dispatch probes);
* :func:`serve_page` — a stdlib ``http.server`` serving one known page on localhost, as a context
  manager a probe enters around the run (the web-fetch probes);
* :func:`near_limit_history` — a pre-filled pydantic-ai conversation sized near a token budget (the
  compaction probe's ``message_history``);
* :func:`seed_mcp_note` / :func:`mcp_stdio_server_stub` — the documented stub for the skip-guarded
  MCP-tool-usage probe (decode has no MCP factory yet, so no ``fastmcp`` dependency).

They are re-exported here so a probe writes ``from evals.regression.fixtures import seed_type_error``.
"""

from __future__ import annotations

from evals.regression.fixtures.conversation import near_limit_history
from evals.regression.fixtures.files import seed_skills_dir, seed_type_error
from evals.regression.fixtures.mcp import mcp_stdio_server_stub, seed_mcp_note
from evals.regression.fixtures.web import serve_page

__all__ = [
    "mcp_stdio_server_stub",
    "near_limit_history",
    "seed_mcp_note",
    "seed_skills_dir",
    "seed_type_error",
    "serve_page",
]
