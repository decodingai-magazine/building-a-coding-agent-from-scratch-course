"""The Kitaru runtime capstone: the headless durable flow end to end, OFFLINE (ADR-0008, task 062).

This is the living proof for the Kitaru-runtime feature (ADR-0008, tasks 057-062) — and it doubles as
documentation, in the style of :mod:`tests.integration.test_milestone1_capstone` (swap only the model
boundary) and :mod:`tests.integration.test_lsp_capstone` (patch the seam, no subprocess). It drives
the **real** :func:`decode.agent.factory.build_agent` + the real Kitaru ``@flow`` + the real
``KitaruAgent`` adapter through scripted headless runs on a **local** Kitaru stack — *no Kitaru
server, no network, no* ``GEMINI_API_KEY`` — swapping out only two boundaries:

* the **runtime seam** (task 058's :func:`decode.runtime.flow._build_runtime_agent` /
  :func:`decode.runtime.flow._build_hitl_runtime_agent`) — patched to inject an agent built on a
  scripted :class:`~pydantic_ai.models.function.FunctionModel` instead of a real provider model;
* the **model** itself (the ``FunctionModel`` walks a canned tool/text script).

Everything else is real: the flat tool registry, the bypass / gating permission gates, the durable
``KitaruAgent`` checkpoints, the flow-scope ``kitaru.wait`` HITL bridge, the durable ``sleep`` timer,
the Credentials-Proxy ``get_secret`` round-trip, and the local ZenML store the checkpoints persist to.
The store is redirected under ``tmp_path`` (so the repo's home / ``.kitaru`` are never touched) and the
process ``cwd`` is moved there too, so any file a tool writes stays in the sandbox.

The four runtime sub-features, each asserted through the real flow:

1. **Durability (058)** — :func:`test_durability_runs_the_real_flow_to_completion`: a multi-step task
   runs to completion via ``run_agent_task.run(task)`` and returns the scripted final text (read back
   from the ``_capture_runtime_output`` artifact — ``.wait()`` no longer auto-extracts under the
   terminal sink, task 068); the durable, checkpointed execution is recorded; a fresh re-run is a *new*
   execution. It also proves the
   **real** agent loop ran (the scripted tool sequence wrote a real file inline under BYPASS) and the
   interactive ``Runner`` / ``agent/loop.py`` path was **not** used (a spy asserts neither was built).
2. **Replay (058, AC2)** — :func:`test_replay_serves_a_finished_model_checkpoint_from_cache`: a
   ``flow.replay(...)`` of a finished run serves the finished **model** checkpoints from cache — the
   model's call-count does **not** increase for the cached turn. (Verified empirically on the installed
   kitaru 0.18 local stack; see the module note below.)
3. **HITL (059)** — :func:`test_hitl_pauses_on_named_waits_and_injected_answers_drive_the_tools`: the
   scripted agent calls ``ask_user`` and then a gated ``write`` in one conversation; each pauses on a
   **named** durable wait; an injected answer / verdict becomes the tool result / lets the write run.
4. **Durable sleep (060)** — :func:`test_durable_sleep_uses_the_capped_timer`: a ``sleep`` in flow mode
   pauses on a flow-scope ``kitaru.wait`` named ``"sleep"`` with the **capped** timeout (a spy asserts
   the value; no real wall-clock wait); the interactive ``asyncio.sleep`` seam is restored on exit.
5. **Credentials proxy (061)** — :func:`test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload`:
   with the proxy enabled and a real Kitaru secret, the model is built from the **secret-sourced** key,
   and the serialized flow payload carries only the task — never the raw key.

**Module note — the replay reality, verified first (task 062 de-risk).** The grooming flagged that
task 059's local in-process stack could not drive a true deployed-flow replay. Probing the installed
SDK split the question in two:

* **Finished *model* checkpoints DO replay from cache on the local stack.** ``flow.replay(exec_id,
  from_=<a downstream checkpoint>)`` of a finished ``"calls"``-strategy run serves the upstream model
  checkpoints from the original execution's cache — the ``FunctionModel`` is *not* re-invoked
  (deterministic across runs). So AC2's "a replay serves a finished checkpoint from cache (the model is
  not re-called for it)" is **real** here, and :func:`test_replay_serves_a_finished_model_checkpoint_from_cache`
  asserts it for real (the model leg-count does not increase).
* **A *wait* answer does NOT replay from cache on the local stack.** A HITL ``ask_user`` wait is opted
  out of its per-call checkpoint precisely so its wait lands at flow scope (ADR-0008 §3), so it is never
  cached — a replay **re-creates** (re-asks) the wait rather than reusing the saved answer. AC3's "a
  replay reuses the answer without re-asking" therefore needs a *deployed* stack (deferred to step 12);
  on the local stack what is provable is the **deterministic wait name** — the key a deployed replay
  *would* reuse the answer by — and :func:`test_replay_re_asks_a_wait_on_the_local_stack` documents the
  re-ask reality with that stable name. (The AC3 wording is corrected accordingly in the task log.)

These tests are **synchronous** (the Kitaru ``@flow`` is sync — ``KitaruAgent.run_sync`` bridges the
async agent internally), so they do not run under pytest-asyncio's loop; like the task 058-061 unit
tests they boot the real ZenML stack and scope its two unrelated third-party deprecation warnings.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic import SecretStr
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

import decode.agent.factory as factory_mod
import decode.runtime.flow as flow_mod
from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.runtime import run_agent_task, run_hitl_agent_task
from decode.runtime.flow import (
    HITL_RUNTIME_AGENT_NAME,
    RUNTIME_AGENT_NAME,
    _hitl_wait_name,
    _to_hitl_durable_agent,
)
from decode.tools import sleep as sleep_module
from decode.tools.registry import register_tools

# Booting the real Kitaru/ZenML stack emits two third-party deprecation warnings unrelated to decode
# (passlib importing the stdlib ``crypt``; pydantic-ai's sync bridge touching the event loop). The
# task 058-061 runtime unit tests scope exactly these two; the strict ``filterwarnings=["error"]`` gate
# (pyproject) stays green for everything else. Whether a deployed stack can replay a wait answer is the
# step-12 question; here every wait is resolved inline on the flow thread.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]

# Is the durable runtime importable (kitaru + adapter + the local ZenML stack)? When it is — the normal
# CI case, kitaru is a hard dependency — the guarded real-local test runs; on a stripped environment
# that cannot host the runtime it SKIPS (never fails), mirroring the LSP capstone's ``ty``-guarded test.
try:  # pragma: no cover - import-time capability probe
    import kitaru as _kitaru  # noqa: F401
    import zenml.client as _zenml_client  # noqa: F401

    _LOCAL_KITARU_STACK_AVAILABLE = True
except Exception:  # pragma: no cover - only on an incompatible environment
    _LOCAL_KITARU_STACK_AVAILABLE = False

# The closing ``@checkpoint`` of the HITL flow (its function name == its ZenML step name). Replaying
# ``from_`` this terminal boundary serves every upstream model checkpoint from cache (ADR-0008 §3).
_CAPTURE_CHECKPOINT = "_capture_runtime_output"


# ================================================================================================
# Hermeticity — run the LIVE Kitaru flow offline under ``tmp_path`` and release every straggler.
# Mirrors the task-059 fix in ``tests/unit/decode/runtime/conftest.py`` verbatim: a live ``@flow``
# leaves ZenML's SQLite engine + the ``run_sync`` event loop behind, which ``filterwarnings=["error"]``
# turns into errors when they are finalized during a later test. We dispose + close + ``gc.collect``
# them inside this fixture's scope so the capstone is hermetic in isolation.
# ================================================================================================


@pytest.fixture(autouse=True)
def isolated_kitaru_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect Kitaru/ZenML's store + config under ``tmp_path`` so the flow runs offline, hermetically."""
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
        _dispose_kitaru_engine()
        Client._reset_instance()
        GlobalConfiguration._reset_instance()
        _close_idle_event_loop()
        gc.collect()


