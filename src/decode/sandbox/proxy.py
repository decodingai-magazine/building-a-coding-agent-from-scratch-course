"""The sandbox Credential Proxy — host-side rules + a mitmproxy container (ADR-0011 §6).

The headless + docker-only Credential Proxy, adapted from the kitaru
``examples/end_to_end/agent_harness_platform`` canonical shape (``secrets.py`` +
``stage_4_credential_proxy.py`` — those classes are **not** in the ``kitaru`` package; decode adapts
them). It lets a sandboxed **Worker** make authenticated tool calls while holding **no** secret:

* :class:`SandboxProxyRule` + :func:`build_credential_map` — **host-side** template resolution. Each
  rule maps a set of hosts to header templates (``{{ secret-name.key }}``); the map is resolved once,
  at flow start, in the decode host process via :func:`kitaru.get_secret`, into a plain
  ``{host: {header: value}}`` dict. The resolved values are handed **only** to the proxy container's
  env — never the worker's (AGENTS.md: *secrets never reach the model or the sandbox payload*).
* :class:`DockerCredentialProxy` — the **topology**: a ``mitmproxy/mitmproxy`` container running the
  mounted :mod:`decode.sandbox.proxy_addon` on a per-run docker network, holding the credential map in
  its own env and writing its CA to a host-shared dir. The worker (a proxy-wired
  ``SandboxExecutor(DockerBackend(...))``) is pointed at it via ``http_proxy`` / ``https_proxy`` and
  trusts its CA, so the addon injects the matching host's headers **after** a request leaves the
  token-free worker.

**Built only inside the headless flow.** :func:`decode.runtime.flow._sandbox_proxy` imports this
module lazily, only when ``sandbox_mode == "docker"`` **and** ``sandbox_credential_proxy_enabled``, so
the interactive REPL never imports it and **bare ``decode`` never imports kitaru** (the invariant
holds). :data:`DEFAULT_PROXY_RULES` ships **empty** (opt-in; an empty map is a passthrough proxy).

**Cooperative egress ceiling.** `ponytail:` the worker is *pointed* at the proxy (``http_proxy`` +
trusted CA), not *forced* through it — this is not an exfiltration barrier. An internal-only network
with default-deny egress is the upgrade path (ADR-0011 §6). The credential claim — the worker never
holds a token — holds regardless, and is proven by scanning the worker's env in the integration test.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

# ``{{ secret-name.key }}`` — the template a rule's header value may embed. Resolved host-side via
# ``kitaru.get_secret(name).values[key]``. Names/keys allow word chars, dots, and hyphens (kitaru
# secret names are hyphenated, e.g. ``github-token``).
_TEMPLATE_RE = re.compile(r"\{\{\s*(?P<name>[A-Za-z0-9._\-]+)\.(?P<key>[A-Za-z0-9._\-]+)\s*\}\}")


@dataclass(frozen=True, slots=True)
class SandboxProxyRule:
    """One credential-injection rule: inject ``headers`` on every request to any of ``hosts``.

    ``headers`` values may embed ``{{ secret-name.key }}`` templates that :func:`build_credential_map`
    resolves host-side from a Kitaru secret. Frozen so a rule is an immutable declaration; ``hosts`` /
    ``headers`` are declared inline (required fields, so no mutable-default footgun).
    """

    name: str
    hosts: list[str]
    headers: dict[str, str]


# Ships EMPTY — the Credential Proxy is opt-in and an empty map is a passthrough proxy that injects
# nothing. Populate it to route a sandboxed tool call through the proxy with a credential the worker
# never holds; the ``{{ name.key }}`` template resolves host-side from a Kitaru secret at flow start.
# Example — inject a GitHub token on every request to the GitHub API:
#
#     DEFAULT_PROXY_RULES = [
#         SandboxProxyRule(
#             name="github-auth",
#             hosts=["api.github.com"],
#             headers={"Authorization": "Bearer {{ github-token.value }}"},
#         ),
#     ]
DEFAULT_PROXY_RULES: list[SandboxProxyRule] = []


def _resolve_templates(value: str, *, cache: dict[str, dict[str, str]]) -> str:
    """Replace every ``{{ name.key }}`` in ``value`` with ``kitaru.get_secret(name).values[key]``.

    ``cache`` memoizes each fetched secret's values for the span of one :func:`build_credential_map`
    call, so a secret referenced by several headers/rules is fetched once. A **missing secret**
    surfaces Kitaru's own error from ``get_secret`` (``KitaruRuntimeError``) and a **missing key**
    raises ``KeyError`` — both propagate (never a silent skip; ADR-0011 §6 / the task AC). ``kitaru``
    is imported lazily so this module stays kitaru-free until a headless docker flow calls it.
    """
    from kitaru import get_secret

    def _sub(match: re.Match[str]) -> str:
        name = match.group("name")
        key = match.group("key")
        if name not in cache:
            cache[name] = get_secret(name).values  # KitaruRuntimeError propagates if absent
        return cache[name][key]  # KeyError propagates if the secret has no such key

    return _TEMPLATE_RE.sub(_sub, value)


def build_credential_map(rules: list[SandboxProxyRule]) -> dict[str, dict[str, str]]:
    """Resolve ``rules`` into the ``{host: {header: value}}`` map the proxy container consumes.

    Runs **host-side, at flow start** (ADR-0011 §6): each rule's ``{{ name.key }}`` header templates
    are resolved via :func:`kitaru.get_secret`, then folded into a per-host header map (multiple rules
    on the same host merge). An **empty** ``rules`` yields ``{}`` (a passthrough proxy). Never logs the
    resolved **values** — only rule/host/header **names** (task-061 discipline), so the ``[sandbox]``
    log lines let an operator correlate an injection without leaking a secret.
    """
    cache: dict[str, dict[str, str]] = {}
    result: dict[str, dict[str, str]] = {}
    for rule in rules:
        resolved = {
            header: _resolve_templates(value, cache=cache) for header, value in rule.headers.items()
        }
        for host in rule.hosts:
            result.setdefault(host, {}).update(resolved)
        # NAMES only — never the resolved values (they are secrets). Lets an operator map a
        # ``[decode-proxy] injected headers for <host>`` line back to the rule that produced it.
        logger.debug(
            "[sandbox] proxy rule %r resolved (hosts=%s, headers=%s)",
            rule.name,
            rule.hosts,
            sorted(rule.headers),
        )
    return result


# --- DockerCredentialProxy: the mitmproxy container topology ----------------------------------

# The container-side path the standalone addon is mounted (read-only) at, and passed to ``mitmdump
# -s``. ``proxy_addon.py`` is this module's sibling on the host.
_ADDON_HOST_PATH = Path(__file__).parent / "proxy_addon.py"
_ADDON_CONTAINER_PATH = "/opt/proxy_addon.py"
# mitmproxy's confdir inside the container; bind-mounted to a host temp dir so the CA it generates
# there (``mitmproxy-ca-cert.pem``) is readable host-side for the worker to mount + trust.
_PROXY_CONFDIR = "/certs"
_CA_FILENAME = "mitmproxy-ca-cert.pem"
# The env var the addon reads the credential map (JSON) from — set on the PROXY container ONLY.
_CREDENTIAL_MAP_ENV = "DECODE_CREDENTIAL_MAP"
_LISTEN_PORT = 8080
# Bounds for the readiness wait (CA file appears + the listen port answers). Generous: a cold image
# start is a few seconds; a stall past this is a real failure worth surfacing.
_READY_TIMEOUT_S = 20.0
_READY_POLL_S = 0.2
_DOCKER_CALL_TIMEOUT_S = 30.0
_STOP_TIMEOUT_S = 2


class DockerCredentialProxy:
    """A ``mitmproxy/mitmproxy`` addon container that injects tool credentials per host (ADR-0011 §6).

    Lifecycle is explicit (:meth:`start` / :meth:`stop`), driven by
    :func:`decode.runtime.flow._sandbox_proxy`. On :meth:`start` it creates a per-run docker network,
    starts the proxy container (the mounted :mod:`decode.sandbox.proxy_addon`, the credential map in
    the container's **own** env, ``mitmdump`` writing its CA to a host-shared dir), and waits until the
    CA is written and the proxy is listening. On :meth:`stop` it removes the container + network +
    temp dir — best-effort, safe to call even if :meth:`start` failed partway. All docker access is the
    standard CLI via **sync** ``subprocess`` (the host flow thread is sync), so teardown is
    loop-independent (ADR-0011 §4 lesson).

    Uniquely-named per instance (a uuid suffix), so concurrent headless runs never collide on the
    network / container name. Access is CLI-only (no docker SDK), mirroring
    :class:`~decode.sandbox.docker_backend.DockerBackend`.
    """

    def __init__(self, credential_map: dict[str, dict[str, str]]) -> None:
        self._credential_map = credential_map
        suffix = uuid4().hex[:12]
        self._network_name = f"decode-sandbox-net-{suffix}"
        self._container_name = f"decode-proxy-{suffix}"
        self._container_id: str | None = None
        self._cert_dir: Path | None = None

    @property
    def network(self) -> str:
        """The per-run docker network the worker joins to reach the proxy by container name."""
        return self._network_name

    @property
    def worker_proxy_env(self) -> dict[str, str]:
        """The ``http_proxy`` / ``https_proxy`` env the worker gets, pointing at this proxy container.

        Docker's embedded DNS resolves the proxy's container name on the shared network. Both the
        lower- and upper-case variants are set (different HTTP clients read different casings), plus a
        ``no_proxy`` for loopback. These carry **no** secret — just the proxy URL.
        """
        url = f"http://{self._container_name}:{_LISTEN_PORT}"
        return {
            "http_proxy": url,
            "https_proxy": url,
            "HTTP_PROXY": url,
            "HTTPS_PROXY": url,
            "no_proxy": "localhost,127.0.0.1",
            "NO_PROXY": "localhost,127.0.0.1",
        }

    @property
    def ca_cert_host_path(self) -> Path:
        """Host path to the mitmproxy CA the worker mounts + trusts (valid after :meth:`start`)."""
        if self._cert_dir is None:
            raise RuntimeError("DockerCredentialProxy.start() must run before ca_cert_host_path")
        return self._cert_dir / _CA_FILENAME

    def start(self) -> None:
        """Create the network, start the proxy container, and wait until it is ready (ADR-0011 §6)."""
        self._cert_dir = Path(tempfile.mkdtemp(prefix="decode-proxy-certs-"))
        # World-writable so mitmdump (a non-root user inside the container) can write its CA into the
        # bind-mounted confdir. The dir holds only the generated CA — no decode secret ever lands here
        # (the credential map rides an ``--env-file``, not this mounted dir; see below).
        self._cert_dir.chmod(0o777)
        _docker("network", "create", self._network_name)
        self._container_id = self._run_proxy_container()
        self._wait_until_ready()
        logger.info(
            "[sandbox] proxy start %s (image=%s, hosts=%s)",
            self._container_id[:12],
            _proxy_image(),
            sorted(self._credential_map),
        )

    def _run_proxy_container(self) -> str:
        """``docker run`` the mitmproxy container; hand the credential map via a private ``--env-file``.

        The resolved map goes through an ``--env-file`` (a ``0600`` temp file, deleted the moment
        ``docker run`` has consumed it) rather than ``-e DECODE_CREDENTIAL_MAP=<json>`` — so the resolved
        secret never appears in the ``docker run`` argv (host ``ps``) nor lingers on disk. It still lands
        only in the **proxy** container's env (never the worker's), as before.
        """
        env_fd, env_path = tempfile.mkstemp(prefix="decode-proxy-cred-", suffix=".env")
        try:
            with os.fdopen(env_fd, "w", encoding="utf-8") as handle:
                handle.write(
                    f"{_CREDENTIAL_MAP_ENV}="
                    f"{json.dumps(self._credential_map, separators=(',', ':'))}\n"
                )
            return _docker(
                "run",
                "-d",
                "--rm",
                "--name",
                self._container_name,
                "--network",
                self._network_name,
                "--env-file",
                env_path,
                "-v",
                f"{_ADDON_HOST_PATH}:{_ADDON_CONTAINER_PATH}:ro",
                "-v",
                f"{self._cert_dir}:{_PROXY_CONFDIR}",
                _proxy_image(),
                "mitmdump",
                "--quiet",
                "--listen-host",
                "0.0.0.0",
                "--listen-port",
                str(_LISTEN_PORT),
                "--set",
                f"confdir={_PROXY_CONFDIR}",
                "-s",
                _ADDON_CONTAINER_PATH,
            ).strip()
        finally:
            # Consumed by ``docker run`` (the env is baked into the container) — delete it immediately
            # so the resolved secret's on-disk lifetime is a few milliseconds.
            with contextlib.suppress(OSError):
                os.unlink(env_path)

    def stop(self) -> None:
        """Tear the proxy down — stop the container, remove the network + temp dir (best-effort)."""
        if self._container_id is not None:
            logger.info("[sandbox] proxy stop %s", self._container_id[:12])
        _docker_quiet("stop", "--time", str(_STOP_TIMEOUT_S), self._container_name)
        # ``docker rm -f`` on the worker (the flow reaps it before us) detaches its endpoint, but the
        # daemon's network-endpoint cleanup can lag a beat behind container removal — a bounded retry
        # keeps the "network is gone after teardown" guarantee (AGENTS.md-style best-effort teardown).
        for _ in range(10):
            if _docker_quiet("network", "rm", self._network_name):
                break
            time.sleep(_READY_POLL_S)
        if self._cert_dir is not None:
            shutil.rmtree(self._cert_dir, ignore_errors=True)
            self._cert_dir = None
        self._container_id = None

    def _wait_until_ready(self) -> None:
        """Block until the CA file exists **and** the proxy answers on its listen port (bounded)."""
        ca_path = self.ca_cert_host_path
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if ca_path.exists() and self._port_is_listening():
                return
            time.sleep(_READY_POLL_S)
        raise RuntimeError(
            f"credential proxy container {self._container_name!r} was not ready within "
            f"{_READY_TIMEOUT_S:g}s (CA written + port {_LISTEN_PORT} listening)"
        )

    def _port_is_listening(self) -> bool:
        """True once ``mitmdump`` accepts a TCP connection on its listen port (probed via python3)."""
        probe = (
            "import socket; socket.create_connection"
            f"(('127.0.0.1', {_LISTEN_PORT}), timeout=1).close()"
        )
        return _docker_quiet("exec", self._container_name, "python3", "-c", probe)


def _proxy_image() -> str:
    """The mitmproxy addon container image (``settings.sandbox_proxy_image``), read at call time."""
    from decode.config.settings import settings

    return settings.sandbox_proxy_image


def _docker(*args: str) -> str:
    """Run ``docker <args>`` to completion; return stdout, raising on a non-zero exit (start path)."""
    completed = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=_DOCKER_CALL_TIMEOUT_S,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"docker {args[0]} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def _docker_quiet(*args: str) -> bool:
    """Run ``docker <args>`` best-effort (teardown / probe path); return whether it exited 0."""
    try:
        completed = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CALL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
