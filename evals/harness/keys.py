"""Fail-fast key preflight for ``make eval-benchmark`` / ``make eval-regression`` (ADR-0017 §9).

Both targets drive the REAL agent (an inference key) and store the experiment in Opik (``OPIK_API_KEY``);
without them the underlying command tracebacks deep in opik/inference. This module is the guard the
Makefile runs FIRST — invoked as ``python -m evals.harness.keys``:

* it reads the resolved decode ``settings`` (imported lazily), so a key set in ``.env`` counts, not just
  the process env — a plain ``$(GEMINI_API_KEY)`` shell check would miss the common ``.env`` case;
* when a required key is missing it prints ONE friendly line and exits non-zero, so the Make recipe
  skips the expensive command instead of crashing (see the ``if`` guard in the Makefile).

:func:`eval_keys_missing` is the ONE shared preflight for every eval track — this Makefile guard, the
online judge (:func:`evals.harness.online.online_keys_missing` delegates to it) and the pre-merge
threshold ritual (``evals/regression/test_thresholds.py`` calls it). The required set is provider-
aware and settings-backed: ``OPIK_API_KEY`` plus the active provider's key (``gemini`` →
``GEMINI_API_KEY``, ``openrouter`` → ``OPENROUTER_API_KEY``, ``modal`` → ``MODAL_ENDPOINT_URL``).
"""

from __future__ import annotations

import click


def eval_keys_missing() -> list[str]:
    """The env-var names an eval target needs but does not have — empty means good to run.

    The single shared, settings-backed, provider-aware key preflight for the whole suite (this
    Makefile guard, the online judge, and the pre-merge threshold gate all route through here, so they
    cannot drift). Reads the resolved decode ``settings`` (imported lazily so importing this module
    stays cheap), so a key in ``.env`` counts — never a raw ``os.environ`` read. ``OPIK_API_KEY`` is
    always required; the second entry is the active provider's inference key.
    """
    from decode.config.settings import settings

    missing: list[str] = []
    if not settings.opik_api_key.get_secret_value().strip():
        missing.append("OPIK_API_KEY")

    provider = settings.llm_provider
    if provider == "openrouter":
        if not settings.openrouter_api_key.get_secret_value().strip():
            missing.append("OPENROUTER_API_KEY")
    elif provider == "modal":
        if not settings.modal_endpoint_url.strip():
            missing.append("MODAL_ENDPOINT_URL")
    elif not settings.gemini_api_key.get_secret_value().strip():
        missing.append("GEMINI_API_KEY")
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