def _dispose_kitaru_engine() -> None:
    """Dispose ZenML's live SQLAlchemy engine so its pooled SQLite sockets close deterministically."""
    from zenml.config.global_config import GlobalConfiguration

    store = GlobalConfiguration()._zen_store
    engine = getattr(store, "_engine", None)
    if engine is not None:
        engine.dispose()


def _close_idle_event_loop() -> None:
    """Close the idle event loop ``run_sync`` left as the main thread's current loop, and clear it."""
    import asyncio

    policy = asyncio.get_event_loop_policy()
    loop = getattr(getattr(policy, "_local", None), "_loop", None)
    if loop is not None and not loop.is_running() and not loop.is_closed():
        loop.close()
        asyncio.set_event_loop(None)


# ================================================================================================
# The inline wait resolver — the hermetic stand-in for ``kitaru executions input`` (task 059).
# Resolves each durable Kitaru wait on the flow thread so a wait-paused run never blocks, by forcing
# Kitaru's own local interactive-input seam on. Copied from the task-059 conftest (ADR-0008 §3): a
# background ``KitaruClient.input`` thread races SQLite, and post-timeout ``resume`` needs a deployed
# flow the in-process local stack lacks, so the local interactive seam is the offline-safe path.
# ================================================================================================


class WaitRecorder:
    """Drives + records the durable HITL waits a flow creates, resolving them inline (task 059)."""

    def __init__(self) -> None:
        self.answers: list[str] = []
        self.names: list[str] = []
        self.questions: list[str | None] = []
        self._served = 0

    def resolve(self, condition: Any, poll_interval: int) -> None:
        """Resolve ``condition`` with the next queued answer (the patched interactive poll)."""
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
    """Resolve every durable Kitaru wait inline on the flow thread (task 059, ADR-0008 §3)."""
    from zenml.execution.pipeline.dynamic import interactive_input_utils as iiu
    from zenml.execution.pipeline.dynamic import runner as runner_mod

    recorder = WaitRecorder()
    monkeypatch.setattr(iiu, "can_answer_wait_condition_interactively", lambda orchestrator: True)
    monkeypatch.setattr(runner_mod, "poll_interactive_wait_condition_input", recorder.resolve)
    return recorder


