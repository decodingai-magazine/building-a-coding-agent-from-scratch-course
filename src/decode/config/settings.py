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

# Kitaru secret-store hydration flag (ADR-0008 §5): OFF for the import-time singleton (the REPL
# never imports kitaru), ON only for the span of a headless flow via the runtime/flow.py context
# manager. A plain module global, not a ContextVar — everything involved runs on one thread.
_secret_hydration_active = False


def set_secret_hydration_active(active: bool) -> None:
    """Toggle the Kitaru secret-store config source on/off — called only by the ``runtime/flow.py``
    hydration context manager (headless-only; ADR-0008 §5)."""
    global _secret_hydration_active
    _secret_hydration_active = active


def is_secret_hydration_active() -> bool:
    """Whether the Kitaru secret-store config source is currently active (ADR-0008 §5)."""
    return _secret_hydration_active


class KitaruSecretSettingsSource(PydanticBaseSettingsSource):
    """A pydantic-settings source that hydrates fields from a Kitaru secret (ADR-0008 §5).

    Reads ``.env.example``-shaped key/value pairs from the ``runtime_secret_name`` secret and feeds
    the ones mapping to a known field into ``Settings`` — the whole config surface, no per-variable
    code. Two invariants keep it safe in every ``Settings`` build:

    * **Inert unless activated.** :meth:`__call__` returns ``{}`` — and imports no kitaru — unless
      the headless flow flipped :func:`is_secret_hydration_active`.
    * **``Settings`` object only — never ``os.environ``.** Nothing is written to the process env, so
      a model-chosen ``bash`` never inherits a Kitaru-sourced secret.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Unused: :meth:`__call__` is fully overridden, but the abstract base requires a body."""
        raise NotImplementedError(
            "KitaruSecretSettingsSource overrides __call__; get_field_value is never invoked."
        )

    def __call__(self) -> dict[str, Any]:
        if not is_secret_hydration_active():
            # Inert path: every default/interactive build lands here, so kitaru is never imported.
            return {}
        # Lazy import. The secret name comes from the pre-rebuild singleton (env/.env), so the
        # source can never bootstrap itself out of the secret.
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
    # Explicit selector (no auto-detect); the ``gemini`` default keeps existing .env files working.
    llm_provider: Literal["gemini", "openrouter", "modal"] = "gemini"

    # gemini (default): google-genai API-key path.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"

    # openrouter: the default ``openrouter/free`` router spreads across free models and auto-filters
    # for tool-calling, so one congested upstream cannot hard-block with 429s; pin a :free id for a
    # stricter guarantee.
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "openrouter/free"

    # modal Auto Endpoint: OpenAI-compatible ``/v1``. Endpoint vars are DISTINCT from the
    # MODAL_TOKEN_* account tokens; the url is per-user deploy output (used as ``{url}/v1``); proxy
    # tokens are optional (empty = --unauthenticated), both-or-neither.
    modal_endpoint_url: str = ""
    # More on supported models: MODAL_MODELS.md
    modal_endpoint_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    modal_proxy_token_id: SecretStr = SecretStr("")  # Modal-Key: wk-... request header
    modal_proxy_token_secret: SecretStr = SecretStr("")  # Modal-Secret: ws-... request header

    # --- Logging ---
    log_level: str = "INFO"

    # --- Observability: Opik (ADR-0014) ---
    # Presence-based: a set ``opik_api_key`` (also the OTLP Authorization header) turns tracing on;
    # empty → silent no-op, byte-identical (no spans, no network). Export is configured
    # PROGRAMMATICALLY from these fields — never via global OTEL_* env vars — so kitaru→zenml's own
    # OpenTelemetry SDK is untouched (ADR-0014 §2).
    opik_api_key: SecretStr = SecretStr("")
    opik_workspace: str = "default"  # the ``Comet-Workspace`` OTLP header
    opik_project_name: str = "decode"  # the ``projectName`` OTLP header (Opik groups traces by it)
    # The OTLP **base** URL: ``None`` → Comet cloud base; set to a self-hosted Opik base. The
    # exporter appends ``/v1/traces``.
    opik_url_override: str | None = None

    # --- Tool execution / output truncation ---
    bash_timeout_s: float = 120.0
    max_output_lines: int = 2000
    max_output_bytes: int = 50_000
    web_fetch_timeout_s: float = 30.0

    # --- Orchestration (ADR-0003 §8) ---
    # ``sleep(seconds)`` is capped to this value (never rejected) so a model cannot stall a turn.
    sleep_max_s: float = 60.0

    # --- Subagents: the ``agent`` tool + Explore-subagent runner caps (ADR-0013 §7,8) ---
    # Parallel cap enforced by a per-running-loop Semaphore (keep modest — fan-out multiplies model
    # calls); per-child request cap + report truncation via the shared truncate() idiom. All gt=0 —
    # a non-positive value is a misconfiguration and fails fast.
    subagent_max_parallel: int = Field(4, gt=0)
    subagent_max_requests: int = Field(25, gt=0)
    subagent_result_max_bytes: int = Field(16_000, gt=0)

    # --- Memory caps ---
    memory_max_lines: int = 200
    memory_max_bytes: int = 25_000

    # --- Context compaction (ADR-0006): window-relative two-tier cascade. ---
    # ``compaction_enabled`` gates ONLY the automatic cascade; manual ``/compact`` ignores it.
    compaction_enabled: bool = True
    # The active model's MAX *input* window, in tokens — the single source of truth (pydantic-ai
    # exposes no model window, so this number is the contract). Default = Gemini 2.5 Flash.
    compaction_context_window_tokens: int = Field(1_048_576, gt=0)
    # A tier fires when input_tokens >= window * (1 - reserve). INVARIANT: micro reserves more than
    # full so it fires first — ``microcompaction_reserve_fraction > compaction_reserve_fraction``.
    compaction_reserve_fraction: float = Field(0.20, ge=0.0, le=1.0)
    microcompaction_reserve_fraction: float = Field(0.40, ge=0.0, le=1.0)
    # Token budget of the recent tail full compaction keeps verbatim (microcompaction's "recent" cutoff).
    compaction_keep_recent_tokens: int = 20_000
    # When set, the on-exit MEMORY.md LLM compressor runs at the ``memory_max_lines`` cap instead of
    # pure drop-oldest.
    memory_compression_enabled: bool = True

    # --- Harness artifacts: everything decode writes lives under <cwd>/.decode. ---
    decode_dir: Path = Path(".decode")

    # --- Persistence: JSONL session log ---
    sessions_dir: Path = Path(".decode/sessions")

    # --- Permissions: optional {"permissions": {"allow": [...], "deny": [...]}} rules file. ---
    # Missing/malformed is non-fatal (the gate falls back to mode-only).
    permissions_file: Path = Path(".decode/settings.json")

    # --- Skills: project-local skills directory (ADR-0004 §3) ---
    # ``<name>/SKILL.md`` directories keyed by frontmatter ``name`` (directory name cosmetic); a
    # same-``name`` directory overrides a built-in skill. Missing dir → built-ins only.
    skills_dir: Path = Path(".decode/skills")

    # --- LSP / code intelligence (ADR-0007) ---
    # ``lsp_enabled`` master-gates the WHOLE feature (the ``lsp`` tool, the Diagnostics Enricher, any
    # server spawn): ``False`` → no Language Server is ever launched.
    lsp_enabled: bool = True
    # The swappable stdio Language Server (default ``ty server``; ``pylsp`` is a documented drop-in).
    # The spawn is ``[lsp_server_command, *lsp_server_args]`` — keep executable and args split.
    lsp_server_command: str = "ty"
    lsp_server_args: list[str] = ["server"]
    # Gates ONLY the passive Diagnostics Enricher (post-write/edit errors block), independent of the
    # active ``lsp`` tool; both ride ``lsp_enabled``.
    lsp_diagnostics_on_edit: bool = True
    # Per-request best-effort wall-clock timeout (seconds); ``initialize`` is bounded too.
    lsp_request_timeout_s: float = Field(10.0, gt=0)

    # --- Kitaru durable runtime (ADR-0008) ---
    # ``runtime_enabled`` master-gates the WHOLE headless feature: ``False`` → ``decode run`` exits
    # with a friendly line and never builds a Durable Flow.
    runtime_enabled: bool = True
    # Checkpoint granularity. ``"calls"`` (default) makes every run replay-ready — each model/tool
    # call is its own Checkpoint (loop-safe on gemini via the keep-alive-free flow-mode HTTP client,
    # ADR-0010 §3). ``"turn"`` is a cheaper opt-out but replayable only whole. HITL always forces
    # ``"calls"`` (``flow.py``).
    runtime_checkpoint_strategy: Literal["turn", "calls"] = "calls"
    # The durable Wait (HITL) poll timeout (seconds); matches Kitaru's local 600s default.
    runtime_wait_timeout_s: float = Field(600.0, gt=0)
    # Two headless-only consumers of the ONE Kitaru secret named by ``runtime_secret_name``: the
    # model key alone, or the whole config surface. Both default off — the key comes from ``.env``.
    # Neither is the sandbox Credential Proxy (header injection, ADR-0011 §6); these are secret-store
    # *lookups*, and the "Credentials Proxy" name they shipped under was retired by ADR-0008 §5.
    #
    # When ``True``, flow-mode model construction resolves the provider key from that Kitaru secret
    # instead of settings — a deployed flow payload carries handles, not raw keys (ADR-0008 §5).
    runtime_secret_store_model_key: bool = False
    # The Kitaru secret both consumers below read from.
    runtime_secret_name: str = "decode-llm-creds"
    # When ``True``, a headless ``decode run`` hydrates the WHOLE ``Settings`` surface from the
    # ``runtime_secret_name`` secret via :class:`KitaruSecretSettingsSource` — a superset of the
    # model-key lookup above. Values land in this ``Settings`` object ONLY — never ``os.environ`` —
    # and the real process env still overrides them. Headless-only, so bare ``decode`` never imports
    # kitaru.
    runtime_secret_store_config: bool = False

    # --- Sandboxing (ADR-0012; ADR-0011 §1,§5-7 retained) ---
    # ``sandbox_mode`` selects the ``CommandExecutor`` for the whole process (chosen once at
    # startup); ``none`` (default) keeps the host ``LocalExecutor``, byte-unchanged. An unavailable
    # backend is a friendly startup / pre-flight error (presence, not correctness).
    sandbox_mode: Literal["none", "docker", "modal"] = "none"
    # The worker image (docker pulls it; modal maps it via ``Image.from_registry``). Must include
    # ``bash``. The uv variant of python-slim means both sandboxes run python via ``uv`` out of the
    # box (skill payloads say ``uv run …``). git is NOT in the slim base, so each backend adds it.
    sandbox_image: str = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
    # The git identity preconfigured in the sandbox so a model ``git commit`` succeeds. Defaults to
    # the same identity the hand-back stamps its capture commit with (``sandbox/handback.py``);
    # override to author as yourself or a bot, set both empty to skip.
    sandbox_git_user_name: str = "decode"
    sandbox_git_user_email: str = "decode@localhost"
    # The one git token for BOTH sandboxes' git push / PRs (ADR-0012 §10) — the deliberate docker =
    # Credential Proxy (worker token-free, header injected after egress; auto-engages when non-empty)
    # vs modal = direct injection (``GITHUB_TOKEN`` via ``modal.Secret``, readable in-sandbox)
    # trade-off. Empty injects nothing — rely on the host-side hand-back. Because modal keeps it
    # in-sandbox, use a fine-grained PAT scoped to the target repo, never a broad classic token.
    sandbox_git_token: SecretStr | None = None
    # The HOST directory bind-mounted at the docker Worker's ``/workspace`` — it IS the isolated
    # Workspace. File tools operate on it THROUGH the backend seam, never on the host repo tree;
    # skills are seeded in host-side by ``seed_skills``, not mounted.
    sandbox_workspace_dir: Path = Path(".decode/sandbox")
    # The repo cloned host-side into the Workspace at launch (URL or local path, ambient git creds);
    # empty → an empty Workspace. ``--repo`` overrides it; consumed only in a sandbox mode (ADR-0012 §3).
    sandbox_repo: str = ""
    # Max lifetime (seconds) of a REMOTE (modal) sandbox before Modal reaps it; docker's session
    # container has no lifetime cap (``sleep infinity``).
    sandbox_timeout_s: float = Field(600.0, gt=0)
    # Enable the headless + docker-only Credential Proxy (ADR-0011 §6): a mitmproxy container injects
    # tool credentials AFTER a request leaves the worker, so the worker never holds a secret. Opt-in.
    sandbox_credential_proxy_enabled: bool = False
    # The mitmproxy addon container image the Credential Proxy runs.
    sandbox_proxy_image: str = "mitmproxy/mitmproxy"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the Kitaru secret-store source below env, above .env (ADR-0008 §5).

        Precedence is left-to-right: the real process env wins over the Kitaru secret, which wins
        over ``.env`` / defaults. The source is inert unless a headless flow activated it, so this
        ordering is a no-op for the REPL and every default build.
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

    Mutates the existing object rather than rebinding the name, so every reader that did
    ``from decode.config.settings import settings`` sees the fresh config. Called by the headless
    hydration context manager with the secret source active. Verified to emit zero warnings under
    ``filterwarnings=["error"]`` on pydantic v2.
    """
    fresh = Settings()
    settings.__dict__.update(fresh.__dict__)
    settings.__pydantic_fields_set__.clear()
    settings.__pydantic_fields_set__.update(fresh.__pydantic_fields_set__)
    return settings
