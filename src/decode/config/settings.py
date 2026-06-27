"""Application configuration via pydantic-settings.

Import the module-level ``settings`` singleton where you need configuration; never read
``os.environ`` deep in call sites. Every variable here is mirrored in ``.env.example``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    # More on supported models: MODAL_MODELS.md
    modal_endpoint_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
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

    # --- Context compaction (ADR-0006) ---
    # Window-relative two-tier cascade; settings only here (no readers yet — tasks 042/044/046/047).
    # ``compaction_enabled`` gates ONLY the automatic cascade; manual ``/compact`` (task 045) ignores it.
    compaction_enabled: bool = True
    # The active model's MAX *input* context window, in tokens — the single source of truth (also the
    # TUI fill gauge, task 047). Default = Gemini 2.5 Flash's input window; set this to YOUR active
    # model's input window. pydantic-ai exposes no model window, so this number is the contract.
    compaction_context_window_tokens: int = Field(1_048_576, gt=0)
    # Per-tier reserve fractions: a tier fires when input_tokens >= window * (1 - reserve). Full fires
    # at 80% full; micro fires EARLIER at 60% full. INVARIANT: micro reserves more than full so it fires
    # first — ``microcompaction_reserve_fraction > compaction_reserve_fraction`` (asserted on defaults).
    compaction_reserve_fraction: float = Field(0.20, ge=0.0, le=1.0)
    microcompaction_reserve_fraction: float = Field(0.40, ge=0.0, le=1.0)
    # Token budget of the recent tail full compaction keeps verbatim, and the cutoff microcompaction
    # treats as "recent" (snapped to a turn boundary by task 042).
    compaction_keep_recent_tokens: int = 20_000
    # Second level: when set, the on-exit MEMORY.md LLM compressor (task 046) runs at the
    # ``memory_max_lines`` cap instead of pure drop-oldest. Reuses the existing memory caps — no new ones.
    memory_compression_enabled: bool = True

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

    # --- LSP / code intelligence (ADR-0007) ---
    # The code-intelligence surface; settings only here (no readers yet — tasks 051/052/053/054).
    # ``lsp_enabled`` is the master gate for the WHOLE feature (the ``lsp`` tool, the Diagnostics
    # Enricher, and any server spawn). When ``False`` no Language Server is ever launched.
    lsp_enabled: bool = True
    # The swappable stdio Language Server. Default spawns ``ty server`` (Astral's type-checker, same
    # vendor as ruff/uv); a drop-in is documented (``pylsp`` or any stdio LSP server). The spawn is
    # ``[lsp_server_command, *lsp_server_args]`` — keep the executable and its args split.
    lsp_server_command: str = "ty"
    lsp_server_args: list[str] = ["server"]
    # Gates ONLY the passive Diagnostics Enricher (task 053) — the errors-only block appended after a
    # successful ``.py`` write/edit. Independent of the active ``lsp`` tool; both ride ``lsp_enabled``.
    lsp_diagnostics_on_edit: bool = True
    # Per-request best-effort wall-clock timeout (seconds); the server's ``initialize`` is bounded too.
    # The lazy single spawn per root amortizes the cost. A non-positive value fails fast (Field gt=0).
    lsp_request_timeout_s: float = Field(10.0, gt=0)


settings = Settings()
