---
id: 038-factory-build-model-seam
feature: multi-provider-gateway
status: done
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

- [x] `_build_model()` exists and `build_agent()` delegates model construction to it; the rest of
      `build_agent()` (deps_type, `output_type=[str, DeferredToolRequests]`, instructions,
      `register_tools`, the three hooks) is unchanged — the existing memory/skills/agent-prompt/
      visible-tool tests pass with no edits. Unit-tested.
- [x] `llm_provider="gemini"` (default) builds a `GoogleModel` on the google-gla path
      (`system == "google"`, `model_name == settings.gemini_model`) — identical to today; no
      `vertexai=` argument is ever passed. Unit-tested.
- [x] `llm_provider="openrouter"` builds an `OpenAIChatModel` with `system == "openrouter"` and
      `model_name == settings.openrouter_model`; building the agent makes no network call. Unit-tested.
- [x] `llm_provider="modal"` with **both** proxy tokens set builds an `OpenAIChatModel`
      (`model_name == settings.modal_endpoint_model`) whose `AsyncOpenAI` client `base_url ==
      f"{settings.modal_endpoint_url}/v1"` and whose request headers carry **both** `Modal-Key` and
      `Modal-Secret`. No network on construction. Unit-tested.
- [x] `llm_provider="modal"` with **neither** proxy token set (`--unauthenticated` endpoint) builds an
      `OpenAIChatModel` whose client `base_url == f"{settings.modal_endpoint_url}/v1"`, carries **no**
      Modal headers, and uses the placeholder `api_key="EMPTY"`. No network on construction. Unit-tested.
- [x] An unexpected `llm_provider` value passed to `_build_model()` raises a clear `ValueError`
      (defensive; the settings `Literal` blocks it upstream). Unit-tested.
- [x] `make ci` green, 0 warnings (`filterwarnings=["error"]`); the M1 capstone integration test
      (default `build` agent on the gemini path) still passes.

## Out of scope
- The cli startup guard for the new providers' required secrets + the proxy-token both-or-neither check
  — task 039.
- `.env.example` reorg + README docs + the MODAL_MODELS.md link — task 040.
- Provider fallback / `FallbackModel`, and provider auto-detection — non-goals (ADR-0005 §7).
- Any live request against OpenRouter/Modal (offline construction only; CI makes no network call).

## Log

### [SWE] 2026-06-26 — Implementation

**Files modified**
- `src/decode/agent/factory.py` — extracted model construction into a private `_build_model()`
  Provider Seam branching on `settings.llm_provider` (gemini / openrouter / modal); `build_agent()`
  now calls `model = _build_model()` and is otherwise byte-identical (deps_type, output_type,
  instructions, register_tools, the 3 hooks). Reframed the module + `build_agent()` docstrings as
  "configured LLM Provider" / "Provider Seam"; kept the google-gla/`vertexai=` + deferred-tool notes.
- `tests/unit/decode/agent/test_factory.py` — added the provider-seam section: a parametrized
  type/system/model_name test over gemini / openrouter / modal-authenticated / modal-unauthenticated,
  two dedicated modal-client-shape tests (both proxy headers + secret-as-api_key vs no headers +
  `api_key="EMPTY"`), and the defensive `ValueError` test. All offline (no model request).

**Live-verify (installed openai 2.43 / pydantic-ai 1.107)** — confirmed the real attribute paths so
assertions match the SDK, not a guess:
- The custom modal client is reachable at `agent.model._provider.client` (existing tests already
  touch `agent.model._provider`); `.client` is the public provider attribute.
- `client.base_url` is an `httpx.URL`; httpx normalizes a passed `.../v1` to `str(...) == ".../v1/"`
  (trailing slash) — so the modal tests assert against `f"{_MODAL_URL}/v1/"`.
- `dict(client.default_headers)` carries `Modal-Key` / `Modal-Secret` when authenticated, neither when
  not; `client.api_key` reads back the secret (auth) or the `"EMPTY"` placeholder (unauth).
