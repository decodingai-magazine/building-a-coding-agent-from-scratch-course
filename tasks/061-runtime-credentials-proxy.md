---
id: 061-runtime-credentials-proxy
feature: kitaru-runtime
status: pending
---

# Credentials proxy: resolve the model key via Kitaru secrets at construction (flow mode)

Tags: `runtime`, `infra`, `agent`
Depends on: #058
Blocks: #062

This task implements ADR-0008 §5 and honors the AGENTS.md invariant *"secrets never reach the model
or the sandbox payload."* In **flow mode**, model construction resolves the provider API key through
Kitaru secrets instead of the direct `settings.<provider>_api_key.get_secret_value()` at
`agent/factory.py:115`, so a (later, deployed) flow payload carries **handles, not raw keys**.
Interactive in-process runs keep reading `SecretStr` from settings unchanged.

**Honest caveat (drives the AC below):** the Kitaru secrets proxy for the *model key* is the
**least-exampled** surface — no Agent Harness Platform example wires it; the credential-proxy example
injects HTTP headers for the *sandbox*, and the docs-backed PydanticAI path is **env injection**
(`@flow(image=kitaru.ImageSettings(secret_environment_from=[...]))`) because the adapter needs a
concrete model at construction. So this task **verifies the secrets API against the installed SDK /
context7 first**, and ships the env-injection seam as the documented fallback.

## Scope

Verify against the installed SDK + context7 `/kitaru/guides/secrets.md`,
`/kitaru/guides/secrets-and-model-registration.md`, `/kitaru/adapters/pydantic-ai.md` **before
coding** (pre-1.0). Confirmed API at grooming: `from kitaru import create_secret, get_secret,
delete_secret`; `create_secret(name, {KEY: val}, private=True)`; `get_secret(name).get("KEY")` /
`.values`.

- **Gate:** all of this is behind `settings.runtime_credentials_proxy_enabled` (default `False`, from
  057). When `False` (the default) **or** interactive, `_build_model()` is byte-unchanged (reads
  `SecretStr` from settings). When `True` **and** in flow mode, resolve the key via Kitaru.
- **Primary path (explicit handle):** at the model-construction seam, when enabled+flow-mode, read
  the provider key with `get_secret(settings.runtime_secret_name).get("<PROVIDER>_API_KEY")` (e.g.
  `GEMINI_API_KEY` / `OPENROUTER_API_KEY`) and pass it to the provider exactly where the `SecretStr`
  value is used today. Thread flow-mode awareness into `_build_model` cleanly (a small param or a
  module seam — do not read `os.environ` deep in the factory; AGENTS.md).
- **Documented fallback (env injection):** if the explicit-handle path does not round-trip on the
  local stack, the flow declares the secret on its image —
  `@flow(image=kitaru.ImageSettings(secret_environment_from=[settings.runtime_secret_name]))` — so the
  provider SDK reads the key from the injected env at construction. Pick whichever round-trips on the
  installed SDK; record the choice and why in the task log.
- **Operator setup:** document creating the secret once —
  `kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…` (CLI) or `create_secret("decode-llm-creds",
  {"GEMINI_API_KEY": …}, private=True)` (Python) — and that the raw key then lives only in Kitaru, not
  in the flow payload. Add the `RUNTIME_CREDENTIALS_PROXY_ENABLED` / `RUNTIME_SECRET_NAME` usage to
  the README runtime section.
- **Invariant check:** the resolved-handle path must not log or echo the raw key; the model is
  constructed with the key but the *flow payload* (the serialized `run_agent_task` arguments) carries
  only the task string + the secret *name*.

## Acceptance criteria

- [ ] **Verify-first:** the SWE log records the secrets API confirmed against the installed SDK
      (`get_secret(...).get(...)` shape) and which path round-trips on the local stack
      (explicit-handle vs env-injection); the shipped code matches that finding.
- [ ] With `runtime_credentials_proxy_enabled=False` (default) **or** interactive mode,
      `_build_model()` behavior is byte-identical to today (reads `settings.<provider>_api_key`); the
      existing factory/provider tests pass unchanged.
- [ ] With `runtime_credentials_proxy_enabled=True` in flow mode, the provider key is resolved from
      Kitaru (`get_secret(runtime_secret_name)` **or** the env injected via `secret_environment_from`),
      not from `settings.<provider>_api_key`; a unit test patches `kitaru.get_secret` (or the env seam)
      and asserts the model is constructed with the secret-sourced key and that `settings.gemini_api_key`
      is **not** read on that path.
- [ ] The flow payload carries only the task string + the secret **name** — never the raw key; a test
      asserts the raw key value does not appear in the serialized flow arguments / logs.
- [ ] Works for at least the default `gemini` provider end-to-end through the seam (offline, patched);
      the openrouter/modal branches are covered or explicitly deferred-with-reason in the log.
- [ ] If neither Kitaru secrets path round-trips on the installed SDK, the task ships the
      env-injection fallback and an Open-Question note rather than a broken handle path (no silent
      raw-key leak).
- [ ] `make ci` green, 0 warnings.

## User stories

### Story: A deployed flow never carries the raw key
1. An operator runs `kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…` once and sets
   `RUNTIME_CREDENTIALS_PROXY_ENABLED=true`.
2. A `decode run` flow constructs the Gemini model with the key resolved from the Kitaru secret.
3. Inspecting the flow execution's arguments shows only the task and the secret name — the raw key is
   not in the payload.

### Story: The interactive REPL is unaffected
1. A developer runs bare `decode` with `GEMINI_API_KEY` in `.env` and the proxy disabled (default).
2. The model is constructed exactly as before (SecretStr from settings) — no Kitaru secret lookup,
   no behavior change.

### Story: The proxy is opt-in and safe by default
1. A developer enables `RUNTIME_CREDENTIALS_PROXY_ENABLED=true` but has not created the secret.
2. The flow surfaces Kitaru's missing-secret error (or the documented env-injection fallback), not a
   raw traceback or a silent fallback that leaks the settings key into the payload.

## Out of scope
- Sandbox HTTP header injection / the proxy-container pattern (that is the sandbox feature, a later
  step) — this task is only the **model-construction** credential seam.
- Model-alias registration (`kitaru model register`) as the primary path — noted as an option, not
  required for MVP.
- Rotating/deleting secrets tooling beyond documenting `create_secret`/`delete_secret`.

## Log
