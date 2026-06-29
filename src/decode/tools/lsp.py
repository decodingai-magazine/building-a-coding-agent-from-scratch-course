"""The ``lsp`` tool — model-callable Code Intelligence over the LSP Service (ADR-0007, the active channel).

``lsp`` is the **active** half of decode's LSP integration: a single model-callable tool that gives
the agent the **semantic-graph** view text tools cannot (``read`` numbers lines, ``grep`` regex-
matches, but neither can say "where is this symbol defined?"). It exposes four ops over the task-051
LSP Service via one ``op`` argument — ``definition`` / ``references`` / ``hover`` / ``diagnostics`` —
and returns model-readable strings:

* ``definition`` → the target ``path:line:column`` (1-based), or ``"no definition found"``;
* ``references`` → a counted newline list of ``path:line:column`` (1-based), or ``"no references found"``;
* ``hover`` → the hover text/markdown, or ``"no hover info"``;
* ``diagnostics`` → a counted ``severity path:line:column message`` list of **all** severities (the
  tool is the explicit query surface — the *enricher*, task 053, is the errors-only one), or
  ``"no diagnostics"``.

**1-based line/column.** ``definition`` / ``references`` / ``hover`` take a symbol position the model
read out of a ``read`` (``cat -n``) or ``grep`` (``path:lineno``) result, so the surface here is
**1-based** — the service/client converts to LSP's 0-based wire basis at its one boundary. Those three
ops **require** both ``line`` and ``column``; ``diagnostics`` needs only ``path`` (any line/column is
ignored).

**READ_ONLY, auto-allowed.** Reading code intelligence has no disk/exec side effect, so ``lsp`` is
:class:`~decode.permissions.types.ToolKind.READ_ONLY`. Like ``read`` / ``grep`` / ``web_fetch`` it
raises :class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set, and the gate
**auto-allows** it under every mode (read-only allows everywhere; ADR-0003 §4) — so it never surfaces
a permission prompt.

**Never crashes the loop.** Every recoverable problem maps to a model-readable
:class:`pydantic_ai.ModelRetry`, never an exception into the loop: an unknown ``op`` (lists the four),
a missing ``line``/``column`` for a position op, an out-of-tree or missing ``path``, and — crucially —
the service reporting **unavailable** (no server, timeout, broken spawn, ``lsp_enabled == False``). The
service distinguishes ``UNAVAILABLE`` ("no answer at all") from ``None`` / an empty list ("answered,
found nothing"): only the former becomes a ``ModelRetry`` telling the model to fall back to
``read`` / ``grep``; the latter is the plain ``"no X found"`` string, **not** a retry.

Path safety reuses the file tools' containment helper (:func:`decode.tools.files._resolve_in_cwd`), so
``lsp`` can never reach a symbol outside the project tree.
"""

from __future__ import annotations

import logging

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.services import lsp as lsp_service
from decode.services.lsp import UNAVAILABLE, Diagnostic, Location
from decode.tools import files
from decode.tools.approval import needs_approval

logger = logging.getLogger(__name__)

LSP_TOOL_NAME = "lsp"

# The ops that pinpoint a symbol: they need a 1-based ``line`` AND ``column`` (the model reads the
# position out of a ``read``/``grep`` result). ``diagnostics`` is the odd one out — it needs only a
# path — so it is kept separate and ``_VALID_OPS`` is the full four-op surface the model may pass.
_POSITION_OPS = ("definition", "references", "hover")
_VALID_OPS = (*_POSITION_OPS, "diagnostics")

# LSP ``DiagnosticSeverity`` → a readable label for the model (1=Error … 4=Hint). The ``diagnostics``
# op surfaces ALL severities (it is the explicit query surface), so every code gets a name; an
# unexpected value falls back to ``severity<n>`` rather than crashing on a missing key.
_SEVERITY_LABELS = {1: "error", 2: "warning", 3: "info", 4: "hint"}

# The single ModelRetry message for the "unavailable" case — the Language Server could not answer
# (no server, timeout, broken spawn, or ``lsp_enabled == False``). It points the model back at the
# text tools so the turn still makes progress (ADR-0007 §6); it is NOT used for "found nothing".
_UNAVAILABLE_MESSAGE = (
    "code intelligence is unavailable (the language server did not respond); "
    "fall back to `read`/`grep`."
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
    """Reject an out-of-tree, missing, or non-file ``path`` with a ModelRetry before any server call.

    Reuses the file tools' containment helper (:func:`decode.tools.files._resolve_in_cwd`) so a path
    escaping ``ctx.deps.cwd`` is refused exactly as ``read`` refuses it. A missing path or a directory
    is a model mistake (not "code intelligence unavailable"), so it is reported as its own clear
    :class:`pydantic_ai.ModelRetry` rather than being conflated with the server-unavailable case.
    """
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
    """``word`` pluralised by a naive trailing ``s`` (only the simple "reference"/"diagnostic" here)."""
    return word if count == 1 else f"{word}s"
