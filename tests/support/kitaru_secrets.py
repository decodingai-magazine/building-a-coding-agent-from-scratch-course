"""A fake ``kitaru.client`` for the Environment Bucket tests (ADR-0019 §5, task 132).

The kitaru 0.22.2 named-secrets API is async and lives on ``KitaruClient().api.secrets``
(``list`` / ``get`` / ``create`` / ``update``). Both readers of it — the Environment-Bucket settings
source (``decode.config.settings``) and the operator sync script (``scripts/sync_secrets.py``) —
import it LAZILY, so faking is a ``sys.modules`` swap of the ``kitaru.client`` module and nothing
else: no server, no workspace, no credentials.

Two deliberate choices:

* **Real request/response DTOs.** ``SecretListParams`` / ``SecretCreateRequest`` &co come from the
  installed SDK, so a payload this fake accepts is a payload the real client would send — a fake
  that took ``**kwargs`` would happily pass a call the server rejects with a 422.
* **Every call recorded by NAME.** :attr:`FakeWorkspace.calls` carries ``(method, name-or-id)``
  tuples so a test can pin "list by name, then get with values" — the two-step the secrets resource
  forces (there is no get-by-name endpoint) — without asserting on secret values.

Import it as ``from support.kitaru_secrets import install_fake_kitaru_client``.
"""

from __future__ import annotations

import sys
import types
import uuid
from datetime import UTC, datetime
from typing import Any

from kitaru.api_models.v1.base import Page
from kitaru.api_models.v1.secret import (
    SecretCreateRequest,
    SecretListParams,
    SecretResponse,
    SecretUpdateRequest,
    SecretWithValuesResponse,
)


class FakeWorkspace:
    """The named secrets a faked Kitaru workspace holds, plus every call made against it."""

    def __init__(
        self,
        secrets: dict[str, dict[str, str]] | None = None,
        error: Exception | None = None,
        error_on: frozenset[str] = frozenset({"list", "get", "create", "update"}),
    ) -> None:
        self.values: dict[str, dict[str, str]] = {k: dict(v) for k, v in (secrets or {}).items()}
        self.ids: dict[str, uuid.UUID] = {name: uuid.uuid4() for name in self.values}
        self.error = error
        self.error_on = error_on
        self.calls: list[tuple[str, Any]] = []
        self.opened = 0
        self.closed = 0

    # --- what a test asserts on -------------------------------------------------------------

    @property
    def requested_names(self) -> list[str]:
        """Every bucket name a ``list`` filter asked for, in order (``[]`` when kitaru was untouched)."""
        return [name for method, name in self.calls if method == "list"]

    # --- the fake API surface ---------------------------------------------------------------

    def _record(self, method: str, target: Any) -> None:
        self.calls.append((method, target))
        if self.error is not None and method in self.error_on:
            raise self.error

    def _response(self, name: str) -> SecretResponse:
        return SecretResponse(
            id=self.ids[name],
            owner_id=uuid.UUID(int=1),
            name=name,
            type=None,
            created=datetime.now(UTC),
            updated=datetime.now(UTC),
        )

    def _name_for(self, secret_id: uuid.UUID) -> str:
        for name, known_id in self.ids.items():
            if known_id == secret_id:
                return name
        raise LookupError(f"404: no secret with id {secret_id}")


class _FakeSecretsResource:
    """``client.api.secrets`` — the four methods decode uses, honouring the ``name`` filter."""

    def __init__(self, workspace: FakeWorkspace) -> None:
        self._workspace = workspace

    async def list(self, params: SecretListParams | None = None) -> Page[SecretResponse]:
        condition = getattr(params, "filter", None)
        wanted = getattr(condition, "value", None)
        self._workspace._record("list", wanted)
        names = [name for name in self._workspace.values if wanted in (None, name)]
        return Page[SecretResponse](
            items=[self._workspace._response(name) for name in names], next_cursor=None
        )

    async def get(
        self, secret_id: uuid.UUID, include_values: bool = False
    ) -> SecretWithValuesResponse:
        self._workspace._record("get", secret_id)
        name = self._workspace._name_for(secret_id)
        base = self._workspace._response(name).model_dump()
        return SecretWithValuesResponse(**base, values=self._workspace.values[name])

    async def create(self, request: SecretCreateRequest) -> SecretResponse:
        self._workspace._record("create", request.name)
        if request.name in self._workspace.values:
            raise RuntimeError(f"409: secret {request.name} already exists")
        self._workspace.ids[request.name] = uuid.uuid4()
        self._workspace.values[request.name] = {
            key: value.get_secret_value() for key, value in request.values.items()
        }
        return self._workspace._response(request.name)

    async def update(self, secret_id: uuid.UUID, request: SecretUpdateRequest) -> SecretResponse:
        self._workspace._record("update", secret_id)
        name = self._workspace._name_for(secret_id)
        if request.values is not None:
            # PATCH replaces the whole key set — the mirror semantics the sync script relies on.
            self._workspace.values[name] = {
                key: value.get_secret_value() for key, value in request.values.items()
            }
        return self._workspace._response(name)


class _FakeApi:
    def __init__(self, workspace: FakeWorkspace) -> None:
        self.secrets = _FakeSecretsResource(workspace)


class _FakeClient:
    """``KitaruClient()`` — an async context-free client that must be ``close()``d like the real one."""

    def __init__(self, workspace: FakeWorkspace) -> None:
        self._workspace = workspace
        workspace.opened += 1
        self.api = _FakeApi(workspace)

    async def close(self) -> None:
        self._workspace.closed += 1


def install_fake_kitaru_client(
    monkeypatch: Any,
    secrets: dict[str, dict[str, str]] | None = None,
    *,
    error: Exception | None = None,
    error_on: frozenset[str] = frozenset({"list", "get", "create", "update"}),
) -> FakeWorkspace:
    """Swap ``kitaru.client`` for a fake holding ``secrets`` (``{bucket: {KEY: value}}``).

    ``error`` makes the API calls named by ``error_on`` raise it — a missing workspace URL, an
    expired login or an unreachable server (all four methods), or a rejected write only
    (``{"create", "update"}``), which fail on different sides of the read/write split.
    """
    workspace = FakeWorkspace(secrets, error, error_on)
    module = types.ModuleType("kitaru.client")
    module.KitaruClient = lambda *args, **kwargs: _FakeClient(workspace)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kitaru.client", module)
    return workspace
