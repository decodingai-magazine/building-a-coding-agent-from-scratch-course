"""Application configuration via pydantic-settings.

Import the module-level ``settings`` singleton where you need configuration; never read
``os.environ`` deep in call sites. Every variable here is mirrored in ``.env.example``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)

# --- Kitaru secret-store config source: headless-only hydration flag (ADR-0008 §5, task 064) ---
# A module-level switch the :class:`KitaruSecretSettingsSource` consults on every ``Settings()``
# build. It is OFF for the import-time singleton (so the interactive REPL never imports kitaru) and
# turned ON only for the span of a headless ``decode run`` flow by the ``runtime/flow.py`` context
# manager, which rebuilds the singleton in place while it is active and restores it on exit. Kept a
# plain module global (not a ContextVar): the flow body, the in-place rebuild, and the source all run
# on the same thread, so the simplest thing that works is the right one here.
_secret_hydration_active = False


def set_secret_hydration_active(active: bool) -> None:
    """Toggle the Kitaru secret-store config source on/off (headless-only; ADR-0008 §5).

    Called only by the ``runtime/flow.py`` hydration context manager: ``True`` before it rebuilds the
    ``settings`` singleton (so :class:`KitaruSecretSettingsSource` reads the secret), ``False`` in its
    ``finally`` (so a later in-process ``Settings()`` build — the next test, or a subsequent flow —
    sees an inert source and does not import kitaru).
    """
    global _secret_hydration_active
    _secret_hydration_active = active


def is_secret_hydration_active() -> bool:
    """Whether the Kitaru secret-store config source is currently active (ADR-0008 §5)."""
    return _secret_hydration_active


class KitaruSecretSettingsSource(PydanticBaseSettingsSource):
    """A pydantic-settings source that hydrates fields from a Kitaru secret (ADR-0008 §5, task 064).

    Generalizes the task-061 single-key proxy into a whole-surface config source: when active it
    reads the ``.env.example``-shaped key/value pairs out of the Kitaru secret named by
    ``settings.runtime_secret_name`` and feeds the ones that map to a known field into ``Settings``.
    Because ``config/settings.py`` already maps every ``.env.example`` var to a field, this covers the
    entire config surface (provider/model/keys/tuning) with **no per-variable code**.

    Two invariants make it safe to keep wired into every ``Settings`` build:

    * **Inert unless activated.** :meth:`__call__` returns ``{}`` immediately — and crucially imports
      **no kitaru** — unless :func:`is_secret_hydration_active` is ``True`` (only the headless flow
      flips it). So the interactive REPL, which builds the singleton at import, never pulls in kitaru.
    * **`Settings` object only — never `os.environ`.** It returns a value mapping pydantic validates
      into the model; it writes nothing to the process env, so a model-chosen ``bash`` never inherits
      a Kitaru-sourced secret. This is the line between this source and the deferred sandbox
      Credential Proxy (mitmproxy header injection), which is out of scope here.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Unused: :meth:`__call__` is fully overridden, but the abstract base requires a body."""
        raise NotImplementedError(
            "KitaruSecretSettingsSource overrides __call__; get_field_value is never invoked."
        )

    def __call__(self) -> dict[str, Any]:
        if not is_secret_hydration_active():
            # Inert path: the REPL singleton and every default/interactive build land here, so kitaru
            # is never imported off the headless flow (the REPL-safety invariant).
            return {}
        # Lazy import so this module stays kitaru-free until a headless flow actually activates the
        # source. ``settings`` is the current (pre-rebuild) singleton, so the secret name comes from
        # env/.env exactly as configured — it can never bootstrap itself out of the secret.
        from kitaru import get_secret

        values = get_secret(settings.runtime_secret_name).values
        known = self.settings_cls.model_fields
        hydrated = {key.lower(): value for key, value in values.items() if key.lower() in known}
        logger.debug(
            "hydrated %d field(s) from Kitaru secret %r: %s",
            len(hydrated),
            settings.runtime_secret_name,
            sorted(hydrated),  # field NAMES only — never the values (they may be secrets)
        )
        return hydrated


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

    # --- Kitaru durable runtime (ADR-0008) ---
    # The Headless Runtime config surface (``decode run`` / ``runtime/``); settings only here — no
    # readers yet (they land in tasks 058/059/061). ``runtime_enabled`` master-gates the WHOLE headless
    # feature: ``False`` → ``decode run`` exits with a friendly line and never builds a Durable Flow.
    runtime_enabled: bool = True
    # ``KitaruAgent`` Checkpoint granularity. Default ``"turn"`` (one Checkpoint per agent turn) — cheap.
    # ``"calls"`` (per model/tool call) records finer Replay anchors — enough to anchor a Replay before a
    # specific model call — at the cost of more Checkpoints per run. Both are loop-safe on a real
    # provider: flow mode builds a keep-alive-free HTTP client so Kitaru's per-call event loops never
    # reuse a connection across loops (``_flow_mode_http_client``, ADR-0010 §3). ``"turn"`` is the
    # default for cost, not safety; opt into ``"calls"`` for granular replay. HITL always forces
    # ``"calls"`` regardless (``flow.py``).
    runtime_checkpoint_strategy: Literal["turn", "calls"] = "turn"
    # The durable Wait (HITL) poll timeout (seconds) in flow mode; matches Kitaru's local 600s default.
    # A non-positive value fails fast (Field gt=0). Task 059 reads it.
    runtime_wait_timeout_s: float = Field(600.0, gt=0)
    # When ``True``, flow-mode model construction resolves the provider API key through the Kitaru
    # Credentials Proxy (Kitaru secrets) instead of reading the ``SecretStr`` from settings — so a
    # deployed flow payload carries handles, not raw keys. Default ``False``: the secrets-proxy surface
    # is the least-exampled in Kitaru (ADR-0008 §5) and must be verified first (task 061 reads it).
    runtime_credentials_proxy_enabled: bool = False
    # The Kitaru secret name the Credentials Proxy reads the provider key from when enabled (task 061).
    runtime_secret_name: str = "decode-llm-creds"
    # When ``True``, a headless ``decode run`` flow hydrates the WHOLE ``Settings`` surface from the
    # ``runtime_secret_name`` Kitaru secret (any ``.env.example`` var: LLM_PROVIDER, GEMINI_MODEL,
    # OPENROUTER_*/MODAL_*, OPIK_API_KEY, tuning, …) via :class:`KitaruSecretSettingsSource`. Values
    # land in this ``Settings`` object ONLY — never ``os.environ`` — and the real process env still
    # overrides them. Default ``False`` and **headless-only**: the import-time singleton has the
    # hydration flag off, so bare ``decode`` never imports kitaru (task 064 reads it). This is the
    # secret-store config source, NOT the deferred sandbox Credential Proxy (header injection).
    runtime_secret_store_config: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the Kitaru secret-store source below env, above .env (ADR-0008 §5, task 064).

        Precedence is left-to-right (earlier = higher priority), so the Kitaru source sits between
        ``env_settings`` and ``dotenv_settings``: a value in the **real process env wins over the
        Kitaru secret**, and the **Kitaru secret wins over ``.env`` / field defaults**. The source is
        inert (returns ``{}``, imports no kitaru) unless a headless flow has activated it, so this
        ordering is a no-op for the interactive REPL and every default build.
        """
        return (
            init_settings,
            env_settings,
            KitaruSecretSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


settings = Settings()


def reload_settings() -> Settings:
    """Rebuild the module-level ``settings`` singleton **in place** from its sources (ADR-0008 §5).

    The headless ``decode run`` flow calls this (through the ``runtime/flow.py`` hydration context
    manager, with :func:`set_secret_hydration_active` on) so ``build_agent`` and every other reader
    that did ``from decode.config.settings import settings`` sees the freshly hydrated config — the
    rebuild mutates the existing object rather than rebinding the name, so those shared references
    update too. A fresh :class:`Settings` is constructed (re-reading env, the Kitaru secret when the
    source is active, ``.env``, and defaults), then its field values are copied into the singleton.
    Verified to emit **zero warnings** under ``filterwarnings=["error"]`` on pydantic v2.
    """
    fresh = Settings()
    settings.__dict__.update(fresh.__dict__)
    settings.__pydantic_fields_set__.clear()
    settings.__pydantic_fields_set__.update(fresh.__pydantic_fields_set__)
    return settings
