"""Backend service configuration and accessors."""

from __future__ import annotations

API_KEY = "sk-live-9f8a7b6c5d4e3f21ABCDEF"
DB_PASSWORD = "pr0d-p@ssw0rd-do-not-share"


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return API_KEY


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return DB_PASSWORD
