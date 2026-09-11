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
``GEMINI_API_KEY``, ``openrouter`` → ``OPENROUTER_API_KEY``, ``modal`` → ``MODAL_ENDPOINT_URL``) —
except for the Opik-only live-project commands (``evals mine``, ``evals online-rule create``), which
run no inference and pass ``require_provider=False`` for ``OPIK_API_KEY`` alone.
"""

from __future__ import annotations

import click


def eval_keys_missing(*, require_provider: bool = True) -> list[str]:
    """The env-var names an eval target needs but does not have — empty means good to run.

    The single shared, settings-backed, provider-aware key preflight for the whole suite (this
    Makefile guard, the online judge, and the pre-merge threshold gate all route through here, so they
    cannot drift). Reads the resolved decode ``settings`` (imported lazily so importing this module
    stays cheap), so a key in ``.env`` counts — never a raw ``os.environ`` read. ``OPIK_API_KEY`` is
    always required; the second entry is the active provider's inference key.

    ``require_provider=False`` drops that second entry, for the Opik-only commands that make NO
    inference call: ``evals mine`` only reads traces, and ``evals online-rule create`` writes a rule
    whose judge runs on OPIK's server-side provider, never on decode's key. Demanding the provider
    key there blocks a real case — the repo's own ``.env`` ships ``LLM_PROVIDER=modal``, so a
    checkout with just ``OPIK_API_KEY`` was told to set ``MODAL_ENDPOINT_URL`` to run a read-only
    query. Every other caller keeps the default and is unchanged.
    """
    from decode.config.settings import settings

    missing: list[str] = []
    if not settings.opik_api_key.get_secret_value().strip():
        missing.append("OPIK_API_KEY")
    if not require_provider:
        return missing

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