- `OpenRouterProvider` → `model.system == "openrouter"`; modal's `OpenAIProvider` → `system == "openai"`.
- Constructing all four offline under `-W error` emits **no** warnings (`filterwarnings=["error"]` safe).

**Tests**
- Unit: 705 passing, 0 failing (`make pre-commit`); factory module 25/25.
- Integration: M1 capstone + M3 skills capstone pass — `make ci` 713 passing, 0 warnings.

**Acceptance criteria**
- [x] `_build_model()` exists, `build_agent()` delegates, rest unchanged — `tests/unit/decode/agent/test_factory.py` (all prior memory/skills/prompt/visible-tool tests pass untouched).
- [x] gemini → `GoogleModel`, `system=="google"`, no `vertexai=` — `...::test_build_agent_constructs_the_model_for_the_configured_provider[gemini]` (+ existing google tests).
- [x] openrouter → `OpenAIChatModel`, `system=="openrouter"`, offline — `...[openrouter]`.
- [x] modal both tokens → `OpenAIChatModel`, `base_url==.../v1`, both Modal headers — `...::test_modal_authenticated_client_carries_both_proxy_headers`.
- [x] modal neither token → no Modal headers, `api_key=="EMPTY"` — `...::test_modal_unauthenticated_client_has_no_modal_headers_and_placeholder_api_key`.
- [x] unexpected provider → `ValueError` — `...::test_build_model_rejects_an_unsupported_provider`.
- [x] `make ci` green, 0 warnings, M1 capstone passes.

**Evidence**

```
$ make ci   # tail
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================= 713 passed in 7.94s ==============================
```

End-to-end exercise of the real seam (offline, `build_agent()` per provider):
```
[gemini] GoogleModel system='google' model_name='gemini-2.5-flash'
[openrouter] OpenAIChatModel system='openrouter' model_name='qwen/qwen3-coder:free'
         base_url='https://openrouter.ai/api/v1/' api_key='or-fake' modal_headers={}
[modal authenticated] OpenAIChatModel system='openai' model_name='openai/gpt-oss-120b'
         base_url='https://me--decode.modal.run/v1/' api_key='ws-xyz' modal_headers={'Modal-Key': 'wk-abc', 'Modal-Secret': 'ws-xyz'}
[modal --unauthenticated] OpenAIChatModel system='openai' model_name='openai/gpt-oss-120b'
         base_url='https://me--decode.modal.run/v1/' api_key='EMPTY' modal_headers={}
[unsupported] raised ValueError: unsupported llm_provider: 'anthropic'
```

**Notes**
- Scope held to `factory.py` + its tests only — no cli guard (039), no `.env.example`/docs (040).
- Did NOT commit — handing off to the Tester first per the lifecycle.

### [Tester] 2026-06-26 16:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 112 files formatted; `ruff check` all passed; `uv lock --check` clean)
- Unit tests: 705 passed / 0 failed
- Integration tests: 8 passed / 0 failed (M1 capstone + M3 skills capstone)
- `make ci`: 713 passed / 0 failed
- Warnings: 0 (`filterwarnings=["error"]`; adversarial script also ran under `python -W error` with no warning)

