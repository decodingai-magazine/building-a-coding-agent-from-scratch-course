"""Headless-Runtime test fixtures (ADR-0008), registered at the **rootdir** ``tests/conftest.py``.

They live in ``tests/support`` — not ``tests/unit/decode/runtime/conftest.py`` — because under
``--import-mode=importlib`` a per-package conftest only reliably applies when its tests are collected
contiguously: an interleaved non-runtime file de-associated the autouse store isolation (letting
``create_secret``/``get_secret`` fall through to the developer's **real** ZenML store) and broke the
named fixtures. The rootdir conftest is the only ancestor always in scope, so registering there makes
the isolation order-robust (task 065). :func:`isolated_kitaru_store` is gated to the unit runtime
package and is a pure no-op for every other test.
"""

from __future__ import annotations

import gc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


def _is_runtime_unit_test(request: pytest.FixtureRequest) -> bool:
    """True only for tests in the unit runtime package (``tests/unit/decode/runtime/``).

    The gate keeps the rootdir-registered autouse fixture inert for every other test (it imports no
    zenml/kitaru and has no side effect). The integration runtime capstone lives under
    ``tests/integration`` (parent ``integration``) and isolates its own store, so it is excluded.
    """
    return request.path.parent.name == "runtime"


@pytest.fixture(autouse=True)
def isolated_kitaru_store(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path | None]:
    """Redirect Kitaru/ZenML's store + config under ``tmp_path`` for every unit runtime test.

    Kitaru's local stack persists checkpoints/metadata through ZenML, which by default writes under
    the user's home (or a configured ZenML server). We redirect ``Path.home`` / ``click.get_app_dir``
    / ``ZENML_CONFIG_PATH`` to ``tmp_path``, disable analytics, and reset the ZenML global-config +
    client singletons before and after so no test ever touches real user state or makes a network
    call. ``cwd`` is moved into ``tmp_path`` too, so any tool that writes a file stays inside the
    sandbox. The reset runs *before* the yield so a sibling non-isolated test that initialized ZenML
    against a real store cannot leak into this test. This fixture is autouse + registered at the
    rootdir conftest so it applies in any collection order (see the module docstring); it is a pure
    no-op for non-runtime tests.
    """
    if not _is_runtime_unit_test(request):
        yield None
        return

    from zenml.client import Client
    from zenml.config.global_config import GlobalConfiguration

    config_dir = tmp_path / "kitaru-config"
    config_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("click.get_app_dir", lambda app_name: str(config_dir))
    monkeypatch.setenv("ZENML_CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("ZENML_ANALYTICS_OPT_IN", "false")
    monkeypatch.chdir(tmp_path)

    GlobalConfiguration._reset_instance()
    Client._reset_instance()
    try:
        yield tmp_path
    finally:
        # Release the live-flow resources WITHIN this fixture's scope, deterministically, so the test
        # file is hermetic in isolation instead of relying on a later test's GC to absorb the leak.
        # A live HITL flow leaves two stragglers behind that ``filterwarnings=["error"]`` turns into
        # errors once they are finalized lazily during whatever test runs next:
        #   * ZenML's SQLAlchemy SQLite connection pool → unclosed-socket ``ResourceWarning`` —
        #     closed by disposing the engine.
        #   * the asyncio event loop pydantic-ai's ``run_sync`` leaves set as the main thread's
        #     *current* loop (a ``_UnixSelectorEventLoop`` owning a self-pipe socketpair). It is reused
        #     while it stays current, but the next test that calls ``asyncio.run`` resets the current
        #     loop and orphans it → its ``__del__`` + socketpair trip unclosed-loop / unclosed-socket
        #     warnings mid-suite. Closing it here and clearing the current loop releases the socketpair
        #     now; the next flow simply builds a fresh loop.
        # Resetting the ZenML singletons alone drops the references but closes neither, so we close
        # both explicitly, then ``gc.collect()`` finalizes the now-clean objects within this scope.
        _dispose_kitaru_engine()
        Client._reset_instance()
        GlobalConfiguration._reset_instance()
        _close_idle_event_loop()
        gc.collect()


def _active_store_url() -> str:
    """Return the URL of the ZenML store the next secret/execution op would hit (the live config)."""
    from zenml.config.global_config import GlobalConfiguration

    return str(GlobalConfiguration().store_configuration.url)


def _assert_store_isolated_under(tmp_path: Path) -> None:
    """Fail loudly if the active ZenML store is not the per-test ``tmp_path`` one (task 065 guard).

    Called right before a secret op, this is the tripwire for an isolation regression: if the autouse
    :func:`isolated_kitaru_store` re-pin ever stops taking effect (a sibling non-isolated test
    poisoning the global config, or the autouse fixture silently not applying), the active store URL
    is no longer the ``sqlite:///<tmp_path>/...`` one and the test errors here — instead of silently
    writing to a developer's real ZenML store/server (which on this machine is a live server).
    """
    url = _active_store_url()
    assert str(tmp_path) in url, (
        f"Kitaru store isolation regressed: the active ZenML store {url!r} is not under the per-test "
        f"tmp_path {str(tmp_path)!r}. A secret/execution op would hit a REAL ZenML store — refusing."
    )


def _delete_secret_best_effort(name: str) -> None:
    """Delete the test's Environment Bucket if it exists, best-effort (defense-in-depth teardown).

    Runs while the test's (isolated) store is still active, before the autouse fixture resets the
    singletons. In the normal case the bucket lives in ``tmp_path`` and vanishes with it anyway.
    """
    try:
        from kitaru import delete_secret

        delete_secret(name)
    except Exception:
        pass


@pytest.fixture
def env_bucket_name(
    isolated_kitaru_store: Path | None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Pin ``DECODE_ENV=dev`` and yield the DERIVED Environment Bucket name (``decode-dev``, ADR-0015 §3).

    Bucket names are derived from the environment — there is no override knob — so the old
    unique-per-test secret name is no longer representable, and **isolation now rests on
    :func:`isolated_kitaru_store` alone**: every secret op in a runtime test hits that test's own
    ``tmp_path`` SQLite store, never a developer's real ZenML store. The fixture asserts exactly that
    before yielding (the task-065 tripwire), and best-effort deletes ``decode-dev`` on teardown, while
    the isolated store is still active.
    """
    from decode.config.settings import settings

    assert isolated_kitaru_store is not None  # only runtime tests use this fixture
    _assert_store_isolated_under(isolated_kitaru_store)
    monkeypatch.setattr(settings, "decode_env", "dev")
    monkeypatch.setenv("DECODE_ENV", "dev")
    name = "decode-dev"
    try:
        yield name
    finally:
        _delete_secret_best_effort(name)


def _dispose_kitaru_engine() -> None:
    """Dispose ZenML's live SQLAlchemy engine so its pooled SQLite sockets close deterministically."""
    from zenml.config.global_config import GlobalConfiguration

    store = GlobalConfiguration()._zen_store  # the live store, or None if no flow touched it
    engine = getattr(store, "_engine", None)
    if engine is not None:
        engine.dispose()


def _close_idle_event_loop() -> None:
    """Close the idle event loop ``run_sync`` left as the main thread's current loop, and clear it.

    pydantic-ai's ``get_event_loop()`` builds a loop on the first headless ``run_sync`` and leaves it
    set as the main thread's *current* loop. It is reused while it stays current, so we close it only
    here (in teardown, when no flow is mid-run) and reset the current loop to ``None`` so the leaked
    loop is not carried into the next test to be orphaned by an unrelated ``asyncio.run``. The next
    flow builds a fresh loop. ``_local._loop`` is read directly so we never *create* a loop here.
    """
    import asyncio

    policy = asyncio.get_event_loop_policy()
    loop = getattr(getattr(policy, "_local", None), "_loop", None)
    if loop is not None and not loop.is_running() and not loop.is_closed():
        loop.close()
        asyncio.set_event_loop(None)


class WaitRecorder:
    """Drives + records the durable HITL waits a flow creates, resolving them inline (task 059).

    ``answers`` is the queue of raw string values served to successive waits in the order they
    suspend the flow (e.g. ``["staging"]`` for one ``ask_user``; ``["true"]`` to approve a write).
    After the run, ``names`` / ``questions`` hold what each wait was named and asked, so a test can
    assert the wait was named deterministically (Replay reuse) and carried the model's question. A
    wait with no queued answer is left pending (the flow keeps polling) — that is how a test exercises
    the *paused* outcome.
    """

    def __init__(self) -> None:
        self.answers: list[str] = []
        self.names: list[str] = []
        self.questions: list[str | None] = []
        self._served = 0

    def resolve(self, condition: Any, poll_interval: int) -> None:
        """Resolve ``condition`` with the next queued answer (the patched interactive poll).

        The signature mirrors ZenML's ``poll_interactive_wait_condition_input(condition, poll_interval)``
        — the runner calls it with both as keywords, so the names must match.
        """
        _ = poll_interval
        from zenml.client import Client
        from zenml.enums import RunWaitConditionResolution
        from zenml.models import RunWaitConditionResolveRequest
        from zenml.utils.json_utils import parse_value_for_schema

        self.names.append(condition.name)
        self.questions.append(condition.question)
        if self._served >= len(self.answers):
            # No answer yet — leave the wait pending so the flow polls/pauses. Sleep briefly so the
            # runner's poll loop does not busy-spin while it counts down to its timeout.
            import time

            time.sleep(0.02)
            return
        raw = self.answers[self._served]
        self._served += 1
        result = (
            parse_value_for_schema(raw, condition.data_schema)
            if condition.data_schema is not None
            else None
        )
        Client().zen_store.resolve_run_wait_condition(
            run_wait_condition_id=condition.id,
            resolve_request=RunWaitConditionResolveRequest(
                resolution=RunWaitConditionResolution.CONTINUE, result=result
            ),
        )


@pytest.fixture
def inline_wait_resolver(monkeypatch: pytest.MonkeyPatch) -> WaitRecorder:
    """Resolve every durable Kitaru wait **inline on the flow thread** (task 059, ADR-0008 §3).

    A durable ``kitaru.wait()`` suspends the flow and polls a wait record until an operator resolves
    it out-of-band. Hermetically driving that offline is the hard part the task flagged:
    ``KitaruClient.input`` from a **background thread** re-initializes ZenML's per-thread stack/store
    and races SQLite; ``executions.resume`` after a timeout pause needs a *deployed* flow the local
    in-process stack does not have. The robust offline path is Kitaru's own **local interactive input
    seam**: the runner, when it deems input answerable interactively, calls
    ``poll_interactive_wait_condition_input`` between polls. We force that path on
    (``can_answer_wait_condition_interactively`` → ``True``) and replace the poll with
    :meth:`WaitRecorder.resolve`, which resolves the wait with a queued answer on the *same* thread —
    so a single-process test never blocks, threads, or pauses. This is the literal "local input path"
    the task asks the test to inject through.
    """
    from zenml.execution.pipeline.dynamic import interactive_input_utils as iiu
    from zenml.execution.pipeline.dynamic import runner as runner_mod

    recorder = WaitRecorder()
    monkeypatch.setattr(iiu, "can_answer_wait_condition_interactively", lambda orchestrator: True)
    monkeypatch.setattr(runner_mod, "poll_interactive_wait_condition_input", recorder.resolve)
    return recorder
