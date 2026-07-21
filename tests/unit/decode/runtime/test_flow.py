"""The Headless Runtime flow: a real ``@flow`` + ``KitaruAgent`` round-trip, offline (ADR-0008).

These drive the **actual** Kitaru Durable Flow on the local stack — no server, no network — and
swap only the model boundary (a scripted ``FunctionModel`` agent injected through the
``_build_runtime_agent`` seam). They prove the de-risk the task called for: the
async-pydantic-ai-agent ⇄ sync-``run_sync`` bridge works, a gated tool runs **inline** under
``bypass`` (no ``ApprovalRequired`` → no Kitaru wait → no crash), and a finished turn replays from
the checkpoint cache on a re-run.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from support.runtime_agents import make_scripted_agent

import decode.runtime.flow as flow_mod
from decode.agent import context_window
from decode.config.settings import Settings
from decode.runtime import run_agent_task

# Running the real flow boots the Kitaru/ZenML stack, which emits two third-party deprecation
# warnings unrelated to decode (``filterwarnings=["error"]`` would otherwise fail the run): passlib
# importing the stdlib ``crypt`` module, and pydantic-ai's sync bridge touching the event loop. We
# scope the ignores to these runtime tests so the rest of the suite stays strict.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


def _durable(responses, *, strategy="calls"):
    """Wrap a scripted decode agent in a ``KitaruAgent`` for the ``_build_runtime_agent`` seam.

    Defaults to ``"calls"`` — the settings default (ADR-0010 §3), the granularity a real ``decode run``
    records — so the round-trip tests exercise the default path. Pass ``strategy="turn"`` for the coarse
    opt-out. Either way the CLI reads output back from the ``_capture_runtime_output`` artifact (the flow
    uses that sink uniformly).
    """
    agent, counter = make_scripted_agent(responses)
    return KitaruAgent(agent, name="decode-runtime", checkpoint_strategy=strategy), counter


def test_flow_round_trips_a_task_and_returns_the_agents_text(monkeypatch):
    """A bare text turn round-trips; the final text is read from the ``_capture_runtime_output`` artifact.

    Under the default ``"calls"`` strategy the run ends in terminal per-call checkpoints, so ``.wait()``
    cannot auto-extract a value (``_MultipleTerminalStepsOutputError``, task 068). The flow saves its
    final text via the terminal sink and :func:`_load_runtime_output` reads it back by name — what the
    ``decode run`` CLI does. (The sink is used uniformly; the ``"turn"`` opt-out has one terminal step.)
    """
    durable, _counter = _durable([ModelResponse(parts=[TextPart(content="all done")])])
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="say all done")

    assert flow_mod._load_runtime_output(handle.exec_id) == "all done"


def test_flow_runs_a_gated_tool_inline_under_bypass(monkeypatch, tmp_path):
    """A gated ``write`` runs INLINE under the headless bypass gate — no wait, no crash, file written.

    This is the Fork-2 resolution (ADR-0008 §2): ``run_sync`` does not use decode's loop, so a
    deferred ``ApprovalRequired`` would become a Kitaru wait and crash an unattended run. Under
    ``bypass`` the tool runs directly, so the file lands on disk and the turn completes with text.
    """
    durable, _counter = _durable(
        [
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "hi"})]
            ),
            ModelResponse(parts=[TextPart(content="wrote out.txt")]),
        ]
    )
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="write out.txt")

    assert flow_mod._load_runtime_output(handle.exec_id) == "wrote out.txt"
    # cwd is the isolated tmp_path (autouse fixture chdirs there); the tool actually wrote the file.
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi"


def test_flow_records_per_call_checkpoints_not_a_single_turn_step(monkeypatch, tmp_path):
    """The default ``"calls"`` strategy persists PER-CALL checkpoints — the record a fine Replay anchors on.

    Under ``"calls"`` (the default; ADR-0010 §3 — pinned explicitly below so this granularity assertion
    is independent of the settings default) each model/tool call is its own checkpoint, closed by the
    terminal ``_capture_runtime_output`` sink — NOT the coarse single ``decode_runtime`` turn step. A
    two-leg script (read a seeded file → final text) makes that granularity visible: the persisted step
    set carries a per-call ``*_model_request`` + the ``read_tool`` checkpoint + the capture sink, and no
    ``decode_runtime`` turn step. That fine-grained record is what lets a Replay anchor before a specific
    model call (User Story 1) — the payoff of the default.
    """
    (tmp_path / "spec.md").write_text("ship it", encoding="utf-8")
    durable, _counter = _durable(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "spec.md"})]),
            ModelResponse(parts=[TextPart(content="done")]),
        ],
        strategy="calls",
    )
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="record me")

    assert flow_mod._load_runtime_output(handle.exec_id) == "done"
    assert handle.status.is_finished and handle.status.is_successful
    assert isinstance(handle.exec_id, str) and handle.exec_id

    from zenml.client import Client

    run = Client().get_pipeline_run(handle.exec_id)
    assert run.status.is_successful
    steps = set(run.steps)
    # Per-call granularity, not the pre-068 single ``decode_runtime`` turn checkpoint.
    assert "decode_runtime" not in steps
    assert "_capture_runtime_output" in steps  # the terminal output sink
    assert "read_tool" in steps  # the tool call got its own checkpoint
    assert any(s.startswith("decode_runtime_model_request") for s in steps)  # per-model-call


def test_flow_round_trips_a_multi_tool_task_under_the_calls_strategy(monkeypatch, tmp_path):
    """A REAL bypass run round-trips a MULTI-TOOL task with ``"calls"`` sourced from settings (AC3).

    Read a seeded file → write a new file → final text: three model legs and two tool calls, so the
    ``"calls"`` strategy records several terminal per-call checkpoints — precisely the shape that breaks
    ``.wait()`` (``_MultipleTerminalStepsOutputError``, task 068). The strategy is not hardcoded here:
    the FACTORY is patched (not the seam), so the **real** :func:`_build_runtime_agent` runs and reads
    ``settings.runtime_checkpoint_strategy`` (``"calls"``) when it wraps the scripted agent. Proving the
    flow still returns the correct final text — read from the ``_capture_runtime_output`` artifact — is
    the core de-risk: output extraction survives ``"calls"`` end to end.
    """
    (tmp_path / "spec.md").write_text("ship the runtime", encoding="utf-8")
    monkeypatch.setattr(flow_mod.settings, "runtime_checkpoint_strategy", "calls")
    agent, counter = make_scripted_agent(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": "spec.md"})]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="write", args={"path": "out.txt", "content": "go"})]
            ),
            ModelResponse(parts=[TextPart(content="read the spec and wrote out.txt")]),
        ]
    )
    # Patch the factory so the real seam wraps this scripted agent under the settings strategy.
    monkeypatch.setattr(flow_mod, "build_agent", lambda flow_mode=True, model=None: agent)

    handle = run_agent_task.run(task="read the spec then write out.txt")

    assert flow_mod._load_runtime_output(handle.exec_id) == "read the spec and wrote out.txt"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "go"
    assert counter["legs"] >= 3  # the real agent loop drove all three scripted legs


def test_build_runtime_agent_wraps_build_agent_in_a_named_calls_kitaru_agent(monkeypatch):
    """The seam wraps ``build_agent()``'s Agent in a ``KitaruAgent`` with the stable name + ``"calls"``.

    ``checkpoint_strategy`` comes from ``settings.runtime_checkpoint_strategy`` — pinned here to
    ``"calls"`` (also the default) so the assertion proves the real seam propagates the *setting* rather
    than a hardcoded constant, independent of the default. Pinning it also keeps the test hermetic.
    """
    from pydantic import SecretStr

    import decode.agent.factory as factory_mod

    # build_agent() constructs the gemini model; seed a dummy key so construction is offline.
    monkeypatch.setattr(factory_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(factory_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(flow_mod.settings, "runtime_checkpoint_strategy", "calls")

    durable = flow_mod._build_runtime_agent()

    assert isinstance(durable, KitaruAgent)
    assert durable.name == flow_mod.RUNTIME_AGENT_NAME == "decode-runtime"
    assert (
        durable.checkpoint_strategy == "calls"
    )  # reads the pinned setting (here "calls", also the default)


def _seed_gemini(monkeypatch):
    """Seed the gemini provider so ``build_agent`` constructs offline; return the settings default id."""
    from pydantic import SecretStr

    import decode.agent.factory as factory_mod

    monkeypatch.setattr(factory_mod.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(factory_mod.settings, "llm_provider", "gemini")
    monkeypatch.setattr(factory_mod.settings, "gemini_model", "gemini-2.5-flash")
    return "gemini-2.5-flash"


def test_build_runtime_agent_threads_the_model_override_to_the_inner_agent(monkeypatch):
    """The bypass seam forwards ``model=`` into ``build_agent``; the wrapped agent reports the override.

    User Story 1 (the plumbing proof): ``_build_runtime_agent(model="gemini-2.5-pro")`` builds a
    ``KitaruAgent`` whose inner pydantic-ai agent reports model id ``"gemini-2.5-pro"``, while the same
    seam called with no argument reports ``settings.gemini_model`` — proving the override is forwarded
    end to end. That is the single enabler for Kitaru model-swap Replay (ADR-0010 §2).
    """
    default_id = _seed_gemini(monkeypatch)

    overridden = flow_mod._build_runtime_agent(model="gemini-2.5-pro")
    default = flow_mod._build_runtime_agent()

    assert isinstance(overridden, KitaruAgent)
    assert overridden.model.model_name == "gemini-2.5-pro"  # the override reached the inner model
    assert default.model.model_name == default_id  # no argument → the settings default


def test_build_hitl_runtime_agent_threads_the_model_override_to_the_inner_agent(monkeypatch):
    default_id = _seed_gemini(monkeypatch)

    overridden = flow_mod._build_hitl_runtime_agent(model="gemini-2.5-pro")
    default = flow_mod._build_hitl_runtime_agent()

    assert isinstance(overridden, KitaruAgent)
    assert overridden.model.model_name == "gemini-2.5-pro"
    assert default.model.model_name == default_id


# Harness-Home split + headless tool scope (ADR-0012 §6)


def test_build_headless_deps_defaults_cwd_to_harness_home():
    deps = flow_mod._build_headless_deps()

    assert deps.cwd == Path.cwd()
    assert deps.harness_home == Path.cwd()


def test_build_headless_deps_splits_the_workspace_from_harness_home(tmp_path):
    workspace = tmp_path / "ws"
    deps = flow_mod._build_headless_deps(workspace)

    assert deps.cwd == workspace  # file/search tools + bash operate here
    assert deps.harness_home == Path.cwd()  # harness artifacts (memory, skills) anchor here


# Compaction window resolved for the run's ACTUAL model, --model included (task 123)


def test_headless_deps_resolve_the_window_of_the_overridden_model(mocker):
    """``decode run --model <id>`` compacts against <id>'s window, not the configured model's.

    Asserts the resolved VALUE, not merely that the seam was called: the whole bug was a plausible
    number that belonged to a different model.
    """
    # Offline: the table decides, which keeps the assertion on two known, differing windows.
    mocker.patch.object(context_window.httpx, "get", side_effect=httpx.ConnectError("offline"))
    context_window.reset_probe_cache()
    mocker.patch.object(
        context_window,
        "settings",
        Settings(_env_file=None, llm_provider="gemini", gemini_model="gemini-3.5-flash"),
    )

    configured = flow_mod._build_headless_deps()
    overridden = flow_mod._build_headless_deps(model="Qwen/Qwen3.6-35B-A3B-FP8")

    assert configured.context_window_tokens == 1_048_576  # the configured Gemini model
    assert overridden.context_window_tokens == 262_144  # the --model override wins
    context_window.reset_probe_cache()


def test_hitl_deps_resolve_the_window_of_the_overridden_model(mocker):
    """The HITL flow threads ``--model`` on the same terms as the bypass flow."""
    mocker.patch.object(context_window.httpx, "get", side_effect=httpx.ConnectError("offline"))
    context_window.reset_probe_cache()
    mocker.patch.object(
        context_window,
        "settings",
        Settings(_env_file=None, llm_provider="gemini", gemini_model="gemini-3.5-flash"),
    )

    deps = flow_mod._build_hitl_deps(model="Qwen/Qwen3.6-35B-A3B-FP8")

    assert deps.context_window_tokens == 262_144
    context_window.reset_probe_cache()


# the hand-back runs INSIDE the flow, after the executor reap (ADR-0012 §8)


def test_flow_ships_the_workspace_after_reaping_the_executor(monkeypatch, tmp_path):
    """The reap sweeps the sandbox filesystem into the Workspace, so the ship must follow it.

    Ordering is the whole point: ship first and a modal run would push the Workspace as it stood
    BEFORE the export sweep — i.e. without the agent's work.
    """
    from decode.sandbox.handback import ShipResult

    order: list[str] = []

    def _ship(home, *, repo, session_id):
        order.append("ship")
        return ShipResult(branch="decode/abc", pushed=True, message="handed it back.")

    durable, _counter = _durable([ModelResponse(parts=[TextPart(content="done")])])
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "modal")
    monkeypatch.setattr(flow_mod, "_reap_runtime_executor", lambda: order.append("reap"))
    monkeypatch.setattr(flow_mod, "_prepare_headless_tool_scope", lambda repo, local: tmp_path)
    monkeypatch.setattr("decode.sandbox.handback.ship_workspace", _ship)

    handle = run_agent_task.run(task="do it", repo="https://example.com/repo.git")

    assert flow_mod._load_runtime_output(handle.exec_id) == "done"
    assert order == ["reap", "ship"]


def test_ship_headless_workspace_ships_under_the_execution_id(monkeypatch, mocker):
    """The Session Branch is keyed on the flow's OWN execution id — the same id the cli prints."""
    from decode.sandbox.handback import ShipResult

    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "modal")
    monkeypatch.setattr(flow_mod, "current_execution_id", lambda: "exec-abc")
    ship = mocker.patch(
        "decode.sandbox.handback.ship_workspace",
        return_value=ShipResult(branch="decode/exec-abc", pushed=True, message="handed it back."),
    )

    flow_mod._ship_headless_workspace("/src")

    ship.assert_called_once_with(Path.cwd(), repo="/src", session_id="exec-abc")