# ================================================================================================
# Scripted agents — a real decode agent (full tool registry) on a ``FunctionModel``. A shared
# ``counter`` lets a test prove a replay served a turn from cache (the model leg-count does not move).
# ================================================================================================


def _real_agent(model_fn: Any, *, name: str) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """Build the real decode agent (all tools registered) on a scripted ``FunctionModel``."""
    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name=name,
    )
    register_tools(agent)
    return agent


def _scripted_agent(
    responses: list[ModelResponse], counter: dict[str, int], *, name: str = RUNTIME_AGENT_NAME
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """An agent that replays ``responses`` one per model leg, counting legs in the shared ``counter``."""

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        index = min(counter["legs"], len(responses) - 1)
        counter["legs"] += 1
        return responses[index]

    return _real_agent(model_fn, name=name)


def _echo_agent(
    first_call: ModelResponse, counter: dict[str, int], *, name: str = HITL_RUNTIME_AGENT_NAME
) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """An agent that fires ``first_call`` then **echoes** the tool's result as text (proves round-trip)."""

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        counter["legs"] += 1
        last = messages[-1]
        if isinstance(last, ModelRequest):
            for part in last.parts:
                if isinstance(part, ToolReturnPart):
                    return ModelResponse(parts=[TextPart(content=f"tool said: {part.content}")])
        return first_call

    return _real_agent(model_fn, name=name)


def _tool_return_count(messages: list[Any]) -> int:
    """How many tool results the conversation has fed back so far (drives the multi-tool script)."""
    return sum(
        isinstance(part, ToolReturnPart)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _steps(exec_id: str) -> list[str]:
    """The persisted checkpoint (step) names of a recorded execution, via the real ZenML store."""
    from zenml.client import Client

    return list(Client().get_pipeline_run(exec_id).steps)


# ================================================================================================
# 1. Durability (058) — a multi-step task runs to completion through the REAL flow + agent loop.
# ================================================================================================


def test_durability_runs_the_real_flow_to_completion(monkeypatch, mocker, tmp_path):
    """A multi-step bypass run completes via the real flow, records a checkpoint, and a re-run is new.

    Proves the durability slice end to end: the real ``@flow`` drives the real ``KitaruAgent`` over a
    scripted three-leg conversation (read a seeded file inline → write a new file inline → final text),
    returns the scripted text, and persists a finished, checkpointed execution. A fresh re-run gets a
    *new* execution id (not a replay). It also proves the **real** agent loop ran — the scripted
    ``write`` actually landed a file on disk under BYPASS — and that the interactive ``Runner`` /
    ``AgentTurnHandler`` path was never touched (a spy asserts neither class was constructed).
    """
    (tmp_path / "spec.md").write_text("ship the runtime", encoding="utf-8")
    counter = {"legs": 0}
    responses = [
        ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "spec.md"})]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="write", args={"path": "done.txt", "content": "shipped"})]
        ),
        ModelResponse(parts=[TextPart(content="read the spec and wrote done.txt")]),
    ]
    durable = KitaruAgent(
        _scripted_agent(responses, counter), name=RUNTIME_AGENT_NAME, checkpoint_strategy="turn"
    )
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    # The interactive path must NOT be used by the headless flow: spy on both its entry classes.
    runner_spy = mocker.patch("decode.harness.runner.Runner")
    handler_spy = mocker.patch("decode.agent.loop.AgentTurnHandler")

    handle = run_agent_task.run(task="read the spec then record the work")

    # The real flow returned the scripted final text, read back from the ``_capture_runtime_output``
    # artifact — under that terminal sink ``.wait()`` no longer auto-extracts a value (task 068).
    assert flow_mod._load_runtime_output(handle.exec_id) == "read the spec and wrote done.txt"
    # The real agent loop ran the scripted tools INLINE under BYPASS: the write actually hit disk, and
    # the model consumed >1 leg (a stub returning canned text would not write the file or tool-loop).
    assert (tmp_path / "done.txt").read_text(encoding="utf-8") == "shipped"
    assert counter["legs"] >= 3, "the real agent loop must have driven all three scripted legs"
    # The interactive Runner / loop were never built — the headless flow bypasses them entirely.
    runner_spy.assert_not_called()
    handler_spy.assert_not_called()

    # A finished, checkpointed execution was persisted — the durable record a crash-resume replays.
    assert handle.status.is_finished and handle.status.is_successful
    assert isinstance(handle.exec_id, str) and handle.exec_id
    assert "decode_runtime" in set(_steps(handle.exec_id)), "the per-turn checkpoint was persisted"

    # A fresh re-run is a NEW execution (a new ``run`` is not a replay of the prior one).
    rerun = run_agent_task.run(task="read the spec then record the work")
    assert flow_mod._load_runtime_output(rerun.exec_id) == "read the spec and wrote done.txt"
    assert rerun.exec_id != handle.exec_id


