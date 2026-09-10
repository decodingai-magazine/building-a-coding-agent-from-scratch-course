"""The run summary ``decode run --summary-json`` writes — ground truth without parsing traces (ADR-0022 §1).

A Benchmark Trial is a subprocess ``decode run``; the harness needs to know what the run did (did it
finish, did it hit its request cap, what did it cost, which branch carries the work) and must not
have to reconstruct that from an Opik trace, which would couple grading to tracing being configured.
So the runner writes ONE JSON object at the end of the run:

``{session_id, kitaru_session_id, exit_reason, error, requests, input_tokens, output_tokens,
cost_usd, handback, output}``

Usage comes from the run's own message history — the same source
``evals/harness/driver.py::_build_record`` reads — rather than from the agent's result, because the
result only exists on the completed path: a run stopped by its request ceiling, or by a provider
error, still spent real tokens and must report them. ``cost_usd`` follows
:func:`decode.observability.cost.run_cost_usd`'s honesty rule (catalog price, else the configured
rates, else ``null``).

Everything here is pure except :func:`write_summary`, which writes a temp file next to the target and
``os.replace``s it into place, so a crash mid-write never leaves a truncated file where a reader
expects JSON. Its failures — an unwritable path, a value nothing can serialise — are a logged
warning and nothing else: the summary is evidence, never a guard, and must not change a run's exit
code.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai.messages import ModelMessage, ModelResponse

from decode.observability.cost import run_cost_usd

if TYPE_CHECKING:
    # Type-only: importing the Hand-back here would drag the sandbox package into every headless
    # run, including ``none`` mode, where it must stay unimported.
    from decode.sandbox.handback import ShipResult

logger = logging.getLogger(__name__)

# How a run ended, from the harness's point of view: the agent answered, the request ceiling fired,
# or something raised. A cap is NOT an error — the run did exactly what it was told to do.
ExitReason = Literal["completed", "request_limit", "error"]


@dataclass(frozen=True, slots=True)
class RunUsage:
    """What one headless run spent: model requests, tokens, and USD when it can be priced honestly."""

    requests: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


def summarize_usage(messages: Sequence[ModelMessage]) -> RunUsage:
    """Total up a run's spend from its pydantic-ai message history (ADR-0022 §1).

    One ``ModelResponse`` = one model request, exactly as ``_build_record`` counts steps; tokens are
    each response's reported usage (a provider that reported none counts as zero, never as a crash).
    Subagent spend is NOT here: an Explore subagent runs its own agent, whose messages never join
    this history — the same boundary the eval driver's record has always had.
    """
    responses = [message for message in messages if isinstance(message, ModelResponse)]
    return RunUsage(
        requests=len(responses),
        input_tokens=sum(response.usage.input_tokens or 0 for response in responses),
        output_tokens=sum(response.usage.output_tokens or 0 for response in responses),
        cost_usd=run_cost_usd(responses),
    )


def build_summary(
    *,
    session_id: str,
    kitaru_session_id: str | None,
    exit_reason: ExitReason,
    error: str | None,
    usage: RunUsage,
    handback: ShipResult | None,
    output: str,
) -> dict[str, Any]:
    """The one JSON object a run's summary file holds — plain types only, ready for ``json.dump``.

    ``handback`` is ``{"branch", "pushed"}`` when the Hand-back shipped a Session Branch, and
    ``null`` on every skip (no ``--repo``, ``none`` mode, an unchanged Workspace, a failed
    hand-back) — a branch that does not exist is nothing for a grader to clone. An unpushed branch
    keeps ``pushed: false`` rather than disappearing: it exists locally, which is what
    never-lose-results means.
    """
    return {
        "session_id": session_id,
        "kitaru_session_id": kitaru_session_id,
        "exit_reason": exit_reason,
        "error": error,
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": usage.cost_usd,
        "handback": _handback(handback),
        "output": output,
    }


def _handback(result: ShipResult | None) -> dict[str, Any] | None:
    """The Hand-back half of the summary: the branch and whether the push landed, else ``None``."""
    if result is None or result.branch is None:
        return None
    return {"branch": result.branch, "pushed": result.pushed}


def write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    """Write ``summary`` to ``path`` as one JSON object, creating parent dirs — best-effort.

    The bytes go to a temp file in the SAME directory (so the rename stays on one filesystem) and
    :func:`os.replace` swaps it in atomically: a reader — or a re-run to a path that already holds a
    previous summary — sees either the old file or the new one, never a half-written one.

    A failure (an unwritable directory, a value nothing can serialise) is ONE warning line in the
    log and no exception: the run's exit code says what the run did, and a missing summary is the
    harness's cue that the process died before it could report — never a reason to change what the
    run itself returned.
    """
    temp: Path | None = None
    try:
        payload = json.dumps(summary, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp = Path(handle.name)
            handle.write(payload)
        # A temp file is born 0600 and ``os.replace`` keeps the source's mode; the summary is
        # evidence a grader reads, possibly as another user, so restore the mode a plain write gives.
        temp.chmod(0o644)
        os.replace(temp, path)
    except Exception:
        logger.warning("could not write the run summary to %s; continuing", path, exc_info=True)
        if temp is not None:
            # Cleanup is best-effort too — a failure here must not become the exception the
            # docstring above promises this function never raises.
            with suppress(OSError):
                temp.unlink(missing_ok=True)
        return
    logger.debug("wrote the run summary to %s", path)