@pytest.mark.parametrize(
    ("repo", "sandbox_mode"),
    [
        (None, "modal"),  # nothing was cloned, so there is nothing to hand back
        (
            "/src",
            "none",
        ),  # the tool scope IS the launch cwd — shipping it would push the user's repo
    ],
)
def test_ship_headless_workspace_is_a_no_op_without_a_workspace(
    monkeypatch, mocker, repo, sandbox_mode
):
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", sandbox_mode)
    ship = mocker.patch("decode.sandbox.handback.ship_workspace")

    flow_mod._ship_headless_workspace(repo)

    ship.assert_not_called()


def test_ship_headless_workspace_swallows_a_hand_back_failure(monkeypatch, mocker):
    """A completed run still returns its answer when the hand-back blows up (best-effort, §8)."""
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "modal")
    mocker.patch("decode.sandbox.handback.ship_workspace", side_effect=RuntimeError("boom"))

    flow_mod._ship_headless_workspace("/src")  # must not raise


def test_prepare_headless_tool_scope_is_the_launch_cwd_in_none_mode(monkeypatch):
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "none")

    assert flow_mod._prepare_headless_tool_scope() == Path.cwd()


def test_prepare_headless_tool_scope_prepares_and_warms_the_workspace_in_a_sandbox(
    monkeypatch, tmp_path
):
    from unittest.mock import AsyncMock

    workspace = tmp_path / ".decode" / "sandbox"
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(
        "decode.sandbox.workspace.prepare_workspace",
        lambda home, *, repo=None, local=False: workspace,
    )
    warm = AsyncMock()
    monkeypatch.setattr("decode.tools.bash.warm_executor", warm)

    scope = flow_mod._prepare_headless_tool_scope()

    assert scope == workspace  # returned as deps.cwd
    warm.assert_awaited_once_with(workspace)  # eagerly started against the Workspace


