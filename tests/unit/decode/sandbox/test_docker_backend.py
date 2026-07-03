"""Hermetic unit tests for the Docker sandbox backend (``decode.sandbox.docker_backend``, ADR-0012 §2,4).

These exercise the parts of :class:`DockerBackend` that need **no docker daemon**: the fresh-exec argv
(``docker run`` + a fresh ``docker exec`` per call, proxy wiring off/on), the exec timeout that kills
only the ``docker exec`` client (a real ``sleep`` child stands in — no daemon), the daemon-lost /
spawn-failure rendering (exit-125, never a crash), and the **pathlib file ops on the bind-mounted
Workspace** (which are truthful against a plain tmp dir — the whole point of the mount). The real
end-to-end contract (a live container, filesystem persistence, container teardown) lives in the
``@skipif``-guarded ``tests/integration/test_docker_executor.py``.

Fresh-exec means there is **no** persistent shell, so the retired marker/``$?`` protocol,
read-until-marker loop, and loop-free shell-teardown tests are gone with the code they tested.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from decode.config.settings import settings
from decode.sandbox.docker_backend import (
    _DAEMON_LOST_EXIT,
    _TIMEOUT_EXIT,
    _WORKER_CA_PATH,
    _WORKSPACE,
    DockerBackend,
)
from decode.sandbox.executor import FileStat


def _fake_proc(mocker, *, stdout=b"", stderr=b"", returncode=0):
    """A fake asyncio subprocess: awaitable ``communicate`` + a ``returncode`` (no real process)."""
    proc = mocker.MagicMock()
    proc.communicate = mocker.AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = mocker.MagicMock()
    proc.wait = mocker.AsyncMock()
    return proc


# --- docker run argv (byte-identical off the proxy path) --------------------------------------


def test_run_args_mount_the_workspace_without_proxy_wiring():
    # The non-proxy backend mounts the resolved Workspace at /workspace with no --network, no -e, no CA
    # mount, and a bare ``sleep infinity`` entry. No skills mount (seeded host-side now, ADR-0012 §5).
    args = DockerBackend()._run_args(Path("/ws"))

    assert args == [
        "run",
        "-d",
        "--rm",
        "-v",
        f"/ws:{_WORKSPACE}",
        "-w",
        _WORKSPACE,
        settings.sandbox_image,
        "sleep",
        "infinity",
    ]


def test_run_args_add_network_env_and_ca_mount_when_wired():
    backend = DockerBackend(
        network="decode-sandbox-net-abc",
        proxy_env={"http_proxy": "http://decode-proxy-abc:8080", "no_proxy": "localhost"},
        ca_cert_host_path=Path("/host/certs/mitmproxy-ca-cert.pem"),
    )

    args = backend._run_args(Path("/ws"))

    # The base (mount) prefix is unchanged; proxy flags are additive and in a stable order.
    assert args[:7] == ["run", "-d", "--rm", "-v", f"/ws:{_WORKSPACE}", "-w", _WORKSPACE]
    assert "--network" in args and args[args.index("--network") + 1] == "decode-sandbox-net-abc"
    assert "http_proxy=http://decode-proxy-abc:8080" in args
    assert "no_proxy=localhost" in args
    assert f"/host/certs/mitmproxy-ca-cert.pem:{_WORKER_CA_PATH}:ro" in args
    # The entry stays a bare ``sleep infinity`` — the CA is trusted by a synchronous docker exec.
    assert args[-3:] == [settings.sandbox_image, "sleep", "infinity"]


def test_proxy_wiring_defaults_to_none_so_construction_is_inert():
    backend = DockerBackend()

    assert backend._network is None
    assert backend._proxy_env is None
    assert backend._ca_cert_host_path is None
    assert backend._container_id is None
    assert backend._workspace is None


# --- create: start the keeper container (no daemon — docker run is faked) ----------------------


async def test_create_starts_the_container_and_caches_the_id(mocker, tmp_path):
    run_proc = _fake_proc(mocker, stdout=b"container123\n")
    spawn = mocker.patch(
        "asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[run_proc])
    )
    backend = DockerBackend()

    await backend.create(tmp_path)
    await backend.create(tmp_path)  # idempotent: the cached id short-circuits

    assert backend._container_id == "container123"
    assert backend._workspace == tmp_path.resolve()
    assert spawn.await_count == 1  # exactly one docker run — no second container
    argv = spawn.await_args_list[0].args
    assert argv[0] == "docker"
    assert list(argv[1:]) == backend._run_args(tmp_path.resolve())


async def test_create_raises_on_a_docker_run_failure(mocker, tmp_path):
    # A non-zero ``docker run`` (bad image / mount) raises so the executor renders it; no id is cached.
    run_proc = _fake_proc(mocker, stderr=b"No such image", returncode=1)
    mocker.patch("asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[run_proc]))
    backend = DockerBackend()

    with pytest.raises(RuntimeError, match="docker run failed"):
        await backend.create(tmp_path)

    assert backend._container_id is None  # nothing cached → a later create re-attempts


async def test_create_trusts_the_ca_synchronously_on_the_proxy_path(mocker, tmp_path):
    # On the proxy path create runs ``docker exec <id> update-ca-certificates`` and WAITS before
    # returning, so the first command already trusts the CA (no daemon — run + exec are faked).
    run_proc = _fake_proc(mocker, stdout=b"container123\n")
    exec_proc = _fake_proc(mocker, stdout=b"updated\n")
    spawn = mocker.patch(
        "asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[run_proc, exec_proc])
    )
    backend = DockerBackend(ca_cert_host_path=Path("/host/mitmproxy-ca-cert.pem"))

    await backend.create(tmp_path)

    assert backend._container_id == "container123"
    assert spawn.await_count == 2  # docker run, THEN docker exec update-ca-certificates
    exec_argv = spawn.await_args_list[1].args
    assert exec_argv[:4] == ("docker", "exec", "container123", "update-ca-certificates")


async def test_create_runs_no_ca_step_off_the_proxy_path(mocker, tmp_path):
    run_proc = _fake_proc(mocker, stdout=b"cid\n")
    spawn = mocker.patch(
        "asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[run_proc])
    )

    await DockerBackend().create(tmp_path)

    assert spawn.await_count == 1  # the docker run only — no CA step on the non-proxy path


async def test_create_reaps_the_worker_and_drops_the_id_when_ca_trust_fails(mocker, tmp_path):
    # A non-zero update-ca-certificates reaps the just-created worker and raises; create drops the id
    # so a later run re-creates from scratch (never a leaked, untrusted worker).
    run_proc = _fake_proc(mocker, stdout=b"container123\n")
    exec_proc = _fake_proc(mocker, stdout=b"boom\n", returncode=1)
    mocker.patch(
        "asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[run_proc, exec_proc])
    )
    reap = mocker.patch("decode.sandbox.docker_backend._run_docker_quiet", new=mocker.AsyncMock())
    backend = DockerBackend(ca_cert_host_path=Path("/host/ca.pem"))

    with pytest.raises(RuntimeError, match="update-ca-certificates failed"):
        await backend.create(tmp_path)

    reap.assert_awaited_once_with("rm", "-f", "container123")  # the worker was reaped, not leaked
    assert backend._container_id is None  # the id is dropped after the reap


# --- exec: a fresh ``docker exec`` per call ---------------------------------------------------


async def test_exec_runs_a_fresh_docker_exec_with_separate_streams(mocker):
    proc = _fake_proc(mocker, stdout=b"out\n", stderr=b"err\n", returncode=0)
    spawn = mocker.patch("asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[proc]))
    backend = DockerBackend()
    backend._container_id = "cid-live"  # container already up

    result = await backend.exec("bash", "-lc", "echo hi", timeout_s=30.0)

    # A fresh ``docker exec -w /workspace <id> bash -lc echo hi`` — separate stdout/stderr preserved.
    argv = spawn.await_args_list[0].args
    assert list(argv) == ["docker", "exec", "-w", _WORKSPACE, "cid-live", "bash", "-lc", "echo hi"]
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"  # NOT merged — separate streams (unlike the retired shell)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.note == ""


async def test_exec_reports_a_non_zero_exit_code(mocker):
    proc = _fake_proc(mocker, stderr=b"nope\n", returncode=3)
    mocker.patch("asyncio.create_subprocess_exec", new=mocker.AsyncMock(side_effect=[proc]))
    backend = DockerBackend()
    backend._container_id = "cid"

    result = await backend.exec("bash", "-lc", "false", timeout_s=30.0)

    assert result.exit_code == 3
    assert result.timed_out is False


async def test_exec_renders_a_failure_when_no_container_is_running(mocker):
    # Defensive: exec called before a successful create renders exit-125, never crashes.
    spawn = mocker.patch("asyncio.create_subprocess_exec")
    backend = DockerBackend()  # _container_id is None

    result = await backend.exec("bash", "-lc", "echo hi", timeout_s=30.0)

    assert result.exit_code == _DAEMON_LOST_EXIT
    assert result.timed_out is False
    assert result.note  # a session-lost note is set
    spawn.assert_not_called()  # nothing was spawned — there is no container to exec in


async def test_exec_renders_a_failure_when_docker_cannot_spawn(mocker):
    # The ``docker`` CLI vanished mid-session (OSError on spawn): render exit-125, do not crash the tool.
    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=mocker.AsyncMock(side_effect=FileNotFoundError("docker")),
    )
    backend = DockerBackend()
    backend._container_id = "cid"

    result = await backend.exec("bash", "-lc", "echo hi", timeout_s=30.0)

    assert result.exit_code == _DAEMON_LOST_EXIT
    assert result.note  # never an empty, silent failure


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` names a live process; False once it is gone (fully reaped, no zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def test_exec_timeout_kills_only_the_client_process_group(mocker):
    # A real ``sleep`` child stands in for the ``docker exec`` client (no daemon), spawned with the same
    # pipe + ``start_new_session`` shape, so it exercises the identical process-group kill. On timeout
    # the client is killed (SIGTERM→SIGKILL), timed_out=True, note="" (fresh-exec — no shell reset).
    real_spawn = asyncio.create_subprocess_exec  # capture before patching (avoid self-recursion)

    async def _spawn_real_sleep(*_args, **_kwargs):
        return await real_spawn(
            "sleep",
            "30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    mocker.patch("asyncio.create_subprocess_exec", side_effect=_spawn_real_sleep)
    backend = DockerBackend()
    backend._container_id = "cid"

    result = await backend.exec("bash", "-lc", "sleep 30", timeout_s=0.3)

    assert result.timed_out is True
    assert result.exit_code == _TIMEOUT_EXIT
    assert result.note == ""  # fresh-exec: only the command died, no session-level reset note


# --- file ops on the bind mount (truthful against a plain tmp dir) -----------------------------


async def test_file_ops_round_trip_on_the_workspace(tmp_path):
    backend = DockerBackend()
    backend._workspace = tmp_path  # the mount source IS the sandbox fs

    await backend.write_bytes("sub/f.txt", b"hi there")
    assert (tmp_path / "sub" / "f.txt").read_bytes() == b"hi there"  # parents created
    assert await backend.read_bytes("sub/f.txt") == b"hi there"

    await backend.make_directory("made/deep")
    assert (tmp_path / "made" / "deep").is_dir()

    stat = await backend.stat("sub/f.txt")
    assert stat == FileStat(path="sub/f.txt", is_dir=False, size=len(b"hi there"))
    dir_stat = await backend.stat("sub")
    assert dir_stat is not None and dir_stat.is_dir is True
    assert await backend.stat("missing") is None  # absent → None, never raises


async def test_list_dir_returns_logical_paths(tmp_path):
    backend = DockerBackend()
    backend._workspace = tmp_path
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_bytes(b"aa")
    (tmp_path / "sub" / "b").mkdir()

    entries = await backend.list_dir("sub")

    # Logical (workspace-relative, POSIX) paths, sorted by name; dirs flagged.
    assert entries == [
        FileStat(path="sub/a.txt", is_dir=False, size=2),
        FileStat(path="sub/b", is_dir=True, size=(tmp_path / "sub" / "b").stat().st_size),
    ]


async def test_list_dir_at_the_root_uses_bare_names(tmp_path):
    backend = DockerBackend()
    backend._workspace = tmp_path
    (tmp_path / "top.txt").write_bytes(b"x")

    entries = await backend.list_dir("")

    assert entries == [FileStat(path="top.txt", is_dir=False, size=1)]


async def test_remove_deletes_files_and_directory_trees(tmp_path):
    backend = DockerBackend()
    backend._workspace = tmp_path
    (tmp_path / "f.txt").write_bytes(b"x")
    (tmp_path / "tree" / "nested").mkdir(parents=True)
    (tmp_path / "tree" / "nested" / "g.txt").write_bytes(b"y")

    await backend.remove("f.txt")
    await backend.remove("tree")
    await backend.remove("already-gone")  # missing_ok — no raise

    assert not (tmp_path / "f.txt").exists()
    assert not (tmp_path / "tree").exists()


async def test_file_ops_require_a_created_workspace():
    backend = DockerBackend()  # _workspace is None

    with pytest.raises(RuntimeError, match="created workspace"):
        await backend.read_bytes("f.txt")


# --- destroy: docker rm -f, loop-free + idempotent --------------------------------------------


async def test_destroy_force_removes_the_container(mocker):
    reap = mocker.patch("decode.sandbox.docker_backend._run_docker_quiet", new=mocker.AsyncMock())
    backend = DockerBackend()
    backend._container_id = "cid-live"
    backend._workspace = Path("/ws")

    await backend.destroy()

    reap.assert_awaited_once_with("rm", "-f", "cid-live")
    assert backend._container_id is None  # cleared so a later create is a fresh session
    assert backend._workspace is None


async def test_destroy_is_a_safe_noop_when_nothing_was_created(mocker):
    reap = mocker.patch("decode.sandbox.docker_backend._run_docker_quiet", new=mocker.AsyncMock())
    backend = DockerBackend()

    await backend.destroy()
    await backend.destroy()  # double destroy must not raise

    reap.assert_not_awaited()  # no container → nothing to remove


async def test_export_is_a_noop(tmp_path):
    # Docker's mount is live, so export sweeps nothing — a plain no-op that never touches the daemon.
    backend = DockerBackend()
    backend._workspace = tmp_path

    assert await backend.export() is None
