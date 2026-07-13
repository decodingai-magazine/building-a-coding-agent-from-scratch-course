"""The sandbox Credential Proxy — host-side rules + a mitmproxy container (ADR-0011 §6).

Headless + docker only: lets a sandboxed **Worker** make authenticated tool calls while holding
**no** secret. Rule templates are resolved host-side at flow start into a ``{host: {header: value}}``
map handed **only** to the proxy container's env — never the worker's; the mitmproxy addon injects
the headers after a request leaves the token-free worker. :data:`DEFAULT_PROXY_RULES` ships **empty**
= opt-in (an empty map is a passthrough proxy).

A template names a **Settings field** (``{{ sandbox_git_token }}``) and :func:`build_credential_map`
is a **pure function of the hydrated** ``settings`` singleton — no secret store, no network (ADR-0015
§6, amending ADR-0011 §6). At a remote ``DECODE_ENV`` that field's value arrived via the Environment
Bucket, but this module neither knows nor cares: ``Settings`` is the single source of truth, and the
Environment-Bucket settings source is the codebase's ONLY secret-store seam — this package has none
(the module is deliberately grep-clean of it).

`ponytail:` egress is cooperative — the worker is *pointed* at the proxy, not forced through it;
not an exfiltration barrier, but the worker-never-holds-a-token claim holds regardless.
"""

from __future__ import annotations

import base64
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

from pydantic import SecretStr

logger = logging.getLogger(__name__)

# ``{{ field_name }}`` — a ``Settings`` FIELD name, resolved host-side from the hydrated singleton.
_TEMPLATE_RE = re.compile(r"\{\{\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class SandboxProxyRule:
    """One credential-injection rule: inject ``headers`` on every request to any of ``hosts``.

    Header values may embed ``{{ field_name }}`` templates naming a ``Settings`` **field**, which
    :func:`build_credential_map` resolves host-side from the hydrated config — at a remote
    ``DECODE_ENV`` that value reached ``Settings`` through the Environment Bucket (ADR-0015 §6).
    """

    name: str
    hosts: list[str]
    headers: dict[str, str]


# Ships EMPTY = opt-in; an empty map is a passthrough proxy. Example — let the sandboxed agent call an
# internal API while holding NO token. A template names a ``Settings`` FIELD, so the credential rides
# the ONE config surface (``.env`` at ``local``, the Environment Bucket at a remote ``DECODE_ENV``) —
# there is no separate secret to create for a proxy rule (ADR-0015 §6):
#
#     # 1. add the field to ``config/settings.py`` (+ ``.env.example``):
#     #        acme_api_token: SecretStr = SecretStr("")
#     # 2. name it in a rule here:
#     DEFAULT_PROXY_RULES = [
#         SandboxProxyRule(
#             name="acme-api",
#             hosts=["api.acme.test"],
#             headers={"Authorization": "Bearer {{ acme_api_token }}"},
#         ),
#     ]
#     # 3. run headless docker (the worker never holds the token):
#     #        ACME_API_TOKEN=<token> SANDBOX_CREDENTIAL_PROXY_ENABLED=true \
#     #            SANDBOX_MODE=docker decode run "…call the acme API…"
#
# Rule ORDER is significant: ``proxy_addon._match_host`` returns the FIRST match and a parent host
# matches its subdomains, so a specific host (``api.github.com``) must precede its parent (``github.com``).
#
# `ponytail:` for the GitHub push+PR case, ``SANDBOX_GIT_TOKEN`` is the one-knob shortcut —
# :func:`github_token_rules` builds those two rules (Bearer + Basic) and ``_sandbox_proxy``
# auto-engages the proxy. Its values are LITERAL, not templates, precisely because the git transport
# wants a base64 Basic transform — a computation a template cannot express, which is why it is code.
# ``DEFAULT_PROXY_RULES`` remains the general path for any OTHER host.
DEFAULT_PROXY_RULES: list[SandboxProxyRule] = []


def github_token_rules(token: str) -> list[SandboxProxyRule]:
    """Build the GitHub push+PR proxy rules from one PAT — the ``SANDBOX_GIT_TOKEN`` convenience path.

    Bearer for the REST API (``api.github.com``); Basic ``base64("x-access-token:<PAT>")`` for git
    push over HTTPS (GitHub's git transport rejects Bearer). ``api.github.com`` comes **first** so
    the addon's first-match wins over the ``github.com`` parent match. Values are literal (the base64
    transform is exactly why this is code, not a ``{{ field }}`` template) and the worker still holds
    no token — the proxy injects after egress (ADR-0012 §10).
    """
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        SandboxProxyRule(
            name="github-api",
            hosts=["api.github.com"],
            headers={"Authorization": f"Bearer {token}"},
        ),
        SandboxProxyRule(
            name="github-git",
            hosts=["github.com"],
            headers={"Authorization": f"Basic {basic}"},
        ),
    ]


def _settings_value(field: str) -> str:
    """The hydrated ``settings.<field>`` as a header-ready string — loudly, or not at all (ADR-0015 §6).

    ``SecretStr`` is unwrapped (a bare ``str()`` would inject ``'**********'``); any other field is
    stringified. An unknown field or an empty/``None`` value **raises**: a silent skip would send an
    unauthenticated request while the run looked fine — the mirror of the old missing-secret contract.
    """
    from decode.config.settings import Settings, settings

    if field not in Settings.model_fields:
        raise ValueError(
            f"proxy rule template {{{{ {field} }}}} names {field!r}, which is not a Settings field — "
            "add it to decode/config/settings.py (and .env.example) or fix the template"
        )
    value = getattr(settings, field)
    resolved = value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")
    if not resolved:
        raise ValueError(
            f"proxy rule template {{{{ {field} }}}} resolved to an EMPTY value — set {field.upper()} "
            "(in .env, the process env, or the Environment Bucket); the proxy must never inject an "
            "empty credential header"
        )
    return resolved


