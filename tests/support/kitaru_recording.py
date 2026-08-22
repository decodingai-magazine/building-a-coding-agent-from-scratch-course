"""A fake Kitaru recording stack for the Recording Seam tests (ADR-0019 §3, task 134).

The seam imports BOTH halves of the stack lazily, only when recording is configured — the adapter
(``kitaru_pydantic_ai.KitaruAgent``) and the client it probes the workspace with
(``kitaru.client.KitaruAPIClient``) — so faking is a ``sys.modules`` swap of those two modules and
nothing else: no server, no credentials, no network.

Two deliberate choices:

* **The fake adapter really delegates.** ``FakeKitaruAgent.run`` awaits the wrapped agent, so a test
  that runs THROUGH the wrapper proves the run still produces the agent's answer — a fake that
  returned a canned string would pass while the wrapper silently swallowed the run.
* **Every workspace call recorded by NAME.** :attr:`FakeRecordingStack.probe_calls` carries
  ``(resource, target)`` tuples so a test can pin *which* workspace call the seam's reachability
  probe made (``agents.get <configured-id>`` when decode knows the agent, ``info.get`` when a Worker
  Task infers it) without asserting on payloads.

Import it as ``from support.kitaru_recording import install_fake_recording_stack``.
"""

from __future__ import annotations

import sys
import types
import uuid
from typing import Any


class FakeKitaruAgent:
    """``kitaru_pydantic_ai.KitaruAgent`` — records its construction, then delegates every run.

    The keyword-only signature mirrors the installed adapter's (pinned by
    ``tests/unit/decode/test_kitaru_dependency.py``), so a call this fake accepts is a call the real
    ``KitaruAgent`` accepts.
    """

    def __init__(
        self,
        agent: Any,
        *,
        agent_id: uuid.UUID | None = None,
        agent_version_id: uuid.UUID | None = None,
        session_name: str | None = None,
        batch_size: int = 20,
    ) -> None:
        self.wrapped = agent
        self.agent_id = agent_id
        self.agent_version_id = agent_version_id
        self.session_name = session_name
        self.batch_size = batch_size
        self.runs: list[str] = []

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        self.runs.append(str(args[0]) if args else "")
        return await self.wrapped.run(*args, **kwargs)


class FakeRecordingStack:
    """What a faked Kitaru workspace saw: every wrap, every probe call, every client close."""

    def __init__(self, probe_error: Exception | None = None) -> None:
        self.probe_error = probe_error
        self.wrapped: list[FakeKitaruAgent] = []
        self.probe_calls: list[tuple[str, Any]] = []
        self.opened = 0
        self.closed = 0

    def _record(self, resource: str, target: Any) -> None:
        self.probe_calls.append((resource, target))
        if self.probe_error is not None:
            raise self.probe_error

    def _wrap(self, agent: Any, **kwargs: Any) -> FakeKitaruAgent:
        wrapper = FakeKitaruAgent(agent, **kwargs)
        self.wrapped.append(wrapper)
        return wrapper


class _FakeAgentsResource:
    """``client.agents`` — only ``get``, the one call the seam's probe makes."""

    def __init__(self, stack: FakeRecordingStack) -> None:
        self._stack = stack

    async def get(self, agent_id: uuid.UUID) -> object:
        self._stack._record("agents.get", agent_id)
        return object()


class _FakeInfoResource:
    """``client.info`` — the unauthenticated reachability call for a Worker-inferred agent id."""

    def __init__(self, stack: FakeRecordingStack) -> None:
        self._stack = stack

    async def get(self) -> object:
        self._stack._record("info.get", None)
        return object()


class _FakeAPIClient:
    """``KitaruAPIClient()`` — must be ``close()``d like the real one, so the seam's is pinned."""

    def __init__(self, stack: FakeRecordingStack) -> None:
        self._stack = stack
        stack.opened += 1
        self.agents = _FakeAgentsResource(stack)
        self.info = _FakeInfoResource(stack)

    async def close(self) -> None:
        self._stack.closed += 1


def install_fake_recording_stack(
    monkeypatch: Any, *, probe_error: Exception | None = None
) -> FakeRecordingStack:
    """Swap ``kitaru_pydantic_ai`` + ``kitaru.client`` for fakes; return what they record.

    ``probe_error`` makes every workspace call raise it — an unreachable workspace, an expired
    login, or an agent id the workspace does not know, all of which the seam must treat the same
    way (degrade for a user-launched run, hard-fail under a Worker Task).
    """
    stack = FakeRecordingStack(probe_error)

    adapter = types.ModuleType("kitaru_pydantic_ai")
    adapter.KitaruAgent = stack._wrap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kitaru_pydantic_ai", adapter)

    client = types.ModuleType("kitaru.client")
    client.KitaruAPIClient = lambda *args, **kwargs: _FakeAPIClient(stack)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kitaru.client", client)

    return stack
