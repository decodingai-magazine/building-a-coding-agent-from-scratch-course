"""Application configuration via pydantic-settings.

Import the module-level ``settings`` singleton where you need configuration; never read
``os.environ`` deep in call sites. Every variable here is mirrored in ``.env.example``.

``Settings`` is the SINGLE config surface with TWO injection mechanisms, selected by ``DECODE_ENV``
(ADR-0015): ``.env`` at ``local`` (the default — kitaru never imported), the derived **Environment
Bucket** ``decode-<env>`` at ``dev`` / ``staging`` / ``prod``, where ``.env`` is dropped from the
chain entirely so a key missing from the bucket fails loudly instead of being backfilled.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import Field, SecretStr, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)

DecodeEnv = Literal["local", "dev", "staging", "prod"]

# The environments whose config comes from an Environment Bucket; ``local`` reads ``.env`` instead.
_REMOTE_ENVS = frozenset({"dev", "staging", "prod"})

_DECODE_ENV_VAR = "DECODE_ENV"

# The last Environment-Bucket load failure, or None. The ``settings`` singleton is built at IMPORT
# time, so the source must never raise: a missing bucket (or a downed Kitaru local server) is
# recorded here and rendered by the cli startup guards as ONE friendly line (ADR-0015 §5).
_bucket_load_error: str | None = None


def bucket_load_error() -> str | None:
    """Why the Environment Bucket could not be loaded on the last remote build, else None (ADR-0015 §5)."""
    return _bucket_load_error


def environment_bucket_name(decode_env: str) -> str:
    """The DERIVED Environment Bucket name — no override knob, so it cannot drift (ADR-0015 §3)."""
    return f"decode-{decode_env}"


def _decode_env_from_dotenv(dotenv_settings: PydanticBaseSettingsSource) -> str | None:
    """Read ``DECODE_ENV`` straight out of the dotenv file(s) the dotenv source would read, else None.

    Honours the source's own ``env_file`` (``None`` for ``Settings(_env_file=None)``, a custom path
    for a test build), so an out-of-band read can never disagree with the file the chain would use.
    """
    env_file = getattr(dotenv_settings, "env_file", None)
    if env_file is None:
        return None
    paths = env_file if isinstance(env_file, (list, tuple)) else [env_file]
    value: str | None = None
    for path in paths:
        if not Path(path).is_file():
            continue
        found = dotenv_values(path).get(_DECODE_ENV_VAR)
        if found:
            value = found  # later files win, matching the dotenv source's own ordering
    return value


def _resolve_decode_env(dotenv_settings: PydanticBaseSettingsSource) -> str:
    """Resolve the ``DECODE_ENV`` gate OUT-OF-BAND — the one deliberate exception to the chain (ADR-0015 §1).

    ``DECODE_ENV`` is the *bootstrap* variable: it decides whether the Environment Bucket is read at
    all, so it can never come **from** the bucket — and the bucket source sits above dotenv in
    precedence, so it cannot see a ``.env``-only value through the normal chain either. It is read
    here instead: parse the dotenv file, then overlay ``os.environ`` — **process env wins**,
    consistent with every other setting. The resolved value is fed back into the field by the source,
    so ``settings.decode_env`` can never diverge from the gate that was actually applied.
    """
    return os.environ.get(_DECODE_ENV_VAR) or _decode_env_from_dotenv(dotenv_settings) or "local"


class EnvironmentBucketSettingsSource(PydanticBaseSettingsSource):
    """A pydantic-settings source that hydrates the whole surface from the Environment Bucket (ADR-0015 §2).

    Reads ``.env.example``-shaped key/value pairs from the derived Kitaru secret ``decode-<env>`` and
    feeds the ones mapping to a known field into ``Settings`` — the whole config surface, no
    per-variable code. Three invariants keep it safe in every ``Settings`` build:

    * **Inert at ``local``.** It hydrates nothing — and imports no kitaru — unless the resolved gate
      is a remote environment (the restated REPL-safety invariant, ADR-0015 §5).
    * **``Settings`` object only — never ``os.environ``.** Nothing is written to the process env, so
      a model-chosen ``bash`` never inherits a bucket-sourced secret.
    * **Never raises.** The singleton is built at import; a missing bucket / a downed Kitaru local
      server is captured in :func:`bucket_load_error` and surfaced by the cli as one friendly line.
    """

    def __init__(self, settings_cls: type[BaseSettings], decode_env: str) -> None:
        super().__init__(settings_cls)
        self.decode_env = decode_env

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Unused: :meth:`__call__` is fully overridden, but the abstract base requires a body."""
        raise NotImplementedError(
            "EnvironmentBucketSettingsSource overrides __call__; get_field_value is never invoked."
        )

    def __call__(self) -> dict[str, Any]:
        global _bucket_load_error

        # The resolved gate always rides back onto the field, so it can never diverge from the gate
        # that was actually applied — including on the failure path below.
        if self.decode_env not in _REMOTE_ENVS:
            # Inert path: every ``local`` build lands here, so kitaru is never imported.
            return {"decode_env": self.decode_env}

        bucket = environment_bucket_name(self.decode_env)
        try:
            from kitaru import get_secret  # lazy: a remote env is the only importer of kitaru here

            values = get_secret(bucket).values
        except Exception as exc:  # broad on purpose: an import-time crash is never acceptable here
            _bucket_load_error = f"{bucket}: {exc}"
            logger.debug("environment bucket %r could not be loaded: %s", bucket, exc)
            return {"decode_env": self.decode_env}

        _bucket_load_error = None
        known = self.settings_cls.model_fields
        hydrated = {key.lower(): value for key, value in values.items() if key.lower() in known}
        logger.debug(
            "hydrated %d field(s) from environment bucket %r: %s",
            len(hydrated),
            bucket,
            sorted(hydrated),  # field NAMES only — never the values (they may be secrets)
        )
        hydrated["decode_env"] = self.decode_env
        return hydrated


