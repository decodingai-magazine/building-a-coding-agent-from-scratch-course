"""Build a hermetic :class:`~decode.config.settings.Settings` in a suite that pollutes the env.

Two real leaks make ``Settings(_env_file=None, …)`` NOT hermetic once the whole suite has run, and
both are out of scope to fix from the context-window task (task 123) that first tripped over them:

1. **``litellm.load_dotenv()``** (noted in ``abf31e5``): importing litellm — which opik, and so the
   evals tests, pull in — copies the developer's whole ``.env`` into ``os.environ``. Process env is
   the HIGHEST-precedence settings source, so ``_env_file=None`` no longer isolates anything: a
   developer with ``LLM_PROVIDER=modal`` + ``MODAL_ENDPOINT_URL`` in ``.env`` silently gets them
   injected into every ``Settings`` built afterwards, and tests pass alone but fail in the suite.
2. **Direct assignment to the ``settings`` singleton** (``tests/unit/evals/regression/
   test_cases_grounding.py``): assigning a field on a pydantic model adds it to ``model_fields_set``
   permanently, and restoring the *value* in teardown does not undo that. Since ``model_fields_set``
   is exactly how "the operator set this explicitly" is detected, the singleton looks like it has an
   explicit ``COMPACTION_CONTEXT_WINDOW_TOKENS`` for the rest of the session.

So: never assert against the shared singleton's derived state, and scrub the env before building a
Settings whose value you intend to assert on.
"""

from __future__ import annotations

import pytest

from decode.config.settings import Settings

# The decode-owned env vars that can leak in from a developer's ``.env`` via ``load_dotenv``. Only
# the ones that steer provider selection, model identity or the context window — the surface the
# window-resolution tests assert on. Extend as new tests need new fields pinned.
LEAKY_SETTINGS_ENV_VARS = (
    "LLM_PROVIDER",
    "GEMINI_MODEL",
    "GEMINI_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_API_KEY",
    "MODAL_ENDPOINT_URL",
    "MODAL_ENDPOINT_MODEL",
    "MODAL_PROXY_TOKEN_ID",
    "MODAL_PROXY_TOKEN_SECRET",
    "COMPACTION_CONTEXT_WINDOW_TOKENS",
)


def scrub_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the leak-prone decode env vars so a fresh ``Settings`` reads only what it is given."""
    for name in LEAKY_SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def hermetic_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    """A ``Settings`` built from ``overrides`` + declared defaults ONLY — no ``.env``, no host env.

    ``overrides`` land in ``model_fields_set`` exactly as a real settings source would, so the
    "explicit wins" branch of window resolution stays honest.
    """
    scrub_settings_env(monkeypatch)
    return Settings(_env_file=None, **overrides)
