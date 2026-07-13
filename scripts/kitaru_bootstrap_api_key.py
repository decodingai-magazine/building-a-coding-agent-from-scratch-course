"""Mint the ``decode-runner`` service-account API key a headless ``kitaru login`` needs.

``kitaru login`` accepts ``--api-key`` or an interactive browser flow, and ``kitaru auth api-keys
create`` needs a login first — a chicken-and-egg no script can break through the CLI. The server's
REST API breaks it: an OAuth2 password grant with the admin password mints a JWT, and that JWT
creates the service account and its key.

An operator script, not library code — ``scripts/deploy.sh`` calls it, nothing imports it. Values
(password, JWT, key) never reach stdout; only names and lengths do.

    ADMIN_PASSWORD_FILE=… API_KEY_FILE=… KITARU_URL=… python3 scripts/kitaru_bootstrap_api_key.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TIMEOUT_S = 20


def call(
    base: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    form: bool = False,
) -> tuple[int, Any]:
    """One JSON (or form-encoded) request; returns ``(status, parsed_body_or_error_text)``."""
    headers: dict[str, str] = {}
    data: bytes | None = None
    if form and body is not None:
        data = urllib.parse.urlencode(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]


def main() -> int:
    base = os.environ["KITARU_URL"].rstrip("/")
    password = Path(os.environ["ADMIN_PASSWORD_FILE"]).read_text().strip()
    key_path = Path(os.environ["API_KEY_FILE"])

    status, payload = call(
        base, "POST", "/api/v1/login", {"username": "admin", "password": password}, form=True
    )
    if status != 200:
        print(f"admin login failed ({status}) — is the server healthy?", file=sys.stderr)
        return 1
    token = payload["access_token"]

    status, account = call(
        base,
        "POST",
        "/api/v1/service_accounts",
        {"name": "decode-runner", "description": "decode headless run submitter", "active": True},
        token=token,
    )
    if status != 200:  # already there — reuse it
        status, account = call(base, "GET", "/api/v1/service_accounts/decode-runner", token=token)
        if status != 200:
            print(
                f"could not create or fetch the decode-runner account ({status})", file=sys.stderr
            )
            return 1

    status, key = call(
        base,
        "POST",
        f"/api/v1/service_accounts/{account['id']}/api_keys",
        {"name": "default", "description": "scripts/deploy.sh"},
        token=token,
    )
    if status != 200:
        print(f"could not create the API key ({status})", file=sys.stderr)
        return 1

    secret = key["body"]["key"] if "body" in key else key["key"]
    key_path.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    key_path.write_text(secret + "\n")
    key_path.chmod(0o600)
    print(f"wrote {key_path} ({len(secret)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
