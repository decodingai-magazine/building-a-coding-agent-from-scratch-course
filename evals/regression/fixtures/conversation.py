"""A near-limit pre-filled conversation for the compaction probe (ADR-0017 §6; ADR-0006).

decode represents an in-progress conversation as a list of pydantic-ai ``ModelMessage``s — the exact
shape ``decode.context.compaction`` reads and the eval driver's ``message_history`` seeds. The
compaction probe needs a history that has grown near the model's context window so the agent must
compact to keep working. :func:`near_limit_history` builds one deterministically: alternating
user/assistant turns padded with filler until the coarse ``chars≈/4`` token estimate reaches a target,
the same estimator ``compaction.split_tail`` uses to size a tail.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

# The coarse chars→tokens divisor decode uses for tail sizing (ADR-0006). Matched here so a caller can
# reason about the produced history in the same token terms the compaction core does.
CHARS_PER_TOKEN = 4


def near_limit_history(
    *, target_tokens: int = 4000, filler: str = "context "
) -> list[ModelMessage]:
    """Build alternating user/assistant turns padded to roughly ``target_tokens`` (ADR-0006).

    Each round is a ``ModelRequest``/``ModelResponse`` pair whose text is padded with ``filler`` so the
    coarse ``chars≈/4`` estimate of the whole history reaches ``target_tokens`` (it may slightly
    exceed, since a whole final round is never split). The turns are numbered so the transcript reads
    as a real growing session. ``target_tokens`` must be positive.
    """
    if target_tokens <= 0:
        raise ValueError(f"target_tokens must be positive, got {target_tokens}")

    messages: list[ModelMessage] = []
    turn = 0
    while _estimate_tokens(messages) < target_tokens:
        turn += 1
        padding = filler * 40
        messages.append(
            ModelRequest(parts=[UserPromptPart(content=f"Turn {turn} question. {padding}")])
        )
        messages.append(ModelResponse(parts=[TextPart(content=f"Turn {turn} answer. {padding}")]))
    return messages


def _estimate_tokens(messages: list[ModelMessage]) -> int:
    """Coarse ``chars≈/4`` token estimate over every text part in the history (ADR-0006 tail sizing)."""
    chars = 0
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", "")
            if isinstance(content, str):
                chars += len(content)
    return chars // CHARS_PER_TOKEN