**E2E adversarial pass** (real `build_agent()` / `_build_model()`, offline, all construction wrapped in a `socket.connect`-blocking guard → proves NO network call on construction)
- Happy path: build all four providers → `[gemini] GoogleModel system='google' model_name='gemini-2.5-flash'`; `[openrouter] OpenAIChatModel system='openrouter' model_name='qwen/qwen3-coder:free' base_url='https://openrouter.ai/api/v1/'`; `[modal-auth] OpenAIChatModel system='openai' base_url='.../v1/' api_key='ws-secret' Modal-Key='wk-id' Modal-Secret='ws-secret'`; `[modal-unauth] base_url='.../v1/' api_key='EMPTY' no Modal headers` (PASS — none tripped the network guard)
- Defensive: `_build_model()` with `llm_provider='anthropic'` → `ValueError: unsupported llm_provider: 'anthropic'` (PASS)
- Break path 1 (state edge: exactly one modal proxy token set): only-id and only-secret both → `api_key='EMPTY'`, no Modal headers, behaves as UNAUTHENTICATED — safe fallback, no crash; both-or-neither enforcement is task-039's cli guard (PASS — documented behavior)
- Break path 2 (boundary: whitespace-only proxy tokens `"   "`): truthy → treated as AUTHENTICATED with whitespace headers; matches the spec's literal `if token_id and token_secret`, no crash. Value/whitespace validation is task-039's guard (PASS — out-of-scope-here behavior noted)
- Break path 3 (malformed config: empty / trailing-slash `modal_endpoint_url`): empty → `base_url='/v1/'`; trailing slash → `base_url='.../com//v1/'` — garbage-in/garbage-out, no crash, no stack-trace leak; url hygiene is task-039 (PASS)
- Break path 4 (regression: gemini never passes `vertexai=`): constructed google client reports `vertexai=False` (SDK default, never set by us); entire run under `-W error` emitted no deprecation warning (PASS)
- Break path 5 (boundary: empty `gemini_api_key`): `GoogleProvider` raises a clear `UserError` (its own validation, pre-existing M1 behavior unchanged by 038, not a traceback/network call) (PASS)

**Acceptance criteria**
- [x] PASS — `_build_model()` exists, `build_agent()` delegates, rest unchanged — `git show HEAD:…factory.py` vs working tree: the `Agent(...)` block + `register_tools(agent)` + 3 `_register_*_instructions` calls are byte-identical (only `model = _build_model()` and the `logger.debug` string changed); all 18 prior memory/skills/prompt/visible-tool tests pass untouched (`test_factory.py` 25/25).
- [x] PASS — gemini → `GoogleModel`, `system=='google'`, `model_name=='gemini-2.5-flash'`, no `vertexai=` — `…::test_build_agent_constructs_the_model_for_the_configured_provider[gemini]`; `grep vertexai` only hits docstrings; client `vertexai=False` with 0 warnings under `-W error`.
- [x] PASS — openrouter → `OpenAIChatModel`, `system=='openrouter'`, `model_name=='qwen/qwen3-coder:free'`, no network — `…[openrouter]` + socket guard.
- [x] PASS — modal both tokens → `OpenAIChatModel`, `base_url=='…/v1/'`, both Modal headers, `api_key=='ws-secret'`, no network — `…::test_modal_authenticated_client_carries_both_proxy_headers` (factory.py:129-135).
- [x] PASS — modal neither token → no Modal headers, `api_key=='EMPTY'`, `base_url=='…/v1/'`, no network — `…::test_modal_unauthenticated_client_has_no_modal_headers_and_placeholder_api_key` (factory.py:136-138).
- [x] PASS — unexpected provider → `ValueError` — `…::test_build_model_rejects_an_unsupported_provider` (factory.py:143).
- [x] PASS — `make ci` green, 0 warnings, M1 capstone passes — 713 passed; `tests/integration/test_milestone1_capstone.py .` green.

**Evidence**
```
$ make ci   # tail
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================= 713 passed in 8.24s ==============================

$ uv run pytest tests/unit/decode/agent/test_factory.py   # 25 passed (18 pre-existing + 7 new)
… test_build_agent_constructs_the_model_for_the_configured_provider[gemini|openrouter|modal-authenticated|modal-unauthenticated] PASSED
… test_modal_authenticated_client_carries_both_proxy_headers PASSED
… test_modal_unauthenticated_client_has_no_modal_headers_and_placeholder_api_key PASSED
… test_build_model_rejects_an_unsupported_provider PASSED
============================== 25 passed in 2.03s ==============================
```

**Other issues found** (non-blocking — all explicitly task-039 scope per this task's "Out of scope")
- The factory trusts upstream validation: a partial modal token pair silently degrades to unauthenticated; whitespace-only tokens are treated as authenticated; an empty `modal_endpoint_url` yields `base_url='/v1/'`. None crash or leak. These are precisely the both-or-neither / required-secret checks the cli guard (task 039) owns — flagging so 039 covers them.
- `git diff --name-only` = `factory.py` + `test_factory.py` + this task file only; no 039/040 leakage.

**VERDICT: PASS**
