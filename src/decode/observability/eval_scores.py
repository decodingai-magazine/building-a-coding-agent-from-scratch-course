"""Langfuse score reporting for completed evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from decode.config.settings import settings


@dataclass(frozen=True, slots=True)
class EvalScore:
    """A numeric evaluation score associated with one Langfuse session."""

    session_id: str
    name: str
    value: float
    comment: str | None = None
    metadata: dict[str, Any] | None = None


def report_eval_scores(scores: list[EvalScore]) -> int | None:
    """Send scores to Langfuse; return ``None`` when reporting is not configured.

    A configured reporting failure is allowed to surface to the caller rather than silently losing
    evaluation results.
    """
    secret_key = settings.langfuse_secret_key.get_secret_value()
    public_key = settings.langfuse_public_key.get_secret_value()
    if not secret_key and not public_key:
        return None
    if not secret_key or not public_key:
        raise ValueError("Langfuse score reporting requires both Langfuse API keys.")
    if not scores:
        return 0

    from langfuse import Langfuse

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=settings.langfuse_base_url,
        environment=settings.decode_env,
        tracing_enabled=False,
    )
    try:
        for score in scores:
            client.create_score(
                name=score.name,
                value=score.value,
                session_id=score.session_id,
                data_type="NUMERIC",
                comment=score.comment,
                metadata=score.metadata,
                environment=settings.decode_env,
            )
        client.flush()
    finally:
        client.shutdown()
    return len(scores)