def build_credential_map(rules: list[SandboxProxyRule]) -> dict[str, dict[str, str]]:
    """Resolve ``rules`` into the ``{host: {header: value}}`` map the proxy container consumes.

    A **pure function of the hydrated** ``settings`` — each ``{{ field_name }}`` template reads a
    Settings field (ADR-0015 §6): no secret store, no network. Runs host-side at flow start (ADR-0011 §6);
    rules on the same host merge; empty ``rules`` yields ``{}`` (a passthrough proxy). Logs
    rule/host/header **names** only — never the values.
    """
    result: dict[str, dict[str, str]] = {}
    for rule in rules:
        resolved = {
            header: _TEMPLATE_RE.sub(lambda m: _settings_value(m.group("field")), value)
            for header, value in rule.headers.items()
        }
        for host in rule.hosts:
            result.setdefault(host, {}).update(resolved)
        # NAMES only — never the resolved values (they are secrets).
        logger.debug(
            "[sandbox] proxy rule %r resolved (hosts=%s, headers=%s)",
            rule.name,
            rule.hosts,
            sorted(rule.headers),
        )
    return result


# --- DockerCredentialProxy: the mitmproxy container topology ----------------------------------

# The standalone addon (this module's sibling) is mounted read-only and passed to ``mitmdump -s``.
_ADDON_HOST_PATH = Path(__file__).parent / "proxy_addon.py"
_ADDON_CONTAINER_PATH = "/opt/proxy_addon.py"
# mitmproxy's confdir, bind-mounted to a host temp dir so the generated CA is readable host-side.
_PROXY_CONFDIR = "/certs"
_CA_FILENAME = "mitmproxy-ca-cert.pem"
# The env var the addon reads the credential map (JSON) from — set on the PROXY container ONLY.
_CREDENTIAL_MAP_ENV = "DECODE_CREDENTIAL_MAP"
_LISTEN_PORT = 8080
# A DECOY ``GH_TOKEN`` for the worker (ADR-0012 §10). ``gh`` refuses to make any request at all when
# it finds no token in the env — it fails locally with "gh auth login", never reaching the proxy that
# would have authenticated it. So the worker is handed a placeholder: ``gh`` proceeds, sends
# ``Authorization: token <placeholder>``, and the addon **overwrites** that header with the real
# credential after the request has left the worker (mitmproxy ``headers[name] = value`` replaces, it
# does not append). The invariant holds — this string is not a credential and authenticates nothing.
_GH_PLACEHOLDER_TOKEN = "decode-proxy-injects-the-real-token"
# Readiness-wait bounds (CA written + listen port answering); a stall past this is a real failure.
_READY_TIMEOUT_S = 20.0
_READY_POLL_S = 0.2
_DOCKER_CALL_TIMEOUT_S = 30.0
_STOP_TIMEOUT_S = 2


class DockerCredentialProxy:
    """A ``mitmproxy/mitmproxy`` addon container that injects tool credentials per host (ADR-0011 §6).

    :meth:`start` creates a per-run docker network + the proxy container (credential map in its
    **own** env, CA written to a host-shared dir) and waits until ready; :meth:`stop` tears it all
    down best-effort, safe even after a partial start. Uniquely named per instance so concurrent
    runs never collide. Docker access is CLI-only via sync ``subprocess``, mirroring
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
        """The ``http_proxy`` / ``https_proxy`` env pointing the worker at this proxy container.

        Both casings plus a loopback ``no_proxy``; these carry **no** secret — just the proxy URL.
        Plus :data:`_GH_PLACEHOLDER_TOKEN` as ``GH_TOKEN``, which is a **deliberate decoy** — see that
        constant. The worker still holds no real credential.
        """
        url = f"http://{self._container_name}:{_LISTEN_PORT}"
        return {
            "http_proxy": url,
            "https_proxy": url,
            "HTTP_PROXY": url,
            "HTTPS_PROXY": url,
            "no_proxy": "localhost,127.0.0.1",
            "NO_PROXY": "localhost,127.0.0.1",
            "GH_TOKEN": _GH_PLACEHOLDER_TOKEN,
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
        # World-writable so mitmdump (non-root in the container) can write its CA into the mounted
        # confdir; no secret lands here — the credential map rides an ``--env-file``.
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

        A ``0600`` temp file deleted once ``docker run`` has consumed it, so the resolved secret
        never appears in the argv (host ``ps``) nor lingers on disk; it lands only in the **proxy**
        container's env — never the worker's.
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
            # Consumed by ``docker run`` — delete immediately so the on-disk lifetime is milliseconds.
            with contextlib.suppress(OSError):
                os.unlink(env_path)

    def stop(self) -> None:
        """Tear the proxy down — stop the container, remove the network + temp dir (best-effort)."""
        if self._container_id is not None:
            logger.info("[sandbox] proxy stop %s", self._container_id[:12])
        _docker_quiet("stop", "--time", str(_STOP_TIMEOUT_S), self._container_name)
        # The daemon's network-endpoint cleanup can lag container removal — a bounded retry keeps
        # the "network is gone after teardown" guarantee.
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
