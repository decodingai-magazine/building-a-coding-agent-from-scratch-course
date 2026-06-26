---
id: 038-factory-build-model-seam
feature: multi-provider-gateway
status: pending
---

# Factory: _build_model() provider seam (Gemini / OpenRouter / Modal)

Implements [ADR-0005](../docs/adr/0005-multi-llm-provider-support.md) §3-5 (the `_build_model()` seam,
the three model constructions, Modal proxy-token auth incl. `--unauthenticated`). Fills the
provider-swap seam [ADR-0002](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md) §1 deferred.
Modal endpoint behaviour per [`MODAL_MODELS.md`](../MODAL_MODELS.md) §5.3-§6.
Depends on: 037 · Blocks: 040

## Scope

Realize the **Provider Seam** in `src/decode/agent/factory.py`: extract the model construction in
`build_agent()` into a small private `_build_model()` helper that branches on `settings.llm_provider`.
Everything else in `build_agent()` — `deps_type=AgentDeps`, `output_type=[str, DeferredToolRequests]`,
`instructions=_BASE_INSTRUCTIONS`, `register_tools(agent)`, and the three `@agent.instructions` hooks —
stays **byte-identical**. The loop in `decode.agent.loop` is untouched.

Three branches, verified against the installed pydantic-ai 1.107 / openai 2.43 during grooming:

