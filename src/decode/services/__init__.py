"""The Services Interface — boundaries the agent reaches external processes through (ADR-0007).

Distinct from :mod:`decode.tools` (what the *model* calls): ``services/`` holds the clients decode
itself drives — the LLM Gateway, Memory, MCP servers, and Language Servers. :mod:`decode.services.lsp`
is the **first** concrete entry: a hand-rolled JSON-RPC-over-stdio client for one Language Server,
delivering the Code Intelligence surface (definition / references / hover / diagnostics). No shared
"services" abstraction is introduced until a second server arrives (AGENTS.md: no abstraction without
a second caller).
"""

from __future__ import annotations
