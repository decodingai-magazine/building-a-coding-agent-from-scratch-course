"""Backend service configuration and accessors."""

from __future__ import annotations

import os


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return os.environ["API_KEY"]


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return os.environ["DB_PASSWORD"]