class Settings(BaseSettings):
    """Runtime configuration. Defaults are safe for tests, not production."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Environments: the injection-mechanism selector (ADR-0015 §1) ---
    # ``local`` (the default) reads ``.env``; every other value reads the derived Environment Bucket
    # ``decode-<env>`` and DROPS ``.env`` from the chain. It selects the mechanism, nothing else (no
    # session-dir / log-path / MEMORY.md effect). A new environment is a code change, deliberately.
    decode_env: DecodeEnv = "local"

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
    # More on supported models: getting_started/modal_models.md
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
    # The ``projectName`` OTLP header (Opik groups traces by it). DERIVED: ``decode-<DECODE_ENV>``
    # unless explicitly set, so a trace always names the environment that produced it (ADR-0015 §8) —
    # the declared default below is cosmetic; :meth:`_derive_opik_project_name` supplies the real one.
    opik_project_name: str = "decode-local"
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

    # --- Subagents: the ``agent`` tool + Explore-subagent runner caps (ADR-0013 §7,8; ADR-0017 §2,6) ---
    # Parallel cap enforced by a per-running-loop Semaphore (keep modest — fan-out multiplies model
    # calls) — the CONCURRENCY ceiling, distinct from the fan-out WIDTH cap (deliberately NOT a
    # setting: a module constant in ``tools/agent.py``). ``subagent_result_max_bytes`` is the SHARED fold budget: one
    # ``agent`` call divides it across its children, so the fold costs the same at any width.
    # Per-child request cap + report truncation via the shared truncate() idiom. All gt=0 — a
    # non-positive value is a misconfiguration and fails fast.
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
    # Secrets are NOT a runtime knob any more: the retired ``RUNTIME_SECRET_*`` family is deleted,
    # with no shim — config comes from ``DECODE_ENV`` (above), in the TUI and headless alike, and a
    # stale entry in a developer's ``.env`` is silently ignored (ADR-0015 §4; loud in .env.example).

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
    # The one git token for BOTH sandboxes' git push / PRs, direct-injected into the Worker env as
    # ``GITHUB_TOKEN`` + git's credential helper — one mechanism, both backends (ADR-0016 §2). Empty
    # injects nothing: no env var, no helper — rely on the host-side hand-back, which never puts a
    # credential in the sandbox. A sandboxed process CAN read this token, so use a fine-grained,
    # revocable PAT scoped to the target repo, never a broad classic one.
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

    # --- Evals (ADR-0017) — the eval suite is NOT shipped in the wheel, but its judges + Opik
    # project are read from this SAME Settings surface so the harness needs no config of its own. ---
    # The LiteLLM model string G-Eval judges run on; empty derives it from ``llm_provider`` (task 104).
    eval_judge_model: str = ""
    # The Opik project eval runs log under — kept distinct from the live-REPL project (ADR-0014) so
    # eval traces never mix into ``decode-<env>``.
    eval_project_name: str = "decode-evals"

    @model_validator(mode="after")
    def _derive_opik_project_name(self) -> Settings:
        """Default the Opik project to ``decode-<DECODE_ENV>``; an explicit value always wins (ADR-0015 §8).

        "Explicit" is decided by pydantic's ``model_fields_set``, **never** by comparing against the
        declared default: a value supplied by ANY settings source (process env, ``.env``, the
        Environment Bucket, ``init``) lands in ``model_fields_set``, while a default-applied one does
        not. A sentinel/value comparison would misfire on the operator who deliberately sets
        ``OPIK_PROJECT_NAME`` to the same literal the default derives to.

        ``object.__setattr__`` writes the derived value straight into ``__dict__``: it neither
        re-enters validation nor forges an "explicit" mark in ``model_fields_set``, so the field keeps
        reading as derived. ``decode_env`` is resolved on this same model (the source feeds the gate
        back onto the field), so it is safe to read here.
        """
        if "opik_project_name" not in self.model_fields_set:
            object.__setattr__(self, "opik_project_name", f"decode-{self.decode_env}")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Pick the source chain from the ``DECODE_ENV`` gate — two mechanisms, one surface (ADR-0015 §2).

        Precedence is left-to-right, and the gate is resolved out-of-band (see
        :func:`_resolve_decode_env`) because it decides which chain is built:

        * ``local`` (the default): ``init > process env > .env > defaults`` — today's behaviour,
          kitaru never imported.
        * ``dev`` / ``staging`` / ``prod``: ``init > process env > Environment Bucket > defaults``.
          ``dotenv_settings`` is **absent from the returned tuple**, so ``.env`` is dropped from the
          chain entirely and a key missing from the bucket fails loudly instead of being silently
          backfilled from a developer's file. That is the whole point of having environments.

        An invalid ``DECODE_ENV`` is not a remote env, so it takes the ``local`` chain and the closed
        ``Literal`` rejects it with a clear validation error (no bucket read on a typo).
        """
        decode_env = _resolve_decode_env(dotenv_settings)
        if decode_env not in _REMOTE_ENVS:
            return (init_settings, env_settings, dotenv_settings, file_secret_settings)
        return (
            init_settings,
            env_settings,
            EnvironmentBucketSettingsSource(settings_cls, decode_env),
            file_secret_settings,
        )


settings = Settings()