def test_prepare_headless_tool_scope_threads_repo_and_local_to_the_clone(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    workspace = tmp_path / ".decode" / "sandbox"
    captured: dict[str, object] = {}

    def _fake_prepare(home, *, repo=None, local=False):
        captured["home"] = home
        captured["repo"] = repo
        captured["local"] = local
        return workspace

    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.workspace.prepare_workspace", _fake_prepare)
    monkeypatch.setattr("decode.tools.bash.warm_executor", AsyncMock())

    scope = flow_mod._prepare_headless_tool_scope("/some/repo", True)

    assert scope == workspace
    assert captured["repo"] == "/some/repo"  # the resolved --repo threaded to the clone
    assert captured["local"] is True  # ...and the --local flag too


def test_prepare_headless_tool_scope_fails_loudly_on_a_clone_failure(monkeypatch, tmp_path):
    """A bad ``--repo`` FAILS a headless run — it does not quietly work against an empty directory.

    The REPL's degrade-to-empty (ADR-0012 §3) assumes a human reading the warning. A headless run has
    no such human: a browser URL (`…/tree/main`) once let three paid agents build their work in an
    empty Workspace, which the Hand-back then refused to ship ("not a git repo"). The clone is the
    cheapest possible place to stop.
    """
    from unittest.mock import AsyncMock

    workspace = tmp_path / ".decode" / "sandbox"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(
        "decode.sandbox.workspace.prepare_workspace",
        lambda home, *, repo=None, local=False: (_ for _ in ()).throw(
            RuntimeError("git clone failed")
        ),
    )
    monkeypatch.setattr("decode.sandbox.workspace.workspace_dir", lambda home, *_a, **_k: workspace)
    warm = AsyncMock()
    monkeypatch.setattr("decode.tools.bash.warm_executor", warm)

    with pytest.raises(RuntimeError, match="nothing to work on"):
        flow_mod._prepare_headless_tool_scope("/broken/repo")

    warm.assert_not_awaited()  # no sandbox is started for a run that cannot do its job
