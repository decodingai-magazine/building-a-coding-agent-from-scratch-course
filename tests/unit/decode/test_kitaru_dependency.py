"""Smoke test for the pinned kitaru dependencies: what is gone, and what replaced it (ADR-0019).

Kitaru 0.22.2 removed the durable-execution surface decode was built on (``flow`` / ``checkpoint``
/ ``wait`` / ``save`` / ``ImageSettings``), which is why the Durable Flow is deleted rather than
ported. The replacement is the ``kitaru-pydantic-ai`` adapter: ``KitaruAgent`` wraps a built agent
and records the run. This guards both halves of that swap — the retired surface stays retired (a
re-appearing ``flow`` would mean the pin moved backwards, not that the durable runtime is welcome
back), and the adapter's constructor contract the Recording Seam is built on holds.

Signature-inspection only: no Kitaru server, no network.
"""

import importlib
import inspect


def test_kitaru_is_importable():
    module = importlib.import_module("kitaru")

    assert module is not None


def test_the_durable_execution_surface_is_gone_upstream():
    """The ImportError that made ``runtime/flow.py`` dead code, pinned as a fact (ADR-0019)."""
    module = importlib.import_module("kitaru")

    for retired in ("flow", "checkpoint", "wait", "save", "ImageSettings"):
        assert not hasattr(module, retired), (
            f"kitaru re-exposes {retired!r}: the durable runtime was deleted on the premise that "
            "upstream removed it (ADR-0019) — re-read the ADR before building on this again."
        )


def test_the_recording_adapter_is_importable():
    module = importlib.import_module("kitaru_pydantic_ai")

    assert hasattr(module, "KitaruAgent")


def test_the_client_error_family_is_shaped_the_way_the_seam_classifies_it():
    """Task 139: ``is_recording_failure`` tells a session-creation failure from an agent failure.

    It does that WITHOUT importing kitaru — by the exception class's own module — so the two facts
    it stands on are pinned here against the real package: the family lives under ``kitaru``, and an
    API error carries the ``status_code`` the 403 hint keys on. This is also the contract
    ``support.kitaru_recording.kitaru_api_error`` fakes everywhere else in the suite.
    """
    from decode.runtime.recording import is_recording_failure

    exceptions = importlib.import_module("kitaru.client.exceptions")

    for name in ("KitaruClientError", "AuthorizationError", "ValidationError"):
        assert getattr(exceptions, name).__module__.split(".")[0] == "kitaru"
    error = exceptions.AuthorizationError(403, "Task credentials are not accepted on this route")
    assert error.status_code == 403
    assert is_recording_failure(error) is True


def test_kitaru_agent_takes_the_constructor_arguments_the_recording_seam_passes():
    """The Recording Seam (ADR-0019 §3) builds ``KitaruAgent(agent, agent_id=…, session_name=…)``."""
    kitaru_agent = importlib.import_module("kitaru_pydantic_ai").KitaruAgent

    parameters = inspect.signature(kitaru_agent.__init__).parameters

    # Only the arguments decode passes are pinned — upstream may add further optional ones.
    assert parameters["agent"].default is inspect.Parameter.empty
    assert parameters["agent_id"].default is None
    assert parameters["agent_version_id"].default is None
    assert parameters["session_name"].default is None
    assert parameters["batch_size"].default == 20
