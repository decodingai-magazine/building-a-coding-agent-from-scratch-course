---
id: 039-cli-provider-secret-guard
feature: multi-provider-gateway
status: pending
---

# CLI: generalize the startup secret guard to the selected LLM Provider

Implements [ADR-0005](../docs/adr/0005-multi-llm-provider-support.md) §6 (per-provider startup guard).
Generalizes the task-004 `GEMINI_API_KEY`-only guard. Pairs with task 038. Modal requirements per
[`MODAL_MODELS.md`](../MODAL_MODELS.md) §5.3 / §6 (proxy tokens optional).
Depends on: 037 · Blocks: 040

## Scope

Generalize the no-key startup guard in `src/decode/cli.py` from the single `GEMINI_API_KEY` check to
validate the **selected** provider's required config, printing ONE friendly line on stderr (no
traceback) and exiting non-zero — same position (before `load_agent` / `--mode` validation and before
building the agent) and same style/wording as today's guard. Per provider:

- `gemini`: requires `GEMINI_API_KEY`.
- `openrouter`: requires `OPENROUTER_API_KEY`.
- `modal`: requires **only** `MODAL_ENDPOINT_URL` **and** `MODAL_ENDPOINT_MODEL` (the served model id).
  Proxy tokens are **NOT** required — an `--unauthenticated` endpoint is valid (MODAL_MODELS.md §5.3).
  **Both-or-neither proxy-token check:** if **exactly one** of `MODAL_PROXY_TOKEN_ID` /
  `MODAL_PROXY_TOKEN_SECRET` is set, that is a misconfiguration — emit a friendly line telling the user
  to set both (authenticated) or neither (`--unauthenticated`) and exit non-zero. Both-set and
  neither-set are both valid and pass.

Add a small helper, e.g. `_provider_config_error() -> str | None`, that reads `settings.llm_provider`
and returns a ready friendly message (or `None` when config is OK); the cli echoes it to stderr and
exits non-zero when non-`None`. Suggested messages (keep the existing gemini one verbatim for
backward-compat; name only the vars actually missing):

- gemini → `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).`
  (unchanged `_NO_KEY_MESSAGE`).
- openrouter → `Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY set in your environment or
  .env (see .env.example).`
- modal, missing url/model → `Decode: LLM_PROVIDER=modal needs <missing vars> set in your environment
  or .env (see .env.example).` where `<missing vars>` lists only the absent of
  `MODAL_ENDPOINT_URL` / `MODAL_ENDPOINT_MODEL`.
- modal, one proxy token only → `Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither — set
  both MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET, or neither for an --unauthenticated endpoint
  (see .env.example).`

### Tests — `tests/unit/decode/test_cli.py`
- Keep the existing gemini no-key test (friendly line names `GEMINI_API_KEY`, exit non-zero, no
  traceback).
- `LLM_PROVIDER=openrouter`, no `OPENROUTER_API_KEY` → one friendly stderr line naming
  `OPENROUTER_API_KEY` (and the provider), exit non-zero, no traceback.
- `LLM_PROVIDER=modal`, missing `MODAL_ENDPOINT_URL` (and/or `MODAL_ENDPOINT_MODEL`) → friendly line
  naming the missing var(s), exit non-zero, no traceback.
- `LLM_PROVIDER=modal`, url + model present, **both** proxy tokens set → guard passes (reaches
  `run_app`, exits 0 on empty stdin).
- `LLM_PROVIDER=modal`, url + model present, **neither** proxy token set (unauthenticated) → guard
  passes.
- `LLM_PROVIDER=modal`, url + model present, **exactly one** proxy token set → friendly both-or-neither
  line, exit non-zero, no traceback (test both orderings: id-only and secret-only).
- gemini / openrouter with their required secret(s) present → guard passes — parametrize/extend the
  autouse key fixture so the right provider + secret(s) are set.
- The guard runs **before** `--agent` / `--mode` validation for every provider (no raw
  `pydantic_ai.UserError` traceback escapes).

## Acceptance criteria

- [ ] `LLM_PROVIDER=gemini` (or unset) with no `GEMINI_API_KEY` prints the existing friendly
      `GEMINI_API_KEY` line on stderr and exits non-zero — unchanged from task 004. Unit-tested.
- [ ] `LLM_PROVIDER=openrouter` with no `OPENROUTER_API_KEY` prints one friendly stderr line naming
      `OPENROUTER_API_KEY` and the provider, no traceback, exits non-zero. Unit-tested.
- [ ] `LLM_PROVIDER=modal` missing `MODAL_ENDPOINT_URL` and/or `MODAL_ENDPOINT_MODEL` prints one
      friendly stderr line naming the **missing** var(s), no traceback, exits non-zero. Unit-tested.
- [ ] `LLM_PROVIDER=modal` with `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` present passes the guard
      when **both** proxy tokens are set **and** when **neither** is set (`--unauthenticated`); proxy
      tokens are not required. Unit-tested (both cases).
- [ ] `LLM_PROVIDER=modal` with **exactly one** proxy token set prints one friendly both-or-neither
      stderr line, no traceback, exits non-zero (tested id-only and secret-only). Unit-tested.
- [ ] With each provider's required config present, the guard passes and the REPL starts (exits 0 on
      empty stdin). The guard runs before agent/mode validation and before the agent is built — no raw
      `pydantic_ai.UserError` traceback for any provider. Unit-tested / verified via the CLI.
- [ ] **Working looks like:** `LLM_PROVIDER=openrouter decode` with no key → one friendly line + exit 1;
      export `OPENROUTER_API_KEY` → the REPL starts. `LLM_PROVIDER=modal decode` missing
      `MODAL_ENDPOINT_URL` → one friendly line naming it + exit 1; set url + model (no proxy tokens) →
      the REPL starts; set only one proxy token → both-or-neither line + exit 1.
- [ ] `make ci` green, 0 warnings.

## Out of scope
- Model construction / the `_build_model()` seam — task 038.
- Validating that a secret is **correct** (a wrong key fails at the first model request, not at
  startup) — only **presence** / both-or-neither shape is checked, matching the task-004 guard.
- `.env.example` reorg + README docs + the MODAL_MODELS.md link — task 040.

## Log
