"""Fixtures for the Headless Runtime tests (ADR-0008).

The round-trip tests run the **real** Kitaru ``@flow`` + ``KitaruAgent`` on the **local** stack —
no Kitaru server, no network — by swapping only the model boundary (a ``FunctionModel`` agent
injected through the ``_build_runtime_agent`` seam) and isolating Kitaru/ZenML's on-disk store under
a per-test ``tmp_path``. This mirrors how the LSP feature patches its service seam (ADR-0007) and
keeps the suite hermetic under ``filterwarnings=["error"]``.

For the HITL tests (task 059) the :func:`inline_wait_resolver` fixture resolves each durable Kitaru
wait **inline on the same thread**, so a wait-paused flow never blocks: it is the hermetic stand-in
for an operator running ``kitaru executions input``. See that fixture for why this path (and not a
background ``KitaruClient.input`` thread) is the offline-safe one.
"""

from __future__ import annotations

import gc
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from decode.agent.deps import AgentDeps
from decode.tools.registry import register_tools


@pytest.fixture(autouse=True)
def isolated_kitaru_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect Kitaru/ZenML's store + config under ``tmp_path`` so flows run offline, hermetically.

    Kitaru's local stack persists checkpoints/metadata through ZenML, which by default writes under
    the user's home. We redirect ``Path.home`` / ``click.get_app_dir`` / ``ZENML_CONFIG_PATH`` to
    ``tmp_path``, disable analytics, and reset the ZenML global-config + client singletons before
    and after so no test ever touches real user state or makes a network call. ``cwd`` is moved into
    ``tmp_path`` too, so any tool that writes a file stays inside the sandbox.
    """
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


# A model leg: a callable producing the next ``ModelResponse`` from the message history.
ModelLeg = Callable[[list[ModelResponse], AgentInfo], ModelResponse]


def make_scripted_agent(
    responses: Sequence[ModelResponse],
    *,
    name: str = "decode-runtime",
) -> tuple[Agent[AgentDeps, str | DeferredToolRequests], dict[str, int]]:
    """Build a real decode agent (all tools registered) on a scripted ``FunctionModel``.

    ``responses`` is replayed one per model leg (the last one repeats if the agent asks for more),
    so a test scripts e.g. ``[<call write>, <final text>]``. Returns the agent plus a mutable
    ``{"legs": n}`` counter the caller can assert against (e.g. to prove a replay served the turn
    from cache without a fresh model leg). The agent carries ``deps_type=AgentDeps`` and
    ``output_type=[str, DeferredToolRequests]`` exactly like ``build_agent()``.
    """
    counter = {"legs": 0}

    def model_fn(messages: list[ModelResponse], info: AgentInfo) -> ModelResponse:
        index = min(counter["legs"], len(responses) - 1)
        counter["legs"] += 1
        return responses[index]

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name=name,
    )
    register_tools(agent)
    return agent, counter


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