- **`gemini`** (unchanged): `GoogleModel(settings.gemini_model,
  provider=GoogleProvider(api_key=settings.gemini_api_key.get_secret_value()))`. Keep the existing
  google-gla path; **never** pass any `vertexai=` argument (`filterwarnings=["error"]` turns the
  deprecation warning into a test failure — ADR-0002 §1, today's factory docstring).
- **`openrouter`**: `OpenAIChatModel(settings.openrouter_model,
  provider=OpenRouterProvider(api_key=settings.openrouter_api_key.get_secret_value()))`.
- **`modal`**: a custom `AsyncOpenAI` client is **required** (custom `base_url`, and the proxy headers
  are not the OpenAI `Authorization: Bearer` scheme — MODAL_MODELS.md §6.3). Proxy tokens are
  **optional** to support `--unauthenticated` dev endpoints (MODAL_MODELS.md §5.3):
  ```python
  base_url = f"{settings.modal_endpoint_url}/v1"
  token_id = settings.modal_proxy_token_id.get_secret_value()
  token_secret = settings.modal_proxy_token_secret.get_secret_value()
  if token_id and token_secret:
      # authenticated endpoint → dual Modal-Key / Modal-Secret proxy headers
      client = AsyncOpenAI(
          base_url=base_url,
          api_key=token_secret,                    # non-empty; the OpenAI client requires it
          default_headers={"Modal-Key": token_id, "Modal-Secret": token_secret},
      )
  else:
      # --unauthenticated endpoint → no Modal headers; placeholder api_key (SDK requires non-empty)
      client = AsyncOpenAI(base_url=base_url, api_key="EMPTY")
  model = OpenAIChatModel(settings.modal_endpoint_model, provider=OpenAIProvider(openai_client=client))
  ```
  (The both-or-neither invariant — a friendly error when exactly one token is set — is enforced at the
  cli guard, task 039; by the time `_build_model()` runs, modal is either fully authenticated or fully
  unauthenticated.)

Imports: `OpenAIChatModel` from `pydantic_ai.models.openai`; `OpenAIProvider` / `OpenRouterProvider`
from `pydantic_ai.providers.{openai,openrouter}`; `AsyncOpenAI` from `openai`.

`_build_model()` ends with a defensive `raise ValueError(f"unsupported llm_provider: {provider!r}")`
for any value past the three branches (unreachable past the settings `Literal`, but covers a future
literal added before its branch is wired).

Update the factory **module + `build_agent()` docstrings**: reframe "build the agent on Gemini" as
"build the agent on the configured **LLM Provider**"; document `_build_model()` as the realized
Provider Seam (ADR-0002 §1 promised it, ADR-0005 fills it); keep the existing google-gla / `vertexai=`
and deferred-tool notes.

### Tests — parametrize `tests/unit/decode/agent/test_factory.py`
- Parametrize the model-**type** assertion over the providers, constructing offline (no model request
  issued by building the agent), injecting config via `mocker.patch` on `decode.agent.factory.settings.*`
  (mirror the existing `gemini_api_key` patch pattern):
  - `gemini` → `isinstance(agent.model, GoogleModel)`, `agent.model.system == "google"`,
    `model_name == settings.gemini_model` (the existing assertions, now one branch of the param).
  - `openrouter` → `isinstance(agent.model, OpenAIChatModel)`, `agent.model.system == "openrouter"`,
    `model_name == settings.openrouter_model`.
  - `modal`, **authenticated** (both proxy tokens set) → `isinstance(agent.model, OpenAIChatModel)`,
    `model_name == settings.modal_endpoint_model`, the client `base_url` ends with `/v1` (from
    `modal_endpoint_url`), and the request `default_headers` carry **both** `Modal-Key` and
    `Modal-Secret`.
  - `modal`, **unauthenticated** (neither proxy token set) → `OpenAIChatModel`, `base_url` ends with
    `/v1`, the client carries **no** `Modal-Key` / `Modal-Secret` headers, and `api_key == "EMPTY"`
    (the placeholder).
- Add a test that an unexpected `llm_provider` reaching `_build_model()` raises `ValueError`.
- All existing factory tests (memory / skills-catalog / agent-prompt hooks, visible-tool restriction)
  keep passing untouched — they patch `gemini_api_key` and the default provider stays `gemini`.

## Acceptance criteria

- [ ] `_build_model()` exists and `build_agent()` delegates model construction to it; the rest of
      `build_agent()` (deps_type, `output_type=[str, DeferredToolRequests]`, instructions,
      `register_tools`, the three hooks) is unchanged — the existing memory/skills/agent-prompt/
      visible-tool tests pass with no edits. Unit-tested.
- [ ] `llm_provider="gemini"` (default) builds a `GoogleModel` on the google-gla path
      (`system == "google"`, `model_name == settings.gemini_model`) — identical to today; no
      `vertexai=` argument is ever passed. Unit-tested.
- [ ] `llm_provider="openrouter"` builds an `OpenAIChatModel` with `system == "openrouter"` and
      `model_name == settings.openrouter_model`; building the agent makes no network call. Unit-tested.
- [ ] `llm_provider="modal"` with **both** proxy tokens set builds an `OpenAIChatModel`
      (`model_name == settings.modal_endpoint_model`) whose `AsyncOpenAI` client `base_url ==
      f"{settings.modal_endpoint_url}/v1"` and whose request headers carry **both** `Modal-Key` and
      `Modal-Secret`. No network on construction. Unit-tested.
- [ ] `llm_provider="modal"` with **neither** proxy token set (`--unauthenticated` endpoint) builds an
      `OpenAIChatModel` whose client `base_url == f"{settings.modal_endpoint_url}/v1"`, carries **no**
      Modal headers, and uses the placeholder `api_key="EMPTY"`. No network on construction. Unit-tested.
- [ ] An unexpected `llm_provider` value passed to `_build_model()` raises a clear `ValueError`
      (defensive; the settings `Literal` blocks it upstream). Unit-tested.
- [ ] `make ci` green, 0 warnings (`filterwarnings=["error"]`); the M1 capstone integration test
      (default `build` agent on the gemini path) still passes.

## Out of scope
- The cli startup guard for the new providers' required secrets + the proxy-token both-or-neither check
  — task 039.
- `.env.example` reorg + README docs + the MODAL_MODELS.md link — task 040.
- Provider fallback / `FallbackModel`, and provider auto-detection — non-goals (ADR-0005 §7).
- Any live request against OpenRouter/Modal (offline construction only; CI makes no network call).

## Log
