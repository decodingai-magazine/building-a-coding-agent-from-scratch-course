"""Fail-fast key preflight for evaluation commands."""

from __future__ import annotations

import click


def _provider_key_missing(provider: str) -> str | None:
    """Return the missing credential name for one inference provider."""
    from decode.config.settings import settings

    if provider == "openrouter":
        if not settings.openrouter_api_key.get_secret_value().strip():
            return "OPENROUTER_API_KEY"
    elif provider == "modal":
        if not settings.modal_endpoint_url.strip():
            return "MODAL_ENDPOINT_URL"
    elif not settings.gemini_api_key.get_secret_value().strip():
        return "GEMINI_API_KEY"
    return None


def eval_keys_missing(*, require_provider: bool = True) -> list[str]:
    """Return missing Langfuse and inference credentials for the selected workflow."""
    from decode.config.settings import settings
    from evals.harness.judges import judge_provider

    missing: list[str] = []
    if not settings.langfuse_public_key.get_secret_value().strip():
        missing.append("LANGFUSE_PUBLIC_KEY")
    if not settings.langfuse_secret_key.get_secret_value().strip():
        missing.append("LANGFUSE_SECRET_KEY")
    if not require_provider:
        return missing

    providers = [settings.llm_provider, judge_provider()]
    for provider in dict.fromkeys(providers):
        name = _provider_key_missing(provider)
        if name is not None and name not in missing:
            missing.append(name)
    return missing


def main() -> int:
    """Exit successfully when configured; otherwise print one actionable line."""
    missing = eval_keys_missing()
    if missing:
        click.echo(
            "evals: skipped — set "
            + ", ".join(missing)
            + " to run (see the Evals block in .env.example).",
            err=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    from decode.logging import init_logger

    init_logger()

    raise SystemExit(main())
