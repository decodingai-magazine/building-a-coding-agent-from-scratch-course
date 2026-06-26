---
id: 037-llm-provider-settings
feature: multi-provider-gateway
status: done
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

- [x] `Settings(_env_file=None).llm_provider == "gemini"` (default); `openrouter_model` defaults to the
      verified `:free` id, `modal_endpoint_model` defaults to `"openai/gpt-oss-120b"`,
      `modal_endpoint_url` to `""`, and `openrouter_api_key`/`modal_proxy_token_id`/
      `modal_proxy_token_secret` to empty `SecretStr`. Existing `gemini_*` fields and all other settings
      are unchanged. Unit-tested in `tests/unit/decode/config/test_settings.py`.
- [x] `LLM_PROVIDER=gemini|openrouter|modal` each load as the matching literal; an invalid value
      (e.g. `LLM_PROVIDER=anthropic`) raises a pydantic `ValidationError` at construction (the `Literal`
      is enforced, not silently accepted). Unit-tested.
- [x] `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `MODAL_ENDPOINT_URL`, `MODAL_ENDPOINT_MODEL`,
      `MODAL_PROXY_TOKEN_ID`, `MODAL_PROXY_TOKEN_SECRET` each read from the process env and from a `.env`
      file into the matching field; `openrouter_api_key`/`modal_proxy_token_id`/
      `modal_proxy_token_secret` are `SecretStr` and do **not** appear in `repr(settings)`. Unit-tested.
- [x] `.env.example` carries the six new var lines (placeholders) so the new vars are mirrored at this
      commit; the existing `OPENROUTER_API_KEY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` lines remain.
- [x] `make ci` green, 0 warnings (`filterwarnings=["error"]`); `tests/unit/decode/config/` mirrors the
      change 1:1.

## Out of scope
- Per-provider **required-secret enforcement** (incl. "modal requires `MODAL_ENDPOINT_URL`" and the
  proxy-token both-or-neither check) — that is the cli startup guard, task 039. This task only defines
  the fields + their `Literal` validation.
- Any factory / model-construction change — task 038.
- The full `.env.example` inference-section reorg + the README "LLM providers" docs + the MODAL_MODELS.md
  link/light-pass — task 040 (this task does only the minimal var-line mirror).

## Log

### [SWE] 2026-06-26 15:37 — Implementation

**Live-verify (OpenRouter default model)**
Re-confirmed `qwen/qwen3-coder:free` against the live model list
(`GET https://openrouter.ai/api/v1/models`, filtered `id` endswith `:free` AND `supported_parameters`
contains `tools`): **still present and tool-capable** — no swap needed, default kept as
`qwen/qwen3-coder:free`. The documented alternate `meta-llama/llama-3.3-70b-instruct:free` is also
still live (17 free+tool ids total in the snapshot).

**Files modified**
- `src/decode/config/settings.py` — added `llm_provider` `Literal` selector (default `gemini`) plus the
  OpenRouter (`openrouter_api_key` SecretStr, `openrouter_model`) and Modal endpoint
  (`modal_endpoint_url`, `modal_endpoint_model`, `modal_proxy_token_id`/`modal_proxy_token_secret`
  SecretStr) fields; rewrote the `# --- Inference ---` comment to name the three providers + reference
  ADR-0005; `gemini_api_key`/`gemini_model` unchanged.
- `.env.example` — mirrored the 6 new vars (`LLM_PROVIDER`, `OPENROUTER_MODEL`, `MODAL_ENDPOINT_URL`,
  `MODAL_ENDPOINT_MODEL`, `MODAL_PROXY_TOKEN_ID`, `MODAL_PROXY_TOKEN_SECRET`); existing
  `OPENROUTER_API_KEY`/`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` retained. Renamed the stale orphan
  `MODAL_PROXY_TOKEN_KEY` line (mapped to no field) to `MODAL_PROXY_TOKEN_ID` so the file is
  self-consistent with the new field.
