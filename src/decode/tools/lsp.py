"""The ``lsp`` tool — model-callable Code Intelligence over the LSP Service (ADR-0007).

Four ops via one ``op`` argument — ``definition`` / ``references`` / ``hover`` / ``diagnostics``
— returning model-readable strings. Line/column are 1-based (the service converts to LSP's
0-based wire basis); the three position ops require both, ``diagnostics`` needs only ``path``.
READ_ONLY, so the gate auto-allows it in every mode. Every recoverable problem (unknown op,
missing position, bad path, service UNAVAILABLE) maps to a :class:`pydantic_ai.ModelRetry` —
UNAVAILABLE means "no answer at all" and points the model at ``read``/``grep``, while an empty
answer is a plain ``"no X found"`` string. Path safety reuses the file tools' containment helper.
"""

from __future__ import annotations

import logging

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.services import lsp as lsp_service
from decode.services.lsp import UNAVAILABLE, Diagnostic, Location
from decode.tools import files
from decode.tools.approval import needs_approval

logger = logging.getLogger(__name__)

LSP_TOOL_NAME = "lsp"

# Ops that pinpoint a symbol need a 1-based line AND column; ``diagnostics`` needs only a path.
_POSITION_OPS = ("definition", "references", "hover")
_VALID_OPS = (*_POSITION_OPS, "diagnostics")

# LSP DiagnosticSeverity → readable label; unexpected values fall back to ``severity<n>``.
_SEVERITY_LABELS = {1: "error", 2: "warning", 3: "info", 4: "hint"}

# ModelRetry message for the "unavailable" case (no answer at all — NOT "found nothing"); points
# the model back at the text tools so the turn still makes progress (ADR-0007 §6).
_UNAVAILABLE_MESSAGE = (
    "code intelligence is unavailable (the language server did not respond); "
    "fall back to `read`/`grep`."
)

# The modal-sandbox note (ADR-0012 §7): host-side ``ty`` cannot reach the remote Workspace fs, so
# code intelligence is best-effort-disabled; ``none`` + ``docker`` are unaffected.
_MODAL_UNAVAILABLE_MESSAGE = (
    "code intelligence is unavailable in the modal sandbox (the language server cannot reach the "
    "remote workspace); fall back to `read`/`grep`."
)


async def lsp(
    ctx: RunContext[AgentDeps],
    op: str,
    path: str,
    line: int | None = None,
    column: int | None = None,
) -> str:
    """Query Code Intelligence for ``path`` via the LSP Service (ADR-0007, the active channel).

    ``op`` selects the query: ``definition`` / ``references`` / ``hover`` pinpoint the symbol at the
    **1-based** ``(line, column)`` (both required); ``diagnostics`` pulls ``path``'s diagnostics (no
    position needed). Returns a model-readable string — ``path:line:column`` locations, hover text, or
    a counted diagnostics list — or a ``"no X"`` sentinel when the server answered but found nothing.

    Gated like ``read`` / ``web_fetch``: raises :class:`pydantic_ai.ApprovalRequired` until approved,
    and — being :class:`~decode.permissions.types.ToolKind.READ_ONLY` — the gate auto-allows it under
    every mode (no prompt). Every recoverable problem (unknown ``op``, a missing ``line``/``column``
    for a position op, an out-of-tree/missing ``path``, or the server being **unavailable**) becomes a
    :class:`pydantic_ai.ModelRetry` so the model can adapt instead of crashing the REPL.
    """
    if needs_approval(ctx):
        logger.debug("lsp requires approval (op=%r, path=%r)", op, path)
        raise ApprovalRequired

    if settings.sandbox_mode == "modal":
        # Disable cleanly BEFORE path/op checks — deps.cwd is the empty host workspace here.
        logger.debug("lsp disabled in the modal sandbox (op=%r, path=%r)", op, path)
        raise ModelRetry(_MODAL_UNAVAILABLE_MESSAGE)

    if op not in _VALID_OPS:
        raise ModelRetry(f"Unknown op {op!r}; valid ops are: {', '.join(_VALID_OPS)}.")
    if op in _POSITION_OPS and (line is None or column is None):
        raise ModelRetry(
            f"op {op!r} requires both line and column (1-based); provide them "
            "(read them from a read/grep result)."
        )
    _resolve_target(ctx, path)

    if op == "definition":
        return await _run_definition(ctx, path, line, column)  # type: ignore[arg-type]
    if op == "references":
        return await _run_references(ctx, path, line, column)  # type: ignore[arg-type]
    if op == "hover":
        return await _run_hover(ctx, path, line, column)  # type: ignore[arg-type]
    return await _run_diagnostics(ctx, path)


