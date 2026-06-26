---
id: 037-llm-provider-settings
feature: multi-provider-gateway
status: pending
---

# Settings: LLM_PROVIDER selector + per-provider inference fields

Implements [ADR-0005](../docs/adr/0005-multi-llm-provider-support.md) §1-2 (explicit provider
selector + per-provider config surface). Extends [ADR-0002](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md) §1.
Modal var names + default model per [`MODAL_MODELS.md`](../MODAL_MODELS.md) §6.
Depends on: — · Blocks: 038, 039, 040

## Scope

Add the multi-provider config surface to `src/decode/config/settings.py` (the single config reader;
never read `os.environ` in call sites). Default selection stays **`gemini`** so existing `.env` files
that only set `GEMINI_API_KEY` keep working with no `LLM_PROVIDER` line (backward-compatible).

- `from typing import Literal`.
- `llm_provider: Literal["gemini", "openrouter", "modal"] = "gemini"` — the **explicit** selector
  (no auto-detect). pydantic validates the value at load: an unknown value raises `ValidationError`.
- OpenRouter fields:
  - `openrouter_api_key: SecretStr = SecretStr("")`
  - `openrouter_model: str = "qwen/qwen3-coder:free"` — a CURRENT known-good free model that supports
    tool-calling + streaming, verified against the live OpenRouter model list on 2026-06-26. **SWE:
    re-verify it is still live at implementation time** (free ids churn) via
    `GET https://openrouter.ai/api/v1/models`, filtering `id` ending `:free` AND `supported_parameters`
    containing `tools`; documented alternate is `meta-llama/llama-3.3-70b-instruct:free`.
- Modal **endpoint** fields — names + default from `MODAL_MODELS.md` §6; DISTINCT from the existing
  `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` **account** tokens (`modal token set`, for the CLI/sandbox —
  §5.1; do **not** overload them):
  - `modal_endpoint_url: str = ""` — **no default** (per-user deploy output from
    `modal endpoint create`; used as `base_url = f"{modal_endpoint_url}/v1"`). The cli guard (task 039)
    requires it when `provider=modal`.
  - `modal_endpoint_model: str = "openai/gpt-oss-120b"` — the served model id. Default is the
    `MODAL_MODELS.md` best-fit pick (native OpenAI tool-calling, single B200); the user's deployed
    endpoint must serve this same id. Documented alternates: `Qwen/Qwen3.6-35B-A3B-FP8` (cheap dev,
    1×H100) and `zai-org/GLM-5.2-FP8` (max). Do **not** default to a small Qwen — the catalog
    disqualifies them for tool discipline.
  - `modal_proxy_token_id: SecretStr = SecretStr("")` — the `Modal-Key: wk-...` request header
    (proxy token from `modal workspace proxy-tokens create`; **optional** — empty for an
    `--unauthenticated` endpoint).
  - `modal_proxy_token_secret: SecretStr = SecretStr("")` — the `Modal-Secret: ws-...` request header
    (proxy token; **optional**, both-or-neither with `modal_proxy_token_id`).
- Update the inline `# --- Inference ---` comment block to name the three providers behind
  `LLM_PROVIDER` and reference ADR-0005 (drop the stale "M2 ... behind a gateway" wording).
- Keep `gemini_api_key` / `gemini_model` **exactly** as-is.

**Minimal `.env.example` mirror (AGENTS.md "mirror every new var" rule, same commit):** add bare lines
for `LLM_PROVIDER`, `OPENROUTER_MODEL`, `MODAL_ENDPOINT_URL`, `MODAL_ENDPOINT_MODEL`,
`MODAL_PROXY_TOKEN_ID`, `MODAL_PROXY_TOKEN_SECRET` with safe placeholders so this commit is
self-consistent. The full inference-section **reorg** (free-tier notes, account-token-vs-proxy-token
prose, the MODAL_MODELS.md link) is task 040 — do not do the prose here.

## Acceptance criteria

- [ ] `Settings(_env_file=None).llm_provider == "gemini"` (default); `openrouter_model` defaults to the
      verified `:free` id, `modal_endpoint_model` defaults to `"openai/gpt-oss-120b"`,
      `modal_endpoint_url` to `""`, and `openrouter_api_key`/`modal_proxy_token_id`/
      `modal_proxy_token_secret` to empty `SecretStr`. Existing `gemini_*` fields and all other settings
      are unchanged. Unit-tested in `tests/unit/decode/config/test_settings.py`.
- [ ] `LLM_PROVIDER=gemini|openrouter|modal` each load as the matching literal; an invalid value
      (e.g. `LLM_PROVIDER=anthropic`) raises a pydantic `ValidationError` at construction (the `Literal`
      is enforced, not silently accepted). Unit-tested.
- [ ] `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `MODAL_ENDPOINT_URL`, `MODAL_ENDPOINT_MODEL`,
      `MODAL_PROXY_TOKEN_ID`, `MODAL_PROXY_TOKEN_SECRET` each read from the process env and from a `.env`
      file into the matching field; `openrouter_api_key`/`modal_proxy_token_id`/
      `modal_proxy_token_secret` are `SecretStr` and do **not** appear in `repr(settings)`. Unit-tested.
- [ ] `.env.example` carries the six new var lines (placeholders) so the new vars are mirrored at this
      commit; the existing `OPENROUTER_API_KEY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` lines remain.
- [ ] `make ci` green, 0 warnings (`filterwarnings=["error"]`); `tests/unit/decode/config/` mirrors the
      change 1:1.

## Out of scope
- Per-provider **required-secret enforcement** (incl. "modal requires `MODAL_ENDPOINT_URL`" and the
  proxy-token both-or-neither check) — that is the cli startup guard, task 039. This task only defines
  the fields + their `Literal` validation.
- Any factory / model-construction change — task 038.
- The full `.env.example` inference-section reorg + the README "LLM providers" docs + the MODAL_MODELS.md
  link/light-pass — task 040 (this task does only the minimal var-line mirror).

## Log
