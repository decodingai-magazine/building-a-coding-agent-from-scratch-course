"""Real-docker integration tests for the sandbox Credential Proxy (ADR-0011 §6).

The living proof that the credential-injection topology holds against a **real docker daemon**: a
``mitmproxy/mitmproxy`` addon container on a per-run network, a proxy-wired
:class:`~decode.sandbox.docker_executor.DockerExecutor` worker pointed at it, and a stub upstream —
so a request the token-free worker makes **arrives at the upstream with the injected header**, while
the worker's own env holds **no** secret. It also drives the real
:func:`decode.runtime.flow._sandbox_proxy` context manager to prove it tears the proxy container +
network down on exit, **including when the flow body raises**, and restores the ``bash`` seam.

**Skipped, never failed, without a daemon.** A module-level ``docker info`` probe guards the whole
file with ``@pytest.mark.skipif`` (mirroring ``test_docker_executor.py``), so ``make ci`` stays green
on a machine with no Docker — these SKIP. ``kitaru.get_secret`` is **patched** so no real secret store
is needed; the topology itself is real. Every test tears its containers + network down in a
``finally`` (and asserts they are gone), so the suite leaves no docker litter even on failure.
"""

from __future__ import annotations

import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

import decode.runtime.flow as flow_mod
import decode.tools.bash as bash_mod
from decode.sandbox.docker_executor import DockerExecutor
from decode.sandbox.proxy import DockerCredentialProxy, SandboxProxyRule, build_credential_map
from decode.tools.exec import LocalExecutor