# ================================================================================================
# 2. Replay (058, AC2) — a replay serves a FINISHED MODEL checkpoint from cache (model not re-called).
# ================================================================================================


def test_replay_serves_a_finished_model_checkpoint_from_cache(monkeypatch):
    """A ``flow.replay`` of a finished run serves its model checkpoints from cache — model not re-called.

    The real AC2 replay proof, verified against the installed kitaru 0.18 local stack. A HITL flow runs
    a read-only tool to completion under ``checkpoint_strategy="calls"`` (so each model/tool call is its
    own checkpoint, closed by ``_capture_runtime_output``). Replaying ``from_`` that terminal checkpoint
    re-runs only the closing step and serves every upstream **model** checkpoint from the original
    execution's cache: the shared model leg-counter does **not** move, proving the ``FunctionModel`` was
    not re-invoked for the cached turn. (A *wait* answer is a different story — see
    :func:`test_replay_re_asks_a_wait_on_the_local_stack`.)
    """
    Path("note.txt").write_text("hello from note", encoding="utf-8")  # cwd is the isolated tmp_path
    counter = {"legs": 0}
    read_call = ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "note.txt"})])
    monkeypatch.setattr(
        flow_mod,
        "_build_hitl_runtime_agent",
        lambda model=None: _to_hitl_durable_agent(_echo_agent(read_call, counter)),
    )

    result = run_hitl_agent_task("read the note")
    assert result.paused is False
    assert result.output is not None and "hello from note" in result.output
    legs_after_run = counter["legs"]
    assert legs_after_run >= 2, "the initial run must have driven the real model legs"
    steps = _steps(result.exec_id)
    assert _CAPTURE_CHECKPOINT in steps, "the closing capture checkpoint must be persisted"

    # Replay from the terminal capture checkpoint: the body re-runs but every upstream model checkpoint
    # is served from cache. On the local stack ``.replay(...)`` runs in-process and returns once
    # finished, so the leg-count is observable immediately (no ``.wait()`` — it would trip the
    # multiple-terminal-steps extraction guard the HITL flow sidesteps via the output artifact).
    replay = flow_mod.run_agent_task_hitl.replay(result.exec_id, from_=_CAPTURE_CHECKPOINT)

    assert replay.status.is_finished and replay.status.is_successful
    assert counter["legs"] == legs_after_run, (
        "a replay must serve the finished model checkpoints from cache — the model must NOT be re-called"
    )


