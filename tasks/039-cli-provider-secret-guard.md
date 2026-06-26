---
id: 039-cli-provider-secret-guard
feature: multi-provider-gateway
status: done
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

- [x] `LLM_PROVIDER=gemini` (or unset) with no `GEMINI_API_KEY` prints the existing friendly
      `GEMINI_API_KEY` line on stderr and exits non-zero — unchanged from task 004. Unit-tested.
- [x] `LLM_PROVIDER=openrouter` with no `OPENROUTER_API_KEY` prints one friendly stderr line naming
      `OPENROUTER_API_KEY` and the provider, no traceback, exits non-zero. Unit-tested.
- [x] `LLM_PROVIDER=modal` missing `MODAL_ENDPOINT_URL` and/or `MODAL_ENDPOINT_MODEL` prints one
      friendly stderr line naming the **missing** var(s), no traceback, exits non-zero. Unit-tested.
- [x] `LLM_PROVIDER=modal` with `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` present passes the guard
      when **both** proxy tokens are set **and** when **neither** is set (`--unauthenticated`); proxy
      tokens are not required. Unit-tested (both cases).
- [x] `LLM_PROVIDER=modal` with **exactly one** proxy token set prints one friendly both-or-neither
      stderr line, no traceback, exits non-zero (tested id-only and secret-only). Unit-tested.
- [x] With each provider's required config present, the guard passes and the REPL starts (exits 0 on
      empty stdin). The guard runs before agent/mode validation and before the agent is built — no raw
      `pydantic_ai.UserError` traceback for any provider. Unit-tested / verified via the CLI.
- [x] **Working looks like:** `LLM_PROVIDER=openrouter decode` with no key → one friendly line + exit 1;
      export `OPENROUTER_API_KEY` → the REPL starts. `LLM_PROVIDER=modal decode` missing
      `MODAL_ENDPOINT_URL` → one friendly line naming it + exit 1; set url + model (no proxy tokens) →
      the REPL starts; set only one proxy token → both-or-neither line + exit 1.
- [x] `make ci` green, 0 warnings.

## Out of scope
- Model construction / the `_build_model()` seam — task 038.
- Validating that a secret is **correct** (a wrong key fails at the first model request, not at
  startup) — only **presence** / both-or-neither shape is checked, matching the task-004 guard.
- `.env.example` reorg + README docs + the MODAL_MODELS.md link — task 040.

## Log

### [SWE] 2026-06-26 — Implementation

**Files modified**
- `src/decode/cli.py` — generalized the task-004 `GEMINI_API_KEY`-only startup guard into a
  `_provider_config_error() -> str | None` helper that validates the selected `settings.llm_provider`'s
  required config (gemini → `GEMINI_API_KEY`; openrouter → `OPENROUTER_API_KEY`; modal →
  `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` only, plus a both-or-neither proxy-token check). Added
  the openrouter / modal-both-or-neither message constants; `cli()` now echoes the helper's message to
  stderr and exits non-zero, in the same position (before `--agent` / `--mode` validation and before
  the agent is built).
