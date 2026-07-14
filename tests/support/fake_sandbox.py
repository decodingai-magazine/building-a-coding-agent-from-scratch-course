"""An in-memory fake of the sandbox executor seam for offline benchmark-runner tests (ADR-0017 §3,5).

The benchmark runner drives decode's real ``SandboxExecutor`` seam. These fakes stand in for it with
zero infra: :class:`FakeExecutor` duck-types the executor methods the eval sandbox uses
(``start`` / ``run`` / ``file_backend`` / ``aclose``) over an in-memory filesystem, and records an
ordered ``ops`` log so a test can assert the seed → run → inject → verify → teardown sequence and,
crucially, that the hidden ``verify.sh`` is ABSENT while the agent runs and present only at grade time.

Installed exactly as the real backend is: a test patches ``decode.sandbox.select_executor`` to return
a :class:`FakeExecutor`, so ``warm_executor`` wires it into the ``decode.tools.bash`` module seam and
the agent's ``bash`` calls route straight to :meth:`FakeExecutor.run`. ``start`` mirrors the modal
bootstrap — it loads the host Workspace tree into the in-memory fs — so seeded ``setup/`` files are
visible from the first command while ``verify/`` is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decode.tools.exec import ExecResult

# The hidden oracle's entrypoint (kept local so the fake needs no eval imports).
_VERIFY_SCRIPT = "verify.sh"


@dataclass
class FakeBackend:
    """The file-op half of the seam over an in-memory ``{rel: bytes}`` filesystem (ADR-0012 §4).

    Only ``read_bytes`` / ``write_bytes`` are implemented — the sandbox's verify injection writes
    through this backend, exactly what the real ``file_backend`` exposes for byte transport.
    """

    fs: dict[str, bytes]

    async def write_bytes(self, rel: str, data: bytes) -> None:
        self.fs[rel] = data

    async def read_bytes(self, rel: str) -> bytes:
        return self.fs[rel]


@dataclass
class FakeExecutor:
    """An in-memory stand-in for :class:`~decode.sandbox.executor.SandboxExecutor` (ADR-0017 §3).

    ``ops`` records every seam call in order — ``("start", ...)``, ``("run", command,
    verify_present)``, ``("inject", rel)``, ``("aclose",)`` — so a test asserts the lifecycle order
    and the verify-absent-during-run invariant. ``verify_result`` is the scripted
    :class:`~decode.tools.exec.ExecResult` returned for ``bash verify.sh`` at grade time.
    """

    verify_result: ExecResult = field(
        default_factory=lambda: ExecResult("PASS\n", "", 0, timed_out=False)
    )
    start_error: str | None = None
    fs: dict[str, bytes] = field(default_factory=dict)
    ops: list[tuple[Any, ...]] = field(default_factory=list)
    started: bool = False
    closed: bool = False

    async def start(self, workspace: Path) -> None:
        """Bootstrap-load the host Workspace tree into the in-memory fs (mirrors the modal upload).

        When ``start_error`` is set, raise it AFTER recording the attempt — the way a real backend
        fails on ``create`` (docker daemon down / bad modal creds), so tests can prove the benchmark
        catches a sandbox that never came up.
        """
        self.ops.append(("start", str(workspace)))
        if self.start_error is not None:
            raise RuntimeError(self.start_error)
        self.started = True
        for path in sorted(workspace.rglob("*")):
            if path.is_file():
                self.fs[path.relative_to(workspace).as_posix()] = path.read_bytes()

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        """Record the command with a snapshot of whether ``verify.sh`` is present, then reply."""
        verify_present = _VERIFY_SCRIPT in self.fs
        self.ops.append(("run", command, verify_present))
        if _VERIFY_SCRIPT in command:
            return self.verify_result
        return ExecResult("", "", 0, timed_out=False)

    async def file_backend(self, cwd: Path) -> FakeBackend:
        """Return the file-op backend over the shared in-memory fs (records the inject through it)."""
        return _RecordingBackend(self.fs, self.ops)

    async def aclose(self) -> None:
        """Reap the fake session (records teardown so a test can assert it ran on failure too)."""
        self.closed = True
        self.ops.append(("aclose",))


class _RecordingBackend(FakeBackend):
    """A :class:`FakeBackend` that also appends an ``("inject", rel)`` op per write (for ordering asserts)."""

    def __init__(self, fs: dict[str, bytes], ops: list[tuple[Any, ...]]) -> None:
        super().__init__(fs=fs)
        self._ops = ops

    async def write_bytes(self, rel: str, data: bytes) -> None:
        await super().write_bytes(rel, data)
        self._ops.append(("inject", rel))
