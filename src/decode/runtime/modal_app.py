"""One Modal App per environment for the headless runtime — not one App per run.

ZenML's Modal orchestrator names the App it launches the flow container in ``zenml-<run_id>``
(:func:`zenml.integrations.modal.orchestrators.modal_orchestrator.get_modal_app_name` — hardcoded,
with no ``ModalOrchestratorSettings`` field for it: those are gpu / region / cloud /
modal_environment / timeout / synchronous). Every ``decode run`` therefore left a fresh App behind —
a day of testing produced 16 live Apps, each holding one dead sandbox.

This pins it to ``decode-<env>``, so every run of an environment lands in that environment's App and
Modal's dashboard stays a list of environments instead of a list of runs. Modal's own guidance is the
same shape: ``App.lookup(name, create_if_missing=True)`` reuses a persistent named App, and many
Sandboxes attach to it.

**Why sharing an App across runs is safe:** ZenML never stops or deletes the App. It terminates
*sandboxes*, by id (``sandbox.terminate()``), on stop and on teardown — so two runs sharing an App
cannot kill each other. Verified against zenml's modal orchestrator, which has no ``app.stop()`` call
anywhere.

A monkeypatch is the only seam. The name is computed inside ``submit_dynamic_pipeline``, not read
from settings, and the flow container's controller picks it up from the ``ZENML_MODAL_APP_NAME``
env var the submitting client sets — so patching the client covers the step sandboxes too.
"""

from __future__ import annotations

import logging

from decode.config.settings import settings

logger = logging.getLogger(__name__)

# ``decode-<env>`` — the flow container's App. The bash sandboxes decode spawns *inside* it get their
# own App (``decode-sandbox-<env>``, see ``sandbox/modal_backend.py``): one is the harness, the other
# is the workspace, and keeping them apart means terminating one never touches the other.
APP_NAME_PREFIX = "decode"


def orchestrator_app_name() -> str:
    """``decode-<env>`` — dev / staging / prod (``local`` never reaches a Modal orchestrator)."""
    return f"{APP_NAME_PREFIX}-{settings.decode_env}"


def pin_orchestrator_app() -> None:
    """Point ZenML's Modal orchestrator at this environment's App instead of a per-run one.

    A no-op when the modal integration is not installed — that is the local stack, which never talks
    to Modal.
    """
    try:
        from zenml.integrations.modal.orchestrators import modal_orchestrator
    except ImportError:
        return

    modal_orchestrator.get_modal_app_name = lambda _run_id: orchestrator_app_name()  # type: ignore[assignment]
    logger.debug("pinned the Modal orchestrator App to %s", orchestrator_app_name())