# ================================================================================================
# 2b. Model-swap Replay (ADR-0010 §5, task 070) — the INVERSE of the cache proof above: a replay
# with a swapped Model Override RE-EXECUTES the anchored turn with the NEW model.
# ================================================================================================


def test_model_swap_replay_re_executes_downstream_turns(monkeypatch, tmp_path):
    """A model-swap ``replay`` re-executes the anchored turn under the NEW model (the what-if bite).

    The real AC1/AC2 proof for ``decode replay`` (ADR-0010 §5), and the exact **inverse** of
    :func:`test_replay_serves_a_finished_model_checkpoint_from_cache`: that test anchors at the terminal
    so the model is served from cache (leg-counter frozen); this one anchors **at the first model call**
    and swaps the model, so the swapped agent's model IS re-invoked downstream of ``--from``.

    The seam returns **two different scripted agents keyed on ``model``** — a ``model-baseline`` agent and
    a ``model-swapped`` agent, each with its own leg-counter — so which model re-executed is unambiguous.
    After the baseline bypass run, replaying ``from_`` the first ``decode_runtime_model_request`` with
    ``model="model-swapped"`` moves the **swapped** counter (the swapped model re-ran the anchored turn)
    while the **baseline** counter stays frozen (the original model was not re-invoked), and the Fork gets
    a new ``exec_id``.

    Honesty note (verified on kitaru 0.18): under ``"calls"`` the per-call checkpoints are DAG-independent
    siblings and the terminal ``_capture_runtime_output`` sink has no upstream edge, so anchoring at one
    model call re-executes only that call — the cached terminal artifact still serves the *baseline* text.
    So the faithful proof that the swap re-executed is the **leg-counter**, not the returned text (the
    text-swap-through-the-sink path is structurally a deployed-stack concern). That is exactly the option
    the task names ("its leg-counter moves"), and the clean mirror of the cache test's frozen counter.
    """
    (tmp_path / "note.txt").write_text("hello from note", encoding="utf-8")
    base_counter = {"legs": 0}
    swap_counter = {"legs": 0}
    baseline_script = [
        ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "note.txt"})]),
        ModelResponse(parts=[TextPart(content="baseline: read the note")]),
    ]
    swapped_script = [
        ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "note.txt"})]),
        ModelResponse(parts=[TextPart(content="swapped: read the note")]),
    ]
    baseline = KitaruAgent(
        _scripted_agent(baseline_script, base_counter),
        name=RUNTIME_AGENT_NAME,
        checkpoint_strategy="calls",
    )
    swapped = KitaruAgent(
        _scripted_agent(swapped_script, swap_counter),
        name=RUNTIME_AGENT_NAME,
        checkpoint_strategy="calls",
    )

    def seam(model: str | None = None) -> KitaruAgent:
        # Kitaru forwards the Model Override flow input to the seam on the initial run AND on replay, so
        # the swapped agent is selected only when the replay passes ``model="model-swapped"``.
        return swapped if model == "model-swapped" else baseline

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", seam)

    # 1. Baseline run under ``model-baseline`` — the recorded run we will fork.
    handle = run_agent_task.run(task="read the note", model="model-baseline")
    assert flow_mod._load_runtime_output(handle.exec_id) == "baseline: read the note"
    baseline_legs_after_run = base_counter["legs"]
    assert baseline_legs_after_run >= 2, "the baseline agent drove the real model legs"
    assert swap_counter["legs"] == 0, "the swapped agent has not run yet"

    # 2. Pick the replay anchor EXPLICITLY: the first model-request checkpoint (at/before the first model
    # call), so the swap re-executes that turn. ``decode replay`` requires the operator to pass this.
    steps = _steps(handle.exec_id)
    anchor = min(s for s in steps if s.startswith("decode_runtime_model_request"))

    # 3. Replay from the anchor with the SWAPPED model — the what-if fork.
    replay = run_agent_task.replay(handle.exec_id, from_=anchor, model="model-swapped")

    assert replay.status.is_finished and replay.status.is_successful
    # 4. The SWAPPED model re-executed the anchored turn; the BASELINE model was NOT re-invoked — the real
    # proof the swap bit downstream of ``--from`` (the inverse of the cache test's frozen counter).
    assert swap_counter["legs"] >= 1, "the swapped model re-executed the turn downstream of --from"
    assert base_counter["legs"] == baseline_legs_after_run, (
        "the baseline model must NOT be re-invoked on the swapped replay — the swap drove it"
    )
    # 5. A new, linked execution (the Fork), not an in-place mutation of the original.
    assert replay.exec_id != handle.exec_id


