"""Shared pytest fixtures. Mirror src/ 1:1 under tests/unit and tests/integration."""

import os

# Disable file logging for the whole suite BEFORE any decode import (Fix 3). ``init_logger`` runs
# at import time in the ``decode.cli`` entrypoint; an empty ``DECODE_LOG_FILE`` installs a
# NullHandler so the suite never creates ``.decode/logs/`` in the repo. The individual
# ``test_logging`` tests set their own value via ``monkeypatch.setenv`` (which wins per-test).
os.environ["DECODE_LOG_FILE"] = ""

import pytest
from pydantic import SecretStr

# Headless-Runtime test fixtures are registered HERE, at the rootdir conftest, on purpose: under
# ``--import-mode=importlib`` a per-package ``conftest`` is reliably applied only when its tests are
# collected contiguously, so a non-runtime file collected between two runtime files (e.g. under
# ``pytest-randomly``) de-associated ``tests/unit/decode/runtime/conftest.py`` from the second file —
# its autouse Kitaru-store isolation stopped running and a ``create_secret`` fell through to the
# developer's real ZenML store (task 065). The rootdir conftest is the only ancestor always in scope
# for every collected test, so its fixtures apply in any order. ``isolated_kitaru_store`` is autouse
# but gated to the unit runtime package (a no-op importing nothing elsewhere); see the module.
from support.runtime_fixtures import (  # noqa: F401 — re-exported so pytest registers them
    inline_wait_resolver,
    isolated_kitaru_store,
    runtime_secret_name,
)


@pytest.fixture(autouse=True)
def _no_real_provider_key(monkeypatch):
    """Hermeticity guard — no test may reach a real LLM provider.

    ``decode.config.settings.settings`` is built at import time and may have loaded a real
    ``GEMINI_API_KEY`` from a developer's local ``.env``. Without scrubbing it, any test that
    drives the real :func:`decode.tui.app.run_app` would, on exit, run the memory write-back
    (:func:`decode.memory.extract.extract_on_exit` → a *live* Gemini call) — leaking sockets and
    writing the repo's ``MEMORY.md``. CI has no key so it never hit this; a developer with a key
    did. We delete the env var and blank the singleton's key so that path no-ops everywhere.

    Tests that need the agent to *build* inject their own dummy key in a module-local autouse
    fixture (which runs after this conftest one, so it wins); tests that exercise the summarizer
    pass an explicit stub ``Model``. Settings instances built with an explicit ``_env_file`` are
    unaffected (they don't touch this singleton).
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    from decode.config.settings import settings

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""), raising=False)


@pytest.fixture(autouse=True)
def _no_opik_tracing(monkeypatch):
    """Hermeticity guard — no test may configure real Opik export (ADR-0014 §7, task 091).

    ``decode.config.settings.settings`` is built at import time and may have loaded a real
    ``OPIK_API_KEY`` from a developer's local ``.env``. Once :func:`decode.observability.init_tracing`
    is wired into ``run_app`` / the headless flows (tasks 092+), that key would make the suite configure
    a live OTLP exporter to Comet on the first turn/run — a real network side effect. Mirroring
    :func:`_no_real_provider_key`, we delete the env var and blank the singleton's key so
    ``init_tracing()`` no-ops everywhere.

    Span-asserting tracing tests opt in with their own fake key + ``logfire.testing``'s in-memory
    exporter (which wins, running after this conftest fixture) and reset the module flag via
    ``reset_tracing()`` themselves. Kept separate from the settings-block tests, which build explicit
    ``Settings(_env_file=…)`` objects untouched by this singleton blanking.
    """
    monkeypatch.delenv("OPIK_API_KEY", raising=False)

    from decode.config.settings import settings

    monkeypatch.setattr(settings, "opik_api_key", SecretStr(""), raising=False)


@pytest.fixture(autouse=True)
def _default_sandbox_mode(monkeypatch):
    """Hermeticity guard — pin ``SANDBOX_MODE=none`` on the singleton (ADR-0011, task 071).

    The sandbox backend guard (:func:`decode.cli._sandbox_config_error`) is wired into both the REPL
    startup chain and the headless ``decode run`` / ``decode replay`` pre-flight, and both read the
    ``settings`` singleton's ``sandbox_mode``. A developer who exported ``SANDBOX_MODE=docker`` /
    ``modal`` (or set it in ``.env``) would otherwise trip that guard in every test that drives the
    cli, changing outcomes based on local env — exactly the leak :func:`_no_real_provider_key` guards
    against for the provider key. Pin the default ``none`` here so the suite is hermetic; the task-071
    tests that exercise the docker/modal guard override this with their own patch (which wins).
    """
    from decode.config.settings import settings

    monkeypatch.setattr(settings, "sandbox_mode", "none", raising=False)


@pytest.fixture(autouse=True)
def _reset_sandbox_executor():
    """Hermeticity guard — reset the ``bash`` executor selection memo around every test (ADR-0011 §4).

    ``decode.tools.bash`` memoizes the ``CommandExecutor`` it selects by ``SANDBOX_MODE`` on the first
    ``bash`` call and fixes it for the session. Without a reset a test that selected a docker/modal (or
    fake) executor would leak that choice into the next test — e.g. a later plain ``bash`` test would
    route into a real container and hang with no daemon. Resetting before AND after every test pins each
    to the fresh ``none``-mode ``LocalExecutor`` default and leaves nothing behind. Registered at the
    rootdir conftest (like :func:`_default_sandbox_mode`) so it applies in any collection order.
    """
    from decode.tools import bash

    bash.reset_executor()
    yield
    bash.reset_executor()


@pytest.fixture(autouse=True)
def _reset_subagent_seam():
    """Hermeticity guard — clear the subagent-spawn main-Agent seam around every test (ADR-0013 §6).

    ``decode.tools.agent`` holds the running Agent in a set-once module seam (``set_main_agent``, called
    by every ``build_agent``) and caches one concurrency semaphore per running loop. Without a reset a
    test that built an agent would leave the seam pointing at a stale Agent for the next test that
    exercises the ``agent`` tool, and a re-sized ``subagent_max_parallel`` would not take effect. Reset
    before AND after every test so each starts from a clean seam. Registered at the rootdir conftest
    (like :func:`_reset_sandbox_executor`) so it applies in any collection order; importing the module
    pulls in no kitaru, keeping the REPL-safety invariant intact.
    """
    from decode.tools import agent

    agent.reset_main_agent()
    agent._reset_semaphores()
    yield
    agent.reset_main_agent()
    agent._reset_semaphores()