- `tests/unit/decode/config/test_settings.py` — 7 new cases (one parametrized x3) covering the default
  selector + new defaults, each valid `Literal` value, invalid-value `ValidationError`, process-env +
  `.env`-file loading of all 6 vars, and SecretStr redaction in `repr`.

**Tests**
- Unit: 698 passing, 0 failing (`make pre-commit`); config module: 14 passing.
- CI gate: 706 passing (incl. integration), 0 warnings — `make ci` green.
- Integration: N/A for this change (no infra touched); the M1/M3 capstones still pass under `make ci`.

**Acceptance criteria**
- [x] default selector + new defaults + unchanged gemini fields — `test_provider_defaults`,
      `test_defaults`
- [x] each `Literal` value loads / invalid raises `ValidationError` —
      `test_llm_provider_accepts_each_valid_literal[gemini|openrouter|modal]`,
      `test_llm_provider_rejects_unknown_value`
- [x] env + `.env` loading of all 6 vars; SecretStr out of `repr` —
      `test_reads_provider_vars_from_process_env`, `test_loads_provider_vars_from_a_dotenv_file`,
      `test_provider_secrets_not_in_repr`
- [x] `.env.example` mirrors the 6 new vars; existing lines remain
- [x] `make ci` green, 0 warnings; `tests/unit/decode/config/` mirrors 1:1

**Evidence**
```
$ make ci
... uv lock --check · ruff format --check · ruff check ...
============================= 706 passed in 7.97s ==============================

$ uv run pytest tests/unit/decode/config/test_settings.py -q
..............                                                           [100%]
14 passed in 0.13s

$ LLM_PROVIDER=anthropic uv run python -c "from decode.config.settings import Settings; Settings(_env_file=None)"
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
llm_provider
  Input should be 'gemini', 'openrouter' or 'modal' [type=literal_error, input_value='anthropic', ...]

$ LLM_PROVIDER=openrouter OPENROUTER_API_KEY=sk-or-supersecret uv run python -c "...repr..."
repr leaks secret? False        # SecretStr('**********') in repr
```

**Notes**
- Scope held to settings + minimal `.env.example` mirror per the task: NO factory `_build_model()`
  (task 038), NO cli startup guard / both-or-neither check (task 039), NO `.env.example` prose reorg or
  README docs (task 040).
- Did NOT commit — handing to Tester first.

### [Tester] 2026-06-26 16:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 112 files clean; `ruff check` clean; pre-commit 698 unit passed)
- Unit tests: 698 passed / 0 failed (config module: 14 passed)
- Integration tests: 8 passed / 0 failed (M1 + M3 capstones)
- `make ci`: PASS — 706 passed, 0 warnings (`filterwarnings=["error"]`, `uv lock --check` clean)
- Warnings: 0

**E2E adversarial pass** (surface = `Settings`)
- Happy path: `GEMINI_API_KEY=only-gemini` (all provider vars unset) → `llm_provider=gemini`, gemini key
  loaded, `openrouter_model=qwen/qwen3-coder:free`, `modal_endpoint_model=openai/gpt-oss-120b`,
  `modal_endpoint_url=''`; module-level singleton imports cleanly with the repo `.env` present (PASS)
- Break 1 (boundary/malformed `LLM_PROVIDER`): `anthropic`, `''`, `GEMINI` (case), `' gemini'`/`'gemini '`
  (whitespace), `openai` → every value raises `ValidationError` at construction (PASS)
- Break 2 (secret redaction): provider=modal with real-looking secrets in `openrouter_api_key`,
  `modal_proxy_token_id/secret`, `gemini_api_key` → none of the 4 secrets appear in `repr` / `str` /
  `model_dump`; `get_secret_value()` still returns the real value (functionally usable) (PASS)
- Break 3 (env + .env loading + precedence): all 6 new vars load from a `.env` file in cwd; process env
  overrides `.env` for `LLM_PROVIDER` and `MODAL_PROXY_TOKEN_ID` (PASS)