# ================================================================================================
# 3. HITL (059) — ask_user + a gated write pause on NAMED durable waits; injected answers drive them.
# ================================================================================================


def test_hitl_pauses_on_named_waits_and_injected_answers_drive_the_tools(
    monkeypatch, inline_wait_resolver, tmp_path
):
    """One conversation: ``ask_user`` then a gated ``write`` each pause on a named wait; answers drive them.

    The headless HITL slice end to end. The scripted agent calls ``ask_user`` (resolved out-of-band with
    ``"staging"``), then a gated ``write`` (approved out-of-band with ``"true"``), then final text. The
    flow pauses on **two** named durable waits — the ``ask_user`` wait named deterministically from the
    question (the deployed-replay reuse key), and the adapter's native ``write`` approval wait — and the
    injected answer / verdict become the tool result / let the write actually land on disk.
    """
    question = "which environment should I target?"
    inline_wait_resolver.answers = ["staging", "true"]  # ask_user answer, then the write verdict

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        returns = _tool_return_count(messages)
        if returns == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="ask_user", args={"question": question})]
            )
        if returns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name="write", args={"path": "deploy.txt", "content": "go"})
                ]
            )
        return ModelResponse(parts=[TextPart(content="deployed to staging and wrote deploy.txt")])

    monkeypatch.setattr(
        flow_mod,
        "_build_hitl_runtime_agent",
        lambda model=None: _to_hitl_durable_agent(
            _real_agent(model_fn, name=HITL_RUNTIME_AGENT_NAME)
        ),
    )

    result = run_hitl_agent_task("ask which environment, then deploy")

    assert result.paused is False
    assert result.output == "deployed to staging and wrote deploy.txt"
    # The approved gated write actually ran inline-after-approval — the file landed with the right bytes.
    assert (tmp_path / "deploy.txt").read_text(encoding="utf-8") == "go"
    # Exactly two waits paused the flow, in order: the ask_user question wait (named deterministically
    # from the question — the key a deployed replay would reuse the answer by), then the native write
    # approval wait (named by the adapter from the pydantic-ai tool-call id).
    assert len(inline_wait_resolver.names) == 2
    assert inline_wait_resolver.names[0] == _hitl_wait_name(question)
    assert inline_wait_resolver.questions[0] == question
    assert inline_wait_resolver.names[1].startswith("approve_write")


# ================================================================================================
# 4. HITL replay reality (AC3, corrected) — the wait is RE-ASKED on the local stack, not reused.
# ================================================================================================


def test_replay_re_asks_a_wait_on_the_local_stack(monkeypatch):
    """A replay RE-CREATES the ``ask_user`` wait on the local stack — the answer is not reused from cache.

    The honest AC3 finding (ADR-0008 §3, verified first for task 062). A HITL ``ask_user`` wait is opted
    out of its per-call checkpoint precisely so it lands at flow scope, so it is **never cached** — and a
    replay of a finished run therefore **re-asks** it rather than reusing the saved answer. What *is*
    provable on the local stack, and what a deployed replay would key answer-reuse on, is the
    **deterministic wait name**: the replay re-creates a wait under the very same
    :func:`decode.runtime.flow._hitl_wait_name`. True replay-without-re-asking needs a deployed stack
    (deferred to step 12); the model-checkpoint half of replay-from-cache is the real proof in
    :func:`test_replay_serves_a_finished_model_checkpoint_from_cache`.
    """
    question = "which environment should I target?"
    wait_name = _hitl_wait_name(question)
    ask_call = ModelResponse(
        parts=[ToolCallPart(tool_name="ask_user", args={"question": question})]
    )
    monkeypatch.setattr(
        flow_mod,
        "_build_hitl_runtime_agent",
        lambda model=None: _to_hitl_durable_agent(_echo_agent(ask_call, {"legs": 0})),
    )

    # Initial run: resolve the wait with "staging" so it completes and is recorded.
    first = WaitRecorder()
    first.answers = ["staging"]
    _install_recorder(monkeypatch, first)
    result = run_hitl_agent_task("deploy my app")
    assert result.paused is False
    assert first.names == [wait_name], (
        "the initial wait is named deterministically from the question"
    )

    # Replay from the terminal capture checkpoint with a FRESH recorder that can answer. If the answer
    # were served from cache the recorder would record nothing; instead the wait is RE-CREATED under the
    # same deterministic name (the local stack re-asks — deployed-stack reuse is deferred to step 12).
    replayed = WaitRecorder()
    replayed.answers = ["staging"]
    _install_recorder(monkeypatch, replayed)
    flow_mod.run_agent_task_hitl.replay(result.exec_id, from_=_CAPTURE_CHECKPOINT)

    assert wait_name in replayed.names, (
        "the local stack re-asks the wait on replay under the SAME deterministic name "
        "(answer-reuse-from-cache needs a deployed stack — ADR-0008 §3, step 12)"
    )