- `tests/unit/decode/test_cli.py` — broadened the autouse key fixture to `_dummy_provider_config`
  (seeds every provider's config so any selected provider passes by default) + a `_select_provider`
  helper; added 11 direct `_provider_config_error` contract tests and 10 CLI-behaviour tests (exit
  code / friendly line / no-traceback / guard-ordering, parametrized across providers).

**Tests**
- Unit: 729 passing, 0 failing (`make pre-commit`); CLI module 42/42.
- Integration: 8 passing (`make ci` total 737) — no infra changes, capstones unaffected.

**Acceptance criteria**
- [x] gemini no-key → verbatim `GEMINI_API_KEY` line, exit non-zero — `test_cli_with_no_gemini_key_exits_nonzero_with_a_friendly_line`, `test_provider_config_error_gemini_missing_key_returns_the_unchanged_message`.
- [x] openrouter no-key → friendly `OPENROUTER_API_KEY` line — `test_cli_openrouter_with_no_key_exits_nonzero_with_a_friendly_line`.
- [x] modal missing url/model → names only the absent var(s) — `test_provider_config_error_modal_missing_{url,model,both}_*`, `test_cli_modal_missing_url_exits_nonzero_naming_the_missing_var`.
- [x] modal both / neither tokens pass — `test_cli_modal_both_proxy_tokens_passes_the_guard`, `test_cli_modal_unauthenticated_passes_the_guard`.
- [x] modal exactly-one token (id-only + secret-only) → both-or-neither line — `test_cli_modal_only_token_{id,secret}_exits_nonzero_both_or_neither`.
- [x] each provider configured → REPL starts; guard precedes agent/mode, no UserError — `test_cli_with_each_provider_configured_starts_the_real_repl`, `test_cli_provider_guard_precedes_agent_and_mode_validation`.
- [x] `make ci` green, 0 warnings.

**Evidence**

`make ci` (tail):
```
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================= 737 passed in 8.37s ==============================
uv lock --check · ruff format --check (112 files) · ruff check → All checks passed!
```

Real-CLI e2e (env vars override `.env`; stdin redirected / pty for the happy path):
```
$ GEMINI_API_KEY= decode
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).            exit=1
$ LLM_PROVIDER=openrouter OPENROUTER_API_KEY= decode
Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY set in your environment or .env (...). exit=1
$ LLM_PROVIDER=modal MODAL_ENDPOINT_URL= MODAL_ENDPOINT_MODEL=m decode
Decode: LLM_PROVIDER=modal needs MODAL_ENDPOINT_URL set in your environment or .env (...).      exit=1
$ LLM_PROVIDER=modal MODAL_ENDPOINT_URL=… MODAL_ENDPOINT_MODEL=m MODAL_PROXY_TOKEN_SECRET=ws-1 decode
Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither — set both MODAL_PROXY_TOKEN_ID
and MODAL_PROXY_TOKEN_SECRET, or neither for an --unauthenticated endpoint (...).               exit=1
# modal url+model, NEITHER token (--unauthenticated), driven via pty + Ctrl-D:
Decode - type a line; /quit exits.   …   Decode - bye.                                          exit=0
```

**Notes**
- The modal-unauthenticated happy path emits an `OSError [Errno 22]` only when stdin is `</dev/null`
  at a raw shell — that is prompt_toolkit refusing to build its input on `/dev/null`, downstream of
  the guard (the guard had already passed: no friendly line printed). Driven through a real
  pseudo-terminal it boots the REPL and exits 0 cleanly (shown above); the
  `test_cli_with_each_provider_configured_starts_the_real_repl[modal-*]` cases prove the same via
  `CliRunner`.
- Observed while testing (not in scope here): this repo's `.env` sets `MODAL_PROXY_TOKEN_KEY`
  (not the `MODAL_PROXY_TOKEN_ID` the settings field maps from) alongside `MODAL_PROXY_TOKEN_SECRET`,
  so a real `LLM_PROVIDER=modal` run reading `.env` would trip the new both-or-neither guard. That is
  a `.env` / docs concern for task 040 (the `.env.example` reorg), not this code change.
- No commit yet — handing off to the Tester first.

### [Tester] 2026-06-26 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 112 files clean, `ruff check` clean)
- Unit tests: 729 passed / 0 failed (CLI module 42/42)
- Integration tests: 8 passed / 0 failed
- `make ci`: 737 passed; `uv lock --check` clean
- Warnings: 0 (re-ran `tests/unit/decode/test_cli.py` with explicit `-W error` → 42 passed)

**E2E adversarial pass** (real CLI, env vars override `.env`; failure paths exit at the guard before prompt_toolkit; happy paths driven through a real pty + `/quit`)
- Happy path (4 providers configured): `uv run decode` under pty → banner renders, `/quit`, exit 0, no guard line — gemini / openrouter / modal-unauth / modal-auth all PASS.
- Break path 1 (missing config, per provider): gemini → `Decode: set GEMINI_API_KEY …` ; openrouter → names `OPENROUTER_API_KEY`+provider ; modal-no-url → names only `MODAL_ENDPOINT_URL` ; modal-no-url+model → names both. Each: one stderr line, exit 1, 0 tracebacks. PASS.
- Break path 2 (modal both-or-neither, both orderings): id-only AND secret-only → both-or-neither line, exit 1, no traceback. PASS.
- Break path 3 (guard ordering): `--agent nope --mode bogus` + missing key → provider line wins, `nope`/`bogus`/`UserError` absent, no traceback (exit 1). Reverse (provider configured + bad `--agent`/`--mode`) → downstream agent/mode guards still fire correctly. PASS.
- Break path 4 (boundary: whitespace-only secret `GEMINI_API_KEY='   '`): guard PASSES (no friendly line) — presence-only, no stripping, consistent with the task-004 guard and the task's explicit "presence only" scope. PASS (expected behaviour).

**Acceptance criteria**
- [x] PASS — gemini no-key → verbatim line — real CLI emits `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` (exit 1); `_NO_KEY_MESSAGE` byte-identical between HEAD and working tree; `test_provider_config_error_gemini_missing_key_returns_the_unchanged_message`.
- [x] PASS — openrouter no-key → names var+provider — real CLI line + `test_cli_openrouter_with_no_key_exits_nonzero_with_a_friendly_line`.
- [x] PASS — modal missing url/model → names only absent var(s) — real CLI (url-only vs both) + `test_provider_config_error_modal_missing_{url,model,both}_*`, `cli.py:76-88`.
- [x] PASS — modal both/neither tokens pass — pty happy paths + `test_cli_modal_both_proxy_tokens_passes_the_guard`, `test_cli_modal_unauthenticated_passes_the_guard`.
- [x] PASS — modal exactly-one token (id-only + secret-only) → both-or-neither — real CLI both orderings + `test_cli_modal_only_token_{id,secret}_exits_nonzero_both_or_neither`.
- [x] PASS — each provider configured → REPL starts, guard precedes agent/mode, no `UserError` — pty (exit 0) + `test_cli_provider_guard_precedes_agent_and_mode_validation`, `cli.py:129-133` (guard before `load_agent` at `cli.py:139`).
- [x] PASS — "Working looks like" walkthrough reproduced end-to-end on the real CLI (see E2E pass above).
- [x] PASS — `make ci` green, 0 warnings.

**Evidence**
```
$ make ci   → 737 passed in 8.04s ; uv lock --check / ruff format / ruff check clean
$ LLM_PROVIDER=modal MODAL_ENDPOINT_URL= MODAL_ENDPOINT_MODEL=m … uv run decode </dev/null
Decode: LLM_PROVIDER=modal needs MODAL_ENDPOINT_URL set in your environment or .env (see .env.example).   exit=1
$ LLM_PROVIDER=modal …URL=https://x.modal.run MODAL_ENDPOINT_MODEL=m MODAL_PROXY_TOKEN_ID=wk-1 MODAL_PROXY_TOKEN_SECRET= uv run decode </dev/null
Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither — set both MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET, or neither for an --unauthenticated endpoint (see .env.example).   exit=1
$ pty driver: [gemini|openrouter|modal-unauth|modal-auth] exit=0 banner=True guard_line=False → PASS
```

**Other issues found**
- None blocking. Scope held to `src/decode/cli.py` + `tests/unit/decode/test_cli.py` (+ this task file); `git diff --name-only` shows no `docs/` / `.env` / `.env.example` / `settings.py` leakage.
- Out of scope (confirmed, not a 039 defect): repo `.env` uses the stale `MODAL_PROXY_TOKEN_KEY` (no settings field) alongside `MODAL_PROXY_TOKEN_SECRET`, so a real `.env`-driven modal run trips the new both-or-neither guard — a task-040 `.env`/docs fix.
- Minor doc nit (pre-existing, not 039): `AGENTS.md` "Launch" text quotes a lowercase `decode:` for the gemini line, but the implemented (and task-004) constant is `Decode:`. Task 039 correctly preserves the committed `Decode:` wording verbatim; the AGENTS.md casing drift predates this task.

**VERDICT: PASS**