- Break 4 (no premature enforcement — state edge): provider=modal with only `MODAL_PROXY_TOKEN_ID` set,
  no `MODAL_ENDPOINT_URL`, partial proxy pair → loads without error; both-or-neither + modal-requires-url
  enforcement correctly deferred to task 039 (PASS)
- `.env.example` self-consistency: renamed orphan `MODAL_PROXY_TOKEN_KEY` → `MODAL_PROXY_TOKEN_ID`;
  loading `.env.example` through `Settings` confirms `MODAL_PROXY_TOKEN_ID=changeme` maps to
  `modal_proxy_token_id` (orphan no longer mapped to nothing); all 6 new lines present; existing
  `OPENROUTER_API_KEY`/`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` retained (PASS)

**Acceptance criteria**
- [x] PASS — default selector + new defaults + unchanged gemini/other fields — `test_provider_defaults`,
      `test_defaults`; manual: `Settings(_env_file=None)` → provider `gemini`, defaults as specified
- [x] PASS — each `Literal` value loads; invalid raises `ValidationError` —
      `test_llm_provider_accepts_each_valid_literal[gemini|openrouter|modal]`,
      `test_llm_provider_rejects_unknown_value`; manual: 6 invalid/boundary values all → `ValidationError`
- [x] PASS — all 6 vars load from process env and `.env`; SecretStr fields out of `repr` —
      `test_reads_provider_vars_from_process_env`, `test_loads_provider_vars_from_a_dotenv_file`,
      `test_provider_secrets_not_in_repr`; manual: redaction holds across repr/str/model_dump
- [x] PASS — `.env.example` carries the 6 new var lines; existing `OPENROUTER_API_KEY`/`MODAL_TOKEN_ID`/
      `MODAL_TOKEN_SECRET` remain (`.env.example:7,21,22,27,28,31,32,33,34`); stale `MODAL_PROXY_TOKEN_KEY`
      orphan removed and rename maps to the real field
- [x] PASS — `make ci` green, 0 warnings; `tests/unit/decode/config/test_settings.py` mirrors the change 1:1

**Evidence**
```
$ make ci
uv lock --check → Resolved 176 packages
ruff format --check → 112 files already formatted
ruff check → All checks passed!
============================= 706 passed in 7.68s ==============================

$ uv run pytest tests/unit/decode/config/test_settings.py -q
14 passed in 0.13s

$ LLM_PROVIDER=anthropic Settings(_env_file=None) → ValidationError (literal_error)
$ provider=modal + real secrets → leaked in repr/str/model_dump: NONE
```

**Other issues found**
- None blocking. Note (PASS with note, not in this task's scope): `MODAL_MODELS.md` still uses the shell
  var name `MODAL_PROXY_TOKEN_KEY` in its curl examples (lines 209/213/222/265) while the field is
  `modal_proxy_token_id` / env `MODAL_PROXY_TOKEN_ID`. Reconciling that reference doc is the task-040
  light-pass, not 037 — flagging for the orchestrator/040, not a FAIL here.

**VERDICT: PASS**

### [PA] 2026-06-26 17:05 — Acceptance Review

**VERDICT: ACCEPT**

User-POV check of the config surface (settings.py:21-46, .env.example:5-45). Default `llm_provider="gemini"`
keeps every existing single-`GEMINI_API_KEY` `.env` working untouched (backward-compatible); the opt-in is a
single explicit `LLM_PROVIDER=` line, not auto-detect. The two Modal credential scopes (account
`MODAL_TOKEN_*` vs endpoint proxy `MODAL_PROXY_TOKEN_*`) are unmistakably labelled in `.env.example` so a user
can't conflate them. Secrets redact in `repr`. Canonical glossary term **LLM Provider** used. Verified from the
user's seat, not just the suite. Part of feature `multi-provider-gateway` acceptance (PR #11). Hand off to the
PR Reviewer.
