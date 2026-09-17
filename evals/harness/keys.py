"""Fail-fast key preflight for ``make eval-benchmark`` / ``make eval-regression-dataset`` (ADR-0017 §9).

Both targets drive the REAL agent (an inference key) and store the experiment in Opik (``OPIK_API_KEY``);
without them the underlying command tracebacks deep in opik/inference. This module is the guard the
Makefile runs FIRST — invoked as ``python -m evals.harness.keys``:

* it reads the resolved decode ``settings`` (imported lazily), so a key set in ``.env`` counts, not just
  the process env — a plain ``$(GEMINI_API_KEY)`` shell check would miss the common ``.env`` case;
* when a required key is missing it prints ONE friendly line and exits non-zero, so the Make recipe
  skips the expensive command instead of crashing (see the ``if`` guard in the Makefile).

:func:`eval_keys_missing` is the ONE shared preflight for every eval track — this Makefile guard and
the pre-merge threshold ritual (``evals/regression/test_thresholds.py`` calls it). The required set is provider-
aware and settings-backed: ``OPIK_API_KEY`` plus the key of every provider the command will CALL
(``gemini`` → ``GEMINI_API_KEY``, ``openrouter`` → ``OPENROUTER_API_KEY``, ``modal`` →
``MODAL_ENDPOINT_URL``). Since the judge has its own provider (``EVAL_JUDGE_PROVIDER``, ADR-0022 §7)
that is up to TWO keys: this one guard serves ``make eval-benchmark`` (drives the AGENT) and ``make
eval-regression-dataset`` (drives the agent AND judges its answers) from a single invocation that cannot
tell them apart, so when the two providers differ it demands both. One opt-out, named at the call
site: ``require_provider=False`` for the Opik-only live-project commands (``evals mine``, ``evals
kitaru …``), which run no inference at all.
"""

from __future__ import annotations

import click


def _provider_key_missing(provider: str) -> str | None:
    """The env var ``provider`` needs but does not have, else ``None`` (ADR-0017 §9).

    One provider, one name — the mapping both the agent's and the judge's provider are read through,
    so a new provider is added in exactly one place. ``modal`` is the odd one out: its "key" is the
    endpoint URL (auth rides the proxy-token headers, which an ``--unauthenticated`` endpoint does
    not need at all), so an unreachable endpoint is the thing worth failing fast on.
    """
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
    """The env-var names an eval target needs but does not have — empty means good to run.

    The single shared, settings-backed, provider-aware key preflight for the whole suite (this
    Makefile guard and the pre-merge threshold gate both route through here, so they cannot drift). Reads the resolved decode ``settings`` (imported lazily so importing this module
    stays cheap), so a key in ``.env`` counts — never a raw ``os.environ`` read. ``OPIK_API_KEY`` is
    always required; after it come the inference keys, the AGENT's provider first and the JUDGE's
    (:func:`evals.harness.judges.judge_provider`) second, deduplicated — when the two providers are
    the same, which is the default, the list is byte-identical to the pre-``EVAL_JUDGE_PROVIDER`` one.

    ``require_provider=False`` drops EVERY inference key, for the Opik-only commands that make no
    LLM call (``evals mine`` only reads traces). Demanding a provider key there blocks a real case — the repo's own
    ``.env`` ships ``LLM_PROVIDER=modal``, so a checkout with just ``OPIK_API_KEY`` was told to set
    ``MODAL_ENDPOINT_URL`` to run a read-only query.
    """
    from decode.config.settings import settings
    from evals.harness.judges import judge_provider

    missing: list[str] = []
    if not settings.opik_api_key.get_secret_value().strip():
        missing.append("OPIK_API_KEY")
    if not require_provider:
        return missing

    providers = [settings.llm_provider, judge_provider()]
    for provider in dict.fromkeys(providers):  # dedup, agent-then-judge order preserved
        name = _provider_key_missing(provider)
        if name is not None and name not in missing:
            missing.append(name)
    return missing


def main() -> int:
    """Exit 0 when every required key resolves; else print one friendly skip line and exit 1.

    The Makefile runs this before the expensive command and gates on its exit code, so a keyless
    checkout skips friendly (``make eval-benchmark`` prints the line and does nothing) instead of
    tracebacking inside opik/inference.
    """
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