def _docker_available() -> bool:
    """True if a local docker daemon answers a fast ``docker info`` probe (else the file SKIPs)."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_DOCKER_AVAILABLE = _docker_available()

pytestmark = pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")

_SECRET_VALUE = "injected-secret-xyz789"
_INJECTED_HEADER = "X-Decode-Proxy-Auth"
_UPSTREAM_ALIAS = "upstream.local"

# A stub upstream that echoes every request header back in the response body, so the worker can prove
# which headers actually ARRIVED. Stdlib only (runs in ``python:3.12-slim``); binds port 80.
_UPSTREAM_SERVER = (
    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        body=''.join(f'{k}: {v}\\n' for k,v in self.headers.items()).encode()\n"
    "        self.send_response(200); self.send_header('Content-Length', str(len(body)))\n"
    "        self.end_headers(); self.wfile.write(body)\n"
    "    def log_message(self,*a): pass\n"
    "HTTPServer(('0.0.0.0',80),H).serve_forever()\n"
)
# The worker's outbound probe — python/urllib (slim has no curl); reads its ``http_proxy`` env.
_REQUEST_SCRIPT = (
    "import urllib.request\n"
    f"print(urllib.request.urlopen('http://{_UPSTREAM_ALIAS}/', timeout=10).read().decode())\n"
)


def _container_exists(name_or_id: str) -> bool:
    # Two separate probes on purpose: docker ANDs distinct filter types, so a single call with BOTH
    # ``name=`` and ``id=`` can never match (a container's auto-name never contains its id) — which
    # silently made the gone-assertions vacuous. Probing each type alone restores the intended OR.
    for key in ("id", "name"):
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"{key}={name_or_id}"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        if result.stdout.strip():
            return True
    return False


def _network_exists(name: str) -> bool:
    result = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", f"name={name}"],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    return bool(result.stdout.strip())


def _wait_tcp_ready(container: str, port: int, timeout_s: float = 15.0) -> None:
    """Poll (bounded) until a server inside ``container`` accepts a TCP connection on ``port``."""
    probe = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=1).close()"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "python3", "-c", probe],
            capture_output=True,
            timeout=10.0,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.2)
    raise AssertionError(f"{container} port {port} never became ready")


def _start_upstream(network: str) -> str:
    """Start the header-echoing stub upstream on ``network`` (alias ``upstream.local``); return its name."""
    name = f"decode-it-upstream-{uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            _UPSTREAM_ALIAS,
            "python:3.12-slim",
            "python3",
            "-c",
            _UPSTREAM_SERVER,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    _wait_tcp_ready(name, 80)
    return name


def _stop_container(name: str) -> None:
    subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=30.0)


async def test_worker_request_arrives_with_injected_header_but_worker_holds_no_secret(
    monkeypatch, tmp_path
):
    """The credential claim, proven end to end (ADR-0011 §6, AC lines 84-89).

    A rule injects ``X-Decode-Proxy-Auth: <secret>`` on requests to ``upstream.local``, resolved
    host-side from a **patched** Kitaru secret. The token-free worker makes a urllib request through
    the proxy; the stub upstream echoes the headers it received — proving the header ARRIVED — while a
    scan of the worker container's own env proves the secret is **absent** there (it lives only in the
    proxy container). The CA is mounted into the worker too.
    """
    monkeypatch.setattr(
        "kitaru.get_secret", lambda name: SimpleNamespace(values={"token": _SECRET_VALUE})
    )
    credential_map = build_credential_map(
        [
            SandboxProxyRule(
                name="upstream-auth",
                hosts=[_UPSTREAM_ALIAS],
                headers={_INJECTED_HEADER: "{{ test-secret.token }}"},
            )
        ]
    )
    proxy = DockerCredentialProxy(credential_map)
    executor: DockerExecutor | None = None
    upstream: str | None = None
    worker_id: str | None = None
    try:
        proxy.start()
        upstream = _start_upstream(proxy.network)
        # The worker's /workspace is the project's .decode/sandbox scratch (ADR-0011 §2 amended),
        # NOT the cwd itself — drop the probe script where the container will actually see it.
        scratch = tmp_path / ".decode" / "sandbox"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "req.py").write_text(_REQUEST_SCRIPT, encoding="utf-8")
        executor = DockerExecutor(
            network=proxy.network,
            proxy_env=proxy.worker_proxy_env,
            ca_cert_host_path=proxy.ca_cert_host_path,
        )

        result = await executor.run("python3 /workspace/req.py", cwd=tmp_path, timeout_s=30.0)
        worker_id = executor._container_id

        # The upstream echoed the header the proxy injected — it ARRIVED, though the worker never held it.
        assert result.exit_code == 0, result.stdout
        assert f"{_INJECTED_HEADER}: {_SECRET_VALUE}" in result.stdout

        # SECURITY: the worker container's own env carries the proxy URL but NOT the secret value.
        env = subprocess.run(
            ["docker", "exec", worker_id, "env"], capture_output=True, text=True, timeout=15.0
        ).stdout
        assert _SECRET_VALUE not in env
        assert "http_proxy=" in env  # it IS routed through the proxy — it just holds no token

        # The mitmproxy CA was bind-mounted into the worker's trust dir (update-ca-certificates ran).
        ca_listing = subprocess.run(
            ["docker", "exec", worker_id, "ls", "/usr/local/share/ca-certificates/"],
            capture_output=True,
            text=True,
            timeout=15.0,
        ).stdout
        assert "mitmproxy-ca-cert.crt" in ca_listing
    finally:
        if executor is not None:
            await executor.aclose()
        if upstream is not None:
            _stop_container(upstream)
        proxy.stop()

    # Everything is torn down — no docker litter left behind.
    assert not _container_exists(proxy._container_name)
    assert worker_id is None or not _container_exists(worker_id)
    assert upstream is None or not _container_exists(upstream)
    assert not _network_exists(proxy.network)


async def test_worker_trusts_the_proxy_ca_on_its_very_first_command(monkeypatch, tmp_path):
    """The CA-trust race regression (ADR-0011 §6): the FIRST worker command already trusts the CA.

    A lazily-created worker's first ``bash`` must not race a still-booting ``update-ca-certificates`` —
    otherwise the FIRST HTTPS tool call (e.g. the shipped ``github-token`` → ``api.github.com`` rule)
    fails ``CERTIFICATE_VERIFY_FAILED``. Proven without an upstream round-trip: ``openssl verify`` (no
    ``-CAfile``) of the proxy's own CA against the worker's DEFAULT trust store verifies it as a trusted
    root **only once it has been folded in** — so a green here means the CA was trusted before the very
    first command returned. FAILS on the pre-fix code (the first ``docker exec`` races the PID-1
    ``update-ca-certificates``); passes once ``_ensure_container`` folds the CA in synchronously.
    """
    monkeypatch.setattr(
        "kitaru.get_secret", lambda name: SimpleNamespace(values={"token": _SECRET_VALUE})
    )
    credential_map = build_credential_map(
        [
            SandboxProxyRule(
                name="upstream-auth",
                hosts=[_UPSTREAM_ALIAS],
                headers={_INJECTED_HEADER: "{{ test-secret.token }}"},
            )
        ]
    )
    proxy = DockerCredentialProxy(credential_map)
    executor: DockerExecutor | None = None
    try:
        proxy.start()
        executor = DockerExecutor(
            network=proxy.network,
            proxy_env=proxy.worker_proxy_env,
            ca_cert_host_path=proxy.ca_cert_host_path,
        )

        # THE FIRST command through the freshly-wired worker — it must already trust the proxy CA.
        result = await executor.run(
            "openssl verify /usr/local/share/ca-certificates/mitmproxy-ca-cert.crt",
            cwd=tmp_path,
            timeout_s=30.0,
        )

        assert result.exit_code == 0, f"proxy CA not trusted on the FIRST command:\n{result.stdout}"
        assert "OK" in result.stdout
    finally:
        if executor is not None:
            await executor.aclose()
        proxy.stop()

    assert not _container_exists(proxy._container_name)
    assert not _network_exists(proxy.network)


def _engage_proxy(monkeypatch) -> None:
    """Flip settings so ``_sandbox_proxy`` engages, with an empty (passthrough) rule set + fake secret."""
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(flow_mod.settings, "sandbox_credential_proxy_enabled", True)
    # A passthrough map is enough to prove the topology + teardown; no upstream request is made here.
    monkeypatch.setattr("decode.sandbox.proxy.DEFAULT_PROXY_RULES", [])
    # A fresh, un-selected bash seam so we can prove the context installs then restores it.
    monkeypatch.setattr(bash_mod, "_EXECUTOR", LocalExecutor())
    monkeypatch.setattr(bash_mod, "_executor_selected", False)


def test_sandbox_proxy_context_installs_the_seam_then_tears_it_all_down(monkeypatch):
    """The real ``_sandbox_proxy()`` installs a proxy-wired worker, then reaps everything on exit."""
    _engage_proxy(monkeypatch)
    captured: dict[str, object] = {}

    with flow_mod._sandbox_proxy():
        # Inside the span the bash seam is a proxy-wired DockerExecutor (not the LocalExecutor default).
        assert isinstance(bash_mod._EXECUTOR, DockerExecutor)
        assert bash_mod._EXECUTOR._network is not None
        assert bash_mod._executor_selected is True
        captured["container_name"] = _proxy_container_name()
        captured["network"] = bash_mod._EXECUTOR._network

    # On exit: the bash seam is restored to the none-mode default, and the proxy container + its network
    # are gone (the worker was never started — no bash ran — so only the proxy needs reaping).
    assert isinstance(bash_mod._EXECUTOR, LocalExecutor)
    assert bash_mod._executor_selected is False
    assert not _container_exists(str(captured["container_name"]))
    assert not _network_exists(str(captured["network"]))


def test_sandbox_proxy_context_tears_down_even_when_the_body_raises(monkeypatch):
    """AC (teardown incl. on error): a raising flow body still reaps the proxy container + network."""
    _engage_proxy(monkeypatch)
    seen: dict[str, object] = {}

    with pytest.raises(RuntimeError, match="boom"), flow_mod._sandbox_proxy():
        seen["container_name"] = _proxy_container_name()
        seen["network"] = bash_mod._EXECUTOR._network  # type: ignore[attr-defined]
        raise RuntimeError("boom in the flow body")

    assert isinstance(bash_mod._EXECUTOR, LocalExecutor)  # seam restored despite the error
    assert bash_mod._executor_selected is False
    assert not _container_exists(str(seen["container_name"]))
    assert not _network_exists(str(seen["network"]))


def _proxy_container_name() -> str:
    """The proxy container name currently wired into the bash seam's DockerExecutor network.

    The DockerExecutor holds the network name (``decode-sandbox-net-<suffix>``); the proxy container is
    ``decode-proxy-<suffix>`` — the same suffix — so we can name it without threading the proxy handle.
    """
    network = bash_mod._EXECUTOR._network  # type: ignore[attr-defined]
    assert isinstance(network, str)
    return network.replace("decode-sandbox-net-", "decode-proxy-")
