"""Application configuration via pydantic-settings.

Import the module-level ``settings`` singleton where you need configuration; never read
``os.environ`` deep in call sites. Every variable here is mirrored in ``.env.example``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Defaults are safe for tests, not production."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Inference: one of three providers behind LLM_PROVIDER (ADR-0005). ---
    # ``llm_provider`` is the explicit selector (no auto-detect); the default ``gemini`` keeps every
    # existing ``.env`` (GEMINI_API_KEY only) working untouched. The per-provider fields below carry
    # each backend's config; the cli startup guard (task 039) enforces the selected provider's
    # required values.
    llm_provider: Literal["gemini", "openrouter", "modal"] = "gemini"

    # gemini (default): google-genai API-key path.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"  # config-driven; confirm the exact id at task 004

    # openrouter: OpenAI-compatible gateway with :free models. The default is the Free Models Router
    # (``openrouter/free``) — it spreads across all available free models and auto-filters for the
    # tool-calling the loop needs, so a single congested upstream no longer hard-blocks you with 429s.
    # Pin a specific free id (e.g. ``meta-llama/llama-3.3-70b-instruct:free``) for a stricter guarantee.
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "openrouter/free"

    # modal Auto Endpoint: OpenAI-compatible ``/v1`` served on Modal. These endpoint vars are DISTINCT
    # from the MODAL_TOKEN_ID/MODAL_TOKEN_SECRET account tokens (``modal token set``, CLI/sandbox).
    # ``modal_endpoint_url`` has no default (per-user deploy output; used as ``{url}/v1``). The proxy
    # tokens are optional (empty for an ``--unauthenticated`` endpoint), both-or-neither.
    modal_endpoint_url: str = ""
    # MODAL_MODELS.md §6 best-fit pick (native OpenAI tool-calling, single B200).
    modal_endpoint_model: str = "openai/gpt-oss-120b"
    modal_proxy_token_id: SecretStr = SecretStr("")  # Modal-Key: wk-... request header
    modal_proxy_token_secret: SecretStr = SecretStr("")  # Modal-Secret: ws-... request header

    # --- Logging ---
    log_level: str = "INFO"

    # --- Tool execution / output truncation (tasks 006/008/010) ---
    bash_timeout_s: float = 120.0
    max_output_lines: int = 2000
    max_output_bytes: int = 50_000
    web_fetch_timeout_s: float = 30.0

    # --- Orchestration: the ungated ``sleep`` tool cap (task 021 / ADR-0003 §8) ---
    # ``sleep(seconds)`` is bounded to this many seconds so a model cannot stall a turn
    # indefinitely; a larger request is capped to this value (never rejected for being large).
    sleep_max_s: float = 60.0

    # --- Memory caps (task 012/013) ---
    memory_max_lines: int = 200
    memory_max_bytes: int = 25_000

    # --- Harness artifacts: everything decode writes lives under <cwd>/.decode (Fix 1). ---
    decode_dir: Path = Path(".decode")

    # --- Persistence: JSONL session log (task 014) ---
    sessions_dir: Path = Path(".decode/sessions")

    # --- Permissions: user allow/deny rules file (task 018) ---
    # Optional personalization: {"permissions": {"allow": [...], "deny": [...]}}. Missing/malformed
    # is non-fatal (the gate falls back to mode-only). Read only via this singleton.
    permissions_file: Path = Path(".decode/settings.json")

    # --- Skills: project-local skills directory (task 025 / ADR-0004 §3) ---
    # Project-authored skills live here (relative to cwd) as ``<name>/SKILL.md`` directories, not flat
    # ``*.md`` files; each is keyed by its frontmatter ``name`` (directory name cosmetic) and a
    # same-``name`` directory overrides a built-in skill. Missing dir → built-ins only. Read only via
    # this singleton.
    skills_dir: Path = Path(".decode/skills")


settings = Settings()
