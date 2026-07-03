"""mitmproxy addon for the sandbox Credential Proxy — header injection per host (ADR-0011 §6).

Loaded by ``mitmdump`` running **inside** the ``mitmproxy/mitmproxy`` proxy container
(``mitmdump ... -s /opt/proxy_addon.py``), never by the decode host process. It is mounted
**read-only** into the container, so it must stay tiny and self-contained: **stdlib + mitmproxy
only — it imports no ``decode`` and no ``kitaru``** (neither is installed in that image).

The credential map reaches this addon through the container's **own** environment as JSON
(``DECODE_CREDENTIAL_MAP`` = ``{host: {header: value}}``), resolved host-side at flow start by
:func:`decode.sandbox.proxy.build_credential_map` and handed **only** to the proxy container's env —
never the worker's (AGENTS.md: *secrets never reach the model or the sandbox payload*). On each
request the addon matches the request host against the map and, on a hit, sets the configured
headers **after** the request has left the token-free worker — so the worker makes an authenticated
call while holding no secret.

Egress is **cooperative** (`ponytail:` the worker is *pointed* at this proxy via ``http_proxy`` /
``https_proxy`` and trusts its CA; it is not prevented from talking directly to the internet — an
internal-only network with default-deny egress is the upgrade path). The credential claim (the
worker never holds a token) holds regardless.
"""

import json
import os

from mitmproxy.http import HTTPFlow

# The credential map the host handed this container: ``{host: {header: value}}`` (already resolved —
# no ``{{ secret }}`` templates remain). Read from the proxy container's OWN env; the worker never
# sees this var. An absent/empty var means a passthrough proxy that injects nothing (the default when
# ``DEFAULT_PROXY_RULES`` ships empty).
_CREDENTIALS: dict[str, dict[str, str]] = json.loads(os.environ.get("DECODE_CREDENTIAL_MAP", "{}"))

if _CREDENTIALS:
    print(f"[decode-proxy] credentials loaded for hosts: {sorted(_CREDENTIALS)}", flush=True)
else:
    print(
        "[decode-proxy] no credential map (DECODE_CREDENTIAL_MAP empty) — passthrough, no injection",
        flush=True,
    )


def _match_host(host: str) -> dict[str, str] | None:
    """Return the headers to inject for ``host`` (exact or parent-domain match), or ``None``.

    ``host`` matches a map key when it is that key exactly or a sub-domain of it (``api.github.com``
    matches a ``github.com`` rule), both compared case-insensitively with any trailing dot stripped —
    the same normalization the request host and the configured pattern get.
    """
    normalized = host.rstrip(".").lower()
    for pattern, headers in _CREDENTIALS.items():
        normalized_pattern = pattern.rstrip(".").lower()
        if normalized == normalized_pattern or normalized.endswith(f".{normalized_pattern}"):
            return headers
    return None


class CredentialAddon:
    """Inject the configured headers on every outbound request whose host matches the map."""

    def request(self, flow: HTTPFlow) -> None:
        """mitmproxy request hook: set the matched host's headers on the outbound request."""
        host = flow.request.pretty_host
        headers = _match_host(host)
        if headers is None:
            print(
                f"[decode-proxy] no match for host={host!r} known={sorted(_CREDENTIALS)}",
                flush=True,
            )
            return
        for header_name, header_value in headers.items():
            flow.request.headers[header_name] = header_value
        # Log the header NAMES only — never the injected values (they are secrets).
        print(f"[decode-proxy] injected headers for {host}: {sorted(headers)}", flush=True)


addons = [CredentialAddon()]
