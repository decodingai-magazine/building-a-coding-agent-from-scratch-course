"""The ONE reader of an Opik **tool span** — shared by the Kitaru importer and ``evals mine``.

Two callers ask the same three questions of a raw Opik span dict: *is this a tool call?*, *which
tool?*, and *what went in / came out?*. Task 163 needed them in ``evals/harness/mine.py`` while
``importers/opik_importer.py`` already had them; rather than a second copy (which would drift the
moment pydantic-ai renames an attribute — it already did, see below) they live here, in a module
that imports nothing but the stdlib, so ``evals`` can use it without pulling ``kitaru`` in.

Detection is by **provider evidence, never by span name**: a tool span is one whose metadata
carries ``gen_ai.operation.name == "execute_tool"``; the tool's name is parsed out of logfire's
``logfire.msg`` ("running tool: <name>"). A span merely *named* ``execute_tool read`` is not one.

Two payload spellings are live at once, so both are read:

* ``gen_ai.tool.call.arguments`` / ``gen_ai.tool.call.result`` — pydantic-ai's current
  instrumentation version (verified on ``decode-prod``, 2026-09);
* ``tool_arguments`` / ``tool_response`` — the older version the importer's 2026-08 sample used.

:func:`tool_span_deferred` reads the third fact only a gated harness cares about:
``pydantic_ai.tool.deferral.name == "ApprovalRequired"``, which pydantic-ai sets on the span of a
call that raised :class:`pydantic_ai.ApprovalRequired` instead of running. Verified live: the
attribute survives Opik ingestion inside the span's ``input`` dict, and such a span carries no
result. An APPROVED call emits a second, completed span for the same tool + arguments on the
resume leg; a DENIED one never does — which is how ``evals mine --preset denied`` counts denials
(``evals.harness.mine.denied_tool_calls``).
"""

from __future__ import annotations

from typing import Any

TOOL_OPERATION_NAME = "execute_tool"
TOOL_MSG_PREFIX = "running tool: "
DEFERRAL_ATTRIBUTE = "pydantic_ai.tool.deferral.name"
APPROVAL_REQUIRED = "ApprovalRequired"

_ARGUMENT_KEYS = ("gen_ai.tool.call.arguments", "tool_arguments")
_RESULT_KEYS = ("gen_ai.tool.call.result", "tool_response")


def _mapping(value: object) -> dict[str, Any]:
    """The value as a dict, or an empty one — Opik omits absent fields entirely."""
    return value if isinstance(value, dict) else {}


def is_tool_span(span: dict[str, Any]) -> bool:
    """Whether this span is a tool execution, judged on ``gen_ai.operation.name`` alone."""
    return _mapping(span.get("metadata")).get("gen_ai.operation.name") == TOOL_OPERATION_NAME


def tool_span_name(span: dict[str, Any]) -> str | None:
    """The tool's name parsed from ``logfire.msg`` ("running tool: <name>"), else ``None``."""
    message = _mapping(span.get("metadata")).get("logfire.msg")
    if isinstance(message, str) and message.startswith(TOOL_MSG_PREFIX):
        return message[len(TOOL_MSG_PREFIX) :] or None
    return None


def _payload(span: dict[str, Any], field: str, keys: tuple[str, ...]) -> Any:
    payload = _mapping(span.get(field))
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def tool_span_arguments(span: dict[str, Any]) -> Any:
    """The call's arguments under either instrumentation spelling; ``None`` when absent."""
    return _payload(span, "input", _ARGUMENT_KEYS)


def tool_span_result(span: dict[str, Any]) -> Any:
    """The call's result under either instrumentation spelling; ``None`` when the call never ran."""
    return _payload(span, "output", _RESULT_KEYS)


def tool_span_deferred(span: dict[str, Any]) -> bool:
    """Whether this span is the DEFERRED leg of a gated call (``ApprovalRequired``, no result)."""
    return _mapping(span.get("input")).get(DEFERRAL_ATTRIBUTE) == APPROVAL_REQUIRED
