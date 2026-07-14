"""A stdlib ``http.server`` fixture serving one known page on localhost (ADR-0017 §6).

The web-fetch probes need a page the agent can retrieve deterministically — no live internet, no
flakiness. :func:`serve_page` runs a throwaway :class:`~http.server.HTTPServer` on a background thread
serving one fixed body for every path, and yields the base URL. It is a context manager the probe (or
the harness, via ``RegressionProbe.context``) enters around the run so the server is alive for the
fetch and torn down — thread joined, socket closed — the moment the run ends.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

# The default page body served when a probe does not supply its own — a known, greppable marker.
DEFAULT_PAGE = "<html><body><h1>decode regression fixture page</h1></body></html>"


@contextmanager
def serve_page(body: str = DEFAULT_PAGE, *, host: str = "127.0.0.1") -> Iterator[str]:
    """Serve ``body`` for every path on an ephemeral localhost port; yield the base URL (ADR-0017 §6).

    Binds ``host:0`` (the OS picks a free port — no fixed-port collisions across parallel probes),
    serves ``body`` as ``text/html`` for any GET, and yields ``http://<host>:<port>``. On exit the
    server is shut down, the serving thread joined and the socket closed, so no listener or thread
    leaks past the ``with`` block (``filterwarnings=error`` would flag a leaked socket).
    """
    encoded = body.encode("utf-8")

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args: object) -> None:
            """Silence the default stderr access log — the eval harness owns the output surface."""

    server = HTTPServer((host, 0), _Handler)
    thread = threading.Thread(
        target=server.serve_forever, name="regression-http-server", daemon=True
    )
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
