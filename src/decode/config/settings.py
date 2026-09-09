"""Application configuration via pydantic-settings.

Import the module-level ``settings`` singleton where you need configuration; never read
``os.environ`` deep in call sites. Every variable here is mirrored in ``.env.example``.

``Settings`` is the SINGLE config surface, with ONE source chain at every environment (ADR-0021):
``init > process env > .env > defaults``. ``DECODE_ENV`` names the environment and nothing else —
it suffixes the Modal Secret / app names, the nested sandbox app and the Opik project, and never
changes where a value is read from. The store that feeds the chain is the platform's own: a
developer's ``.env`` on a laptop, the ``decode-<app>-<env>`` Modal Secret in a container.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

DecodeEnv = Literal["local", "dev", "staging", "prod"]

# Literals that mean "I copied .env.example and have not filled this in yet", matched
# case-insensitively after stripping. A secret holding one of these reads as UNSET, so the startup
# preflight prints its one friendly line instead of the provider rejecting the placeholder mid-turn:
# presence checks (``if not key``) are the only thing standing between a half-filled ``.env`` and a
# raw 400 on the user's first message.
_PLACEHOLDER_SECRETS = frozenset({"changeme", "change-me", "your-key-here", "todo", "xxx"})

# The secret fields the placeholder scrub covers: every one gates a startup presence check.
_SCRUBBED_SECRET_FIELDS = (
    "gemini_api_key",
    "openrouter_api_key",
    "modal_proxy_token_id",
    "modal_proxy_token_secret",
    "opik_api_key",
    "sandbox_git_token",
)

# Known MAX INPUT windows in tokens, matched case-insensitively as a SUBSTRING of the active
# provider's model id, most-specific first. Substring (not equality) because ids carry vendor
# prefixes and quantization suffixes the window does not depend on:
# ``Qwen/Qwen3.6-35B-A3B-FP8`` and a future ``…-AWQ`` share one window.
#
# Provenance matters — a guessed number reintroduces exactly the bug this table fixes:
#   * qwen3.6-35b-a3b — 262144, READ from the served endpoint (``GET /v1/models`` → max_model_len).
#   * gemini-3.5 / gemini-2.5 — 1048576, the published 1M input window for those Flash/Pro lines.
# Anything absent falls back to :data:`UNKNOWN_MODEL_CONTEXT_WINDOW` with a startup warning; add a
# row here (with its source) rather than widening a pattern on a hunch.
MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("qwen3.6-35b-a3b", 262_144),
    ("gemini-3.5", 1_048_576),
    ("gemini-2.5", 1_048_576),
)

# The window assumed for a model absent from the table. Deliberately CONSERVATIVE: under-estimating
# only makes compaction fire early (a cheaper turn), while over-estimating silently overruns the
# endpoint. 200k clears every model in the course catalog's documented ">=128k long-context" tier
# without assuming a 1M frontier window.
UNKNOWN_MODEL_CONTEXT_WINDOW = 200_000


def context_window_for(model_id: str) -> int | None:
    """The known input window for ``model_id``, or ``None`` when no table row matches.

    ``None`` (rather than the fallback) is the signal the caller needs to warn — the fallback is a
    guess, and a guess the operator never sees is how a 4x-wrong window survives for months.
    """
    lowered = model_id.lower()
    for pattern, window in MODEL_CONTEXT_WINDOWS:
        if pattern in lowered:
            return window
    return None


class Settings(BaseSettings):
    """Runtime configuration. Defaults are safe for tests, not production."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Environments: the NAMING suffix, and nothing else (ADR-0021 §1) ---
    # Read from the same chain as every other field (process env > ``.env`` > default). It suffixes
    # names — the nested sandbox app ``decode-sandbox-<env>``, the Opik project ``decode-<env>``, and
    # (deploy-side) the Modal app + Secret — and changes NOTHING about where config is read from, nor
    # session dirs, log paths or ``MEMORY.md``. A new environment is a code change, deliberately.
    decode_env: DecodeEnv = "local"

    # --- Inference: one of three providers behind LLM_PROVIDER (ADR-0005). ---
    # Explicit selector (no auto-detect); the ``gemini`` default keeps existing .env files working.
    llm_provider: Literal["gemini", "openrouter", "modal"] = "gemini"

    # gemini (default): google-genai API-key path.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash"

    # openrouter: the default ``openrouter/free`` router spreads across free models and auto-filters
    # for tool-calling, so one congested upstream cannot hard-block with 429s; pin a :free id for a
    # stricter guarantee.
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "openrouter/free"

    # modal Auto Endpoint: OpenAI-compatible ``/v1``. Endpoint vars are DISTINCT from the
    # MODAL_TOKEN_* account tokens; the url is per-user deploy output (used as ``{url}/v1``); proxy
    # tokens are optional (empty = --unauthenticated), both-or-neither.
    modal_endpoint_url: str = ""
    # More on supported models: running_the_code/02_modal_endpoints.md
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
    # USD per MILLION tokens, for a PER-TOKEN model the genai-prices catalog does not know — an
    # OpenRouter slug missing from it is the case these exist for. Deliberately NOT the answer for
    # ``modal``: a self-hosted endpoint bills GPU-seconds, so a per-token rate there would report a
    # number nobody is charged. Both 0.0 (the default) means "unknown", and decode reports no cost
    # rather than inventing one (ADR-0014 §8).
    llm_cost_input_usd_per_mtok: float = Field(0.0, ge=0)
    llm_cost_output_usd_per_mtok: float = Field(0.0, ge=0)

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
    # exposes no model window, so this number is the contract). DERIVED from the active provider's
    # model id via :data:`MODEL_CONTEXT_WINDOWS` unless explicitly set; the declared default below is
    # cosmetic, :meth:`_derive_compaction_context_window` supplies the real one. A wrong value here is
    # not cosmetic: too LARGE and both compaction tiers fire above the endpoint's hard ceiling, so the
    # request is truncated or rejected before compaction ever runs.
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

    # --- Headless runtime (ADR-0019 §1) ---
    # ``runtime_enabled`` master-gates the WHOLE headless feature: ``False`` → ``decode run`` exits
    # with a friendly line and never builds an agent. The durable-flow knobs
    # (``RUNTIME_CHECKPOINT_STRATEGY`` / ``RUNTIME_WAIT_TIMEOUT_S``) died with the flow — a clean
    # break, no shim: a stale entry in a developer's ``.env`` is silently ignored.
    runtime_enabled: bool = True
    # The default request ceiling for a headless run — the number of model requests after which
    # ``decode run`` stops with one friendly line and a non-zero exit instead of looping on. ``None``
    # (the default) is unbounded, byte-identical to the REPL; ``decode run --max-requests N``
    # overrides it per run. A background run (a cron job, a webhook, a CI step) has nobody watching
    # its token bill, which is what this ceiling is for.
    runtime_max_requests: int | None = Field(None, gt=0)
    # --- Recording Seam (ADR-0019 §3) ---
    # The Kitaru agent (a UUID) recorded runs are filed under. Presence-based opt-in and decode's
    # ONLY recording knob: set it (together with the adapter client's own ``KITARU_API_URL`` /
    # ``KITARU_API_KEY`` **process** env) and a run is wrapped in ``kitaru_pydantic_ai.KitaruAgent``;
    # empty → the bare agent, and no kitaru module is ever imported. Deliberately NOT paired with
    # url/key settings of decode's own: the adapter client resolves those itself, so there is exactly
    # one place to configure the workspace (a second one would drift).
    kitaru_agent_id: str = ""
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
    def _scrub_placeholder_secrets(self) -> Settings:
        """Read an unfilled ``.env.example`` placeholder (``changeme``) as UNSET.

        Every provider guard in ``cli.py`` is a PRESENCE check, so a literal placeholder is worse
        than an empty value: it sails past the guard and the run dies on a provider 400 at the first
        message, with nothing pointing back at ``.env``. Scrubbing here means one friendly startup
        line instead.

        Whitespace-only values scrub too — ``GEMINI_API_KEY=" "`` is the same mistake. Writes via
        ``object.__setattr__`` so the field keeps its "explicitly supplied" mark in
        ``model_fields_set``; the operator DID set it, they just set it to a placeholder.
        """
        for name in _SCRUBBED_SECRET_FIELDS:
            current: SecretStr | None = getattr(self, name)
            if current is None:
                continue
            value = current.get_secret_value()
            if value.strip().lower() in _PLACEHOLDER_SECRETS or not value.strip():
                if value:  # a name, never the value — the placeholder itself is not worth echoing
                    logger.debug("ignoring placeholder value for %s", name.upper())
                object.__setattr__(self, name, SecretStr(""))
        return self

    @model_validator(mode="after")
    def _derive_opik_project_name(self) -> Settings:
        """Default the Opik project to ``decode-<DECODE_ENV>``; an explicit value always wins (ADR-0015 §8).

        "Explicit" is decided by pydantic's ``model_fields_set``, **never** by comparing against the
        declared default: a value supplied by ANY settings source (process env, ``.env``, ``init``)
        lands in ``model_fields_set``, while a default-applied one does not. A sentinel/value
        comparison would misfire on the operator who deliberately sets ``OPIK_PROJECT_NAME`` to the
        same literal the default derives to.

        ``object.__setattr__`` writes the derived value straight into ``__dict__``: it neither
        re-enters validation nor forges an "explicit" mark in ``model_fields_set``, so the field keeps
        reading as derived.
        """
        if "opik_project_name" not in self.model_fields_set:
            object.__setattr__(self, "opik_project_name", f"decode-{self.decode_env}")
        return self

    @property
    def active_model(self) -> str:
        """The model id of the ACTIVE provider — the one :func:`decode.agent.factory` will build.

        Note this is the configured model, not necessarily the model a given run uses: ``--model``
        overrides the id per run (ADR-0010 §2) while ``Settings`` is process-scoped, so a run that
        overrides to a differently-sized model keeps the window derived from configuration here.
        """
        if self.llm_provider == "openrouter":
            return self.openrouter_model
        if self.llm_provider == "modal":
            return self.modal_endpoint_model
        return self.gemini_model

    @property
    def context_window_is_assumed(self) -> bool:
        """True when the window was FILLED IN from the fallback rather than known or set.

        The cli reads this to warn once at startup. False when the operator set the value
        explicitly (they own the number) or the model matched a table row (it is known).
        """
        return (
            "compaction_context_window_tokens" not in self.model_fields_set
            and context_window_for(self.active_model) is None
        )

    @model_validator(mode="after")
    def _derive_compaction_context_window(self) -> Settings:
        """Default the compaction window to the active model's, or the conservative fallback.

        Same "explicit wins" gate as :meth:`_derive_opik_project_name`: any settings source that
        supplied ``COMPACTION_CONTEXT_WINDOW_TOKENS`` marks the field in ``model_fields_set`` and is
        left untouched, so an operator who knows their endpoint can always override the table.

        Runs after the provider fields are populated (``mode="after"``), so ``active_model`` reads
        the resolved id. The fallback is logged at DEBUG here rather than WARNING — the cli emits the
        one user-facing line via :attr:`context_window_is_assumed`, and library code that warns on
        every import makes the tests noisy under ``filterwarnings=["error"]``.
        """
        if "compaction_context_window_tokens" in self.model_fields_set:
            return self
        known = context_window_for(self.active_model)
        window = known if known is not None else UNKNOWN_MODEL_CONTEXT_WINDOW
        if known is None:
            logger.debug(
                "no known context window for model %r; assuming %d tokens",
                self.active_model,
                window,
            )
        object.__setattr__(self, "compaction_context_window_tokens", window)
        return self


settings = Settings()