def _resolve_target(ctx: RunContext[AgentDeps], path: str) -> None:
    """Reject an out-of-tree, missing, or non-file ``path`` with a ModelRetry before any server call
    (reuses the file tools' containment helper; a model mistake, distinct from server-unavailable)."""
    target = files._resolve_in_cwd(ctx.deps.cwd, path)
    if not target.exists():
        raise ModelRetry(f"No such file: {path!r}.")
    if target.is_dir():
        raise ModelRetry(f"{path!r} is a directory; give a source file path for code intelligence.")


async def _run_definition(ctx: RunContext[AgentDeps], path: str, line: int, column: int) -> str:
    """``op=definition``: the symbol's definition as ``path:line:column`` (1-based) or "no definition found"."""
    result = await lsp_service.definition(ctx.deps.cwd, path, line, column)
    if result is UNAVAILABLE:
        raise ModelRetry(_UNAVAILABLE_MESSAGE)
    if result is None:
        return "no definition found"
    return _format_location(result)


async def _run_references(ctx: RunContext[AgentDeps], path: str, line: int, column: int) -> str:
    """``op=references``: a counted ``path:line:column`` list (1-based) of call sites or "no references found"."""
    result = await lsp_service.references(ctx.deps.cwd, path, line, column)
    if result is UNAVAILABLE:
        raise ModelRetry(_UNAVAILABLE_MESSAGE)
    if not result:
        return "no references found"
    header = f"{len(result)} {_plural('reference', len(result))}:"
    return "\n".join([header, *(_format_location(loc) for loc in result)])


async def _run_hover(ctx: RunContext[AgentDeps], path: str, line: int, column: int) -> str:
    """``op=hover``: the symbol's hover text/markdown, or "no hover info" when the server has none."""
    result = await lsp_service.hover(ctx.deps.cwd, path, line, column)
    if result is UNAVAILABLE:
        raise ModelRetry(_UNAVAILABLE_MESSAGE)
    if not result:
        return "no hover info"
    return result


async def _run_diagnostics(ctx: RunContext[AgentDeps], path: str) -> str:
    """``op=diagnostics``: a counted ``severity path:line:column message`` list (all severities) or "no diagnostics"."""
    result = await lsp_service.diagnostics(ctx.deps.cwd, path)
    if result is UNAVAILABLE:
        raise ModelRetry(_UNAVAILABLE_MESSAGE)
    if not result:
        return "no diagnostics"
    header = f"{len(result)} {_plural('diagnostic', len(result))}:"
    return "\n".join([header, *(_format_diagnostic(path, diag) for diag in result)])


def _format_location(location: Location) -> str:
    """A :class:`~decode.services.lsp.types.Location` as model-readable ``path:line:column`` (1-based)."""
    return f"{location.path}:{location.line}:{location.column}"


def _format_diagnostic(path: str, diagnostic: Diagnostic) -> str:
    """A :class:`~decode.services.lsp.types.Diagnostic` as ``<severity> path:line:column message`` (1-based)."""
    label = _SEVERITY_LABELS.get(diagnostic.severity, f"severity{diagnostic.severity}")
    return f"{label} {path}:{diagnostic.line}:{diagnostic.column} {diagnostic.message}"


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"
