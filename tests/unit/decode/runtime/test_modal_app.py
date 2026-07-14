"""One Modal App per environment, not one per run (``runtime/modal_app.py``)."""

from __future__ import annotations

import sys

import pytest

from decode.runtime import modal_app


@pytest.mark.parametrize(
    ("env", "expected"),
    [("local", "decode-local"), ("dev", "decode-dev"), ("prod", "decode-prod")],
)
def test_orchestrator_app_name_is_one_per_environment(monkeypatch, env, expected):
    monkeypatch.setattr(modal_app.settings, "decode_env", env)

    assert modal_app.orchestrator_app_name() == expected


def test_pin_replaces_zenmls_per_run_app_name(monkeypatch):
    """The whole point: ZenML's ``zenml-<run_id>`` collapses onto this environment's App.

    Upstream's own implementation is re-installed first, because importing ``runtime.flow`` anywhere
    in the session has already pinned it — the patch is process-wide by design.
    """
    modal_orchestrator = pytest.importorskip(
        "zenml.integrations.modal.orchestrators.modal_orchestrator"
    )
    monkeypatch.setattr(modal_app.settings, "decode_env", "prod")
    # monkeypatch restores this, so the pin below cannot leak into another test.
    monkeypatch.setattr(
        modal_orchestrator, "get_modal_app_name", lambda run_id: f"zenml-{run_id}"[:64]
    )

    # The upstream behaviour being replaced: a fresh App per run.
    assert modal_orchestrator.get_modal_app_name("run-a") == "zenml-run-a"
    assert modal_orchestrator.get_modal_app_name("run-b") == "zenml-run-b"

    modal_app.pin_orchestrator_app()

    # Every run id now collapses onto the one App for this environment.
    assert modal_orchestrator.get_modal_app_name("run-a") == "decode-prod"
    assert modal_orchestrator.get_modal_app_name("run-b") == "decode-prod"


def test_pin_is_a_no_op_without_the_modal_integration(monkeypatch):
    """The local stack never talks to Modal — a missing integration must not break a run."""
    monkeypatch.setitem(sys.modules, "zenml.integrations.modal.orchestrators", None)

    modal_app.pin_orchestrator_app()  # must not raise
