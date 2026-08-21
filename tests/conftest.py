"""Shared pytest fixtures. Mirror src/ 1:1 under tests/unit and tests/integration."""

import os

# Disable file logging for the whole suite BEFORE any decode import (Fix 3). ``init_logger`` runs
# at import time in the ``decode.cli`` entrypoint; an empty ``DECODE_LOG_FILE`` installs a
# NullHandler so the suite never creates ``.decode/logs/`` in the repo. The individual
# ``test_logging`` tests set their own value via ``monkeypatch.setenv`` (which wins per-test).
os.environ["DECODE_LOG_FILE"] = ""

import pytest
from pydantic import SecretStr

# The Kitaru/ZenML store-isolation fixtures that used to be registered here died with the durable
# runtime (ADR-0019 §1): the headless runner boots no local stack, so there is no global store to
# redirect and no durable wait to resolve.
from support.git_env import GIT_HOOK_ENV_VARS


@pytest.fixture(autouse=True)
def _scrub_git_hook_env(monkeypatch):
    """Hermeticity guard — no test inherits git's repository location (task 111).

    Git exports ``GIT_DIR`` & friends into every hook it runs, and our pre-push hook runs the unit
    suite. Every test that shells out to ``git`` therefore inherited a pointer to the *repository
    being pushed* and quietly worked on it instead of the throwaway repo it had just built under
    ``tmp_path`` — ``git -C <tmp_path>`` stops meaning ``<tmp_path>`` once ``GIT_DIR`` is set.

    Two things this cost us, both observed: a leaked ``GIT_DIR`` fails 7 of the ``test_workspace.py``
    tests (~20 across the suite under the real hook), so pushing from a worktree meant ``--no-verify``
    every time — a hook you must always skip is a hook that will miss the one real failure; and a
    leaked ``GIT_INDEX_FILE`` let a test's ``git add`` stage against the *outer* repo's index, leaving
    a worktree with 252 files staged as deleted while the identical files sat on disk.

    The fix belongs here, not in the tests that call git: the leak is environmental, so it would
    otherwise have to be re-defended in every future git-touching test. See ``support/git_env`` for
    why exactly these variables and not a blanket ``GIT_*`` purge. Tests that point git somewhere on
    purpose still can — their ``monkeypatch.setenv`` runs after this fixture and wins (pinned by
    ``test_a_test_may_still_set_a_git_var_itself``).
    """
    for name in GIT_HOOK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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
def _no_context_window_probe(monkeypatch):
    """Hermeticity guard — no test may probe a real provider for a context window (task 123).

    :func:`decode.agent.context_window.resolve_context_window` asks the active provider for the
    model's real window before falling back to the static table, and it is wired into the cli
    startup notice and every ``AgentDeps`` the entrypoints build. Any test that drives the real
    ``run_app`` / headless flow would therefore issue a live ``GET /v1/models`` or a google-genai
    ``models.get`` — and a Modal endpoint answers that by cold-starting an H100. Exactly the leak
    :func:`_no_real_provider_key` guards against, one API surface over.

    Stubbing ``_probe`` (not the HTTP client) is deliberate: it is the single choke point all three
    provider branches pass through, so this cannot be bypassed by a future fourth provider that
    reaches for a different client. Resolution still runs for real — table, fallback and the
    explicit-setting short-circuit are all unaffected, so the tests keep asserting real behaviour.

    Tests that exercise probing install their own ``_probe`` / client stub (which runs after this
    fixture, so it wins); ``test_context_window.py`` restores the real function outright.
    """
    from decode.agent import context_window

    monkeypatch.setattr(context_window, "_probe", lambda *args, **kwargs: None)
    context_window.reset_probe_cache()
    yield
    context_window.reset_probe_cache()


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
def _default_decode_env(monkeypatch):
    """Hermeticity guard — pin ``DECODE_ENV=local`` for the whole suite (ADR-0015 §1, task 097).

    ``DECODE_ENV`` selects the *injection mechanism*: at any remote value the ``Settings`` chain drops
    ``.env`` and hydrates from the Kitaru Environment Bucket instead — importing kitaru, touching the
    local ZenML store, and (on a missing bucket) tripping the new cli startup guard. A developer who
    exported ``DECODE_ENV=staging`` (or put it in ``.env``) would flip the whole suite remote and see
    failures no one else gets — the same class of leak :func:`_default_sandbox_mode` /
    :func:`_no_sandbox_git_token` exist for, and it has bitten this repo twice.

    Deleting the env var also scrubs it for the subprocesses tests spawn (they inherit ``os.environ``),
    so the "at ``local``, decode never imports kitaru" invariant check stays honest. The bucket tests
    set their own value with ``monkeypatch.setenv`` (which runs after this fixture, so it wins).
    """
    monkeypatch.delenv("DECODE_ENV", raising=False)

    from decode.config.settings import settings

    monkeypatch.setattr(settings, "decode_env", "local", raising=False)


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
def _no_sandbox_git_token(monkeypatch):
    """Hermeticity guard — blank ``SANDBOX_GIT_TOKEN`` on the singleton (ADR-0016 §2).

    A non-empty token is a *behavioral switch*, not just a value: it changes the ``docker run`` argv
    (a ``-e GITHUB_TOKEN`` pass-through) and the worker's git setup in BOTH backends (the injected
    credential helper; on modal, an extra image layer + a ``modal.Secret``). A developer who put a
    real PAT in ``.env`` — the natural thing to do when trying the sandbox — would flip those paths on
    inside the suite and see failures no one else gets (this fixture exists because that happened).
    Same class of leak as :func:`_no_real_provider_key` / :func:`_default_sandbox_mode`.

    Tests that exercise the token paths set their own value with ``monkeypatch.setattr`` (which runs
    after this fixture, so it wins).
    """
    monkeypatch.delenv("SANDBOX_GIT_TOKEN", raising=False)

    from decode.config.settings import settings

    monkeypatch.setattr(settings, "sandbox_git_token", None, raising=False)


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