def _install_recorder(monkeypatch: pytest.MonkeyPatch, recorder: WaitRecorder) -> None:
    """Point Kitaru's local interactive-input seam at ``recorder`` (a second resolver within one test)."""
    from zenml.execution.pipeline.dynamic import interactive_input_utils as iiu
    from zenml.execution.pipeline.dynamic import runner as runner_mod

    monkeypatch.setattr(iiu, "can_answer_wait_condition_interactively", lambda orchestrator: True)
    monkeypatch.setattr(runner_mod, "poll_interactive_wait_condition_input", recorder.resolve)


# ================================================================================================
# 5. Durable sleep (060) — ``sleep`` in flow mode is a flow-scope ``kitaru.wait`` with the CAPPED timeout.
# ================================================================================================


def test_durable_sleep_uses_the_capped_timer(monkeypatch, inline_wait_resolver):
    """A ``sleep`` in the durable run pauses on a flow-scope ``kitaru.wait`` named "sleep", capped, no wall-clock.

    The durable-timer slice end to end. The scripted agent calls ``sleep(10_000)``; with
    ``settings.sleep_max_s`` clamped to ``3.0`` the durable sleeper invokes ``kitaru.wait(name="sleep",
    timeout=3)`` — a spy captures the call to assert the **capped int** timeout reached the real timer,
    and the inline resolver fires it immediately so there is **no** real wall-clock wait. The interactive
    ``asyncio.sleep`` seam is restored on flow exit (no leakage into a later in-process ``sleep``).
    """
    import kitaru

    monkeypatch.setattr(sleep_module.settings, "sleep_max_s", 3.0)
    inline_wait_resolver.answers = ["resume"]  # the timer "fires" → the flow continues

    captured: dict[str, Any] = {}
    real_wait = kitaru.wait

    def spy_wait(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("name") == sleep_module.SLEEP_TOOL_NAME:
            captured["name"] = kwargs.get("name")
            captured["timeout"] = kwargs.get("timeout")
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(kitaru, "wait", spy_wait)

    sleep_call = ModelResponse(parts=[ToolCallPart(tool_name="sleep", args={"seconds": 10_000})])
    monkeypatch.setattr(
        flow_mod,
        "_build_hitl_runtime_agent",
        lambda model=None: _to_hitl_durable_agent(_echo_agent(sleep_call, {"legs": 0})),
    )

    result = run_hitl_agent_task("back off then continue")

    assert result.paused is False
    # The clamp ran before the seam: the duration actually "slept" is the cap, fed back through the model.
    assert result.output == "tool said: Slept 3.0 s."
    # The durable timer was the real flow-scope ``kitaru.wait`` named "sleep", carrying the CAPPED int.
    assert captured == {"name": sleep_module.SLEEP_TOOL_NAME, "timeout": 3}
    assert inline_wait_resolver.names == [sleep_module.SLEEP_TOOL_NAME]
    # The seam was reset on flow exit — a later in-process ``sleep`` uses ``asyncio.sleep`` again.
    assert sleep_module._SLEEPER is sleep_module._interactive_sleep


# ================================================================================================
# 6. Credentials proxy (061) — the model key comes from a Kitaru secret; the raw key is off the payload.
# ================================================================================================

_SECRET_NAME = "decode-capstone-creds"
_KITARU_RAW_KEY = "KITARU-RAW-GEMINI-KEY-capstone-7f3a"
_SETTINGS_RAW_KEY = "SETTINGS-RAW-GEMINI-KEY-must-not-be-used"


def test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload(monkeypatch):
    """The proxy builds the model from a Kitaru secret; the serialized flow payload never carries the raw key.

    The Credentials-Proxy slice on the real local stack (offline, no server). A real Kitaru secret is
    created with :func:`kitaru.create_secret`; with the proxy enabled the patched seam first calls the
    **real** ``build_agent(flow_mode=True)`` (so the proxy genuinely resolves the key inside the flow
    body — asserted to be the *Kitaru* key, not the settings sentinel), then runs the turn on a scripted
    offline model. The persisted execution's input parameters carry only the task; the raw key (Kitaru's
    or settings') appears nowhere in the serialized flow config — the "secrets never reach the … payload"
    invariant (AGENTS.md), proven on the real store.
    """
    from kitaru import create_secret

    create_secret(_SECRET_NAME, {"GEMINI_API_KEY": _KITARU_RAW_KEY}, private=True)
    monkeypatch.setattr(factory_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(factory_mod.settings, "gemini_model", "gemini-2.5-flash")
    monkeypatch.setattr(factory_mod.settings, "gemini_api_key", SecretStr(_SETTINGS_RAW_KEY))
    monkeypatch.setattr(factory_mod.settings, "runtime_credentials_proxy_enabled", True)
    monkeypatch.setattr(factory_mod.settings, "runtime_secret_name", _SECRET_NAME)

    resolved: dict[str, str] = {}
    counter = {"legs": 0}

    def seam(model: str | None = None) -> KitaruAgent:
        # Build the REAL agent so the proxy resolves the key inside the flow body; capture the key the
        # model carries to prove it came from Kitaru, then run the turn on a scripted offline model.
        real_agent = build_agent(flow_mode=True)
        resolved["api_key"] = real_agent.model._provider.client._api_client.api_key
        scripted = _scripted_agent([ModelResponse(parts=[TextPart(content="done")])], counter)
        return KitaruAgent(scripted, name=RUNTIME_AGENT_NAME, checkpoint_strategy="turn")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", seam)

    handle = run_agent_task.run(task="summarize the repository")
    assert flow_mod._load_runtime_output(handle.exec_id) == "done"

    # The model was built from the SECRET-sourced key, never the settings sentinel.
    assert resolved["api_key"] == _KITARU_RAW_KEY
    assert resolved["api_key"] != _SETTINGS_RAW_KEY

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    # The persisted flow arguments are the task string + the Model Override input (``model=None``
    # here) — no credential rides in the payload/logs; a model id is not a secret (ADR-0010 §2).
    assert set(run.config.parameters) == {"task", "model"}
    assert run.config.parameters["task"] == "summarize the repository"
    assert _KITARU_RAW_KEY not in run.config.model_dump_json()
    assert _SETTINGS_RAW_KEY not in run.config.model_dump_json()


# ================================================================================================
# Optional guarded real-local test — the real wire on a real local stack; SKIPS when unavailable.
# ================================================================================================


@pytest.mark.skipif(
    not _LOCAL_KITARU_STACK_AVAILABLE,
    reason="the local Kitaru stack (kitaru + zenml) is not available in this environment",
)
def test_real_local_stack_wire(monkeypatch, tmp_path):
    """Run the REAL bypass flow on a real local Kitaru stack and assert the durable record — else SKIP.

    The graceful-degradation proof (User Stories 2-3): when the runtime is installed (kitaru + the local
    ZenML stack — the normal CI case), the real ``@flow`` round-trips and persists a finished,
    checkpointed execution on the actual local stack; when an environment cannot host it, this SKIPS
    rather than fails. The always-run hermetic tests above are the primary proof — they already exercise
    the real Kitaru ``@flow`` + adapter on the (isolated) local stack — so this is a thin, explicitly
    skippable smoke that mirrors the LSP capstone's ``ty``-guarded real-wire test.
    """
    (tmp_path / "input.txt").write_text("real wire", encoding="utf-8")
    counter = {"legs": 0}
    responses = [
        ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "input.txt"})]),
        ModelResponse(parts=[TextPart(content="read the real input")]),
    ]
    durable = KitaruAgent(
        _scripted_agent(responses, counter), name=RUNTIME_AGENT_NAME, checkpoint_strategy="turn"
    )
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="read the real input")

    assert flow_mod._load_runtime_output(handle.exec_id) == "read the real input"
    assert handle.status.is_finished and handle.status.is_successful
    assert "decode_runtime" in set(_steps(handle.exec_id))
    assert counter["legs"] >= 2, (
        "the real agent loop drove the scripted legs against the real stack"
    )
