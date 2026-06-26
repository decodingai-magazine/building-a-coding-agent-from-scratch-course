---
id: 040-providers-docs-and-env
feature: multi-provider-gateway
status: done
---

# Docs + .env.example: LLM providers section, MODAL_MODELS.md link, tool-calling caveat

Implements [ADR-0005](../docs/adr/0005-multi-llm-provider-support.md) §7 (docs surface + non-goals) and
delivers the "run decode for free" goal. Documents the seam built in tasks 037-039.
[`MODAL_MODELS.md`](../MODAL_MODELS.md) is the canonical Modal model-selection + endpoint-setup reference.
Depends on: 037, 038, 039 · Blocks: —

## Scope

Make the multi-provider feature **discoverable** and free-tier-friendly. Code already works (037-039);
this task is `.env.example` + README + a light pass on MODAL_MODELS.md only. **Do not duplicate the
Modal model catalog** — link to MODAL_MODELS.md.

### `.env.example` — reorganize the inference section
- Present the three providers behind `LLM_PROVIDER` (`gemini` | `openrouter` | `modal`), default
  `gemini` (existing `.env` keeps working), with short **free-tier notes**: Modal (open-source models,
  $30 free credits), OpenRouter (`:free` model options), Gemini (free credits).
- Annotate the new vars added in 037 with one-line guidance: `LLM_PROVIDER`, `OPENROUTER_MODEL`, and
  the four Modal endpoint vars (`MODAL_ENDPOINT_URL`, `MODAL_ENDPOINT_MODEL`, `MODAL_PROXY_TOKEN_ID`,
  `MODAL_PROXY_TOKEN_SECRET`). Note the proxy tokens are **optional** (omit both for an
  `--unauthenticated` endpoint).
- **Clearly distinguish** the two Modal credential pairs so a reader can't conflate them:
  - `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` = **account** tokens for `modal token set` / the CLI
    (existing; sandbox + deploy auth — MODAL_MODELS.md §5.1).
  - `MODAL_PROXY_TOKEN_ID` (wk-...) / `MODAL_PROXY_TOKEN_SECRET` (ws-...) = **endpoint proxy-token**
    request headers (`Modal-Key` / `Modal-Secret`) from `modal workspace proxy-tokens create` (new;
    how `decode` calls the Auto Endpoint — MODAL_MODELS.md §5.3).
- Point the Modal block at MODAL_MODELS.md for model selection + endpoint setup; keep `.env.example`
  lean (no catalog).

### `README.md` — add a lean "LLM providers" section (near `## Configure`)
- The three-line free-tier blurb: Modal (open-source models + $30 credits), OpenRouter (free models),
  Gemini (free credits); the opt-in is `LLM_PROVIDER=<name>` + that provider's secret(s); default is
  `gemini` (existing `.env` keeps working).
- Update the `## Requirements` note and the Milestone-1 status blurb so "later milestones add OpenRouter
  / Modal" reads as **shipped** (link ADR-0005).
- **Tool-calling + streaming caveat** (for both OpenRouter and Modal): the agent loop requires a model
  that supports **tool-calling + streaming**; the shipped `OPENROUTER_MODEL` default is known-good;
  "if you swap models, pick a tool-capable one"; the Modal-served model must likewise support
  tool-calling + streaming (the shipped `MODAL_ENDPOINT_MODEL` default `openai/gpt-oss-120b` is the
  best-fit pick).
- **One-line Modal opt-in path**, then LINK to MODAL_MODELS.md as the canonical reference for model
  selection, `modal endpoint create`, `modal workspace proxy-tokens create`, and wiring — e.g. "set
  `LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` (+ proxy tokens unless
  `--unauthenticated`); see [`MODAL_MODELS.md`](MODAL_MODELS.md) for picking a model and creating the
  endpoint." Do **not** restate the catalog or the full CLI walkthrough in README.

### `MODAL_MODELS.md` — light pass on §6 to reflect the SHIPPED wiring
- §6 currently frames the wiring as "already sketched in `.env.example`" / planned. Update it to the
  shipped reality: the env vars exist (`MODAL_ENDPOINT_URL` / `MODAL_ENDPOINT_MODEL` /
  `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` — already the names in §6), the selector is
  `LLM_PROVIDER=modal`, the gateway builds `base_url = f"{MODAL_ENDPOINT_URL}/v1"` via the factory's
  Provider Seam, and the §6.3 auth nuance is implemented: both proxy tokens → `Modal-Key` /
  `Modal-Secret` default headers; `--unauthenticated` → no headers + placeholder `api_key="EMPTY"`.
  Keep the catalog (§1-§5) intact — this is a wording/accuracy pass on §6 only.

Use the canonical glossary terms **LLM Provider** and **Provider Seam** throughout; do not contradict
ADR-0005.

## Acceptance criteria

- [x] `.env.example` presents the three providers behind `LLM_PROVIDER` with free-tier notes; the new
      vars carry guidance; the **account-token** vars (`MODAL_TOKEN_*`) and the new **endpoint
      proxy-token** vars (`MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET`) are unmistakably
      distinguished (a reader can tell which is the CLI auth vs the endpoint request headers); the
      proxy tokens are noted as optional (`--unauthenticated`).
- [x] `README.md` has a lean "LLM providers" section naming Modal / OpenRouter / Gemini with their
      free-tier story, the opt-in (`LLM_PROVIDER=<name>` + secret), and that the default `gemini` keeps
      existing `.env` files working; the Milestone-1 status note no longer calls OpenRouter/Modal
      "later".
- [x] The tool-calling + streaming caveat is documented for both OpenRouter and Modal, including the
      "if you swap models, pick a tool-capable one" warning, and names the shipped `OPENROUTER_MODEL`
      and `MODAL_ENDPOINT_MODEL` defaults as known-good.
- [x] README gives a one-line Modal opt-in path and **links** to `MODAL_MODELS.md` for model selection +
      endpoint setup + wiring; it does **not** duplicate the model catalog or the full CLI walkthrough.
- [x] `MODAL_MODELS.md` §6 reflects the shipped wiring (`LLM_PROVIDER=modal`, the four final var names,
      the Provider Seam `base_url`/`/v1` path, and the implemented §6.3 auth nuance incl. the
      `--unauthenticated` / `api_key="EMPTY"` case) rather than "planned/sketched"; §1-§5 are unchanged.
- [x] Canonical glossary terms (**LLM Provider**, **Provider Seam**) used throughout; no contradiction
      with ADR-0005.
- [x] `make ci` green (docs + `.env.example` only; no test regressions).

## Out of scope
- Code changes (settings / factory / cli) — tasks 037-039.
- An automated test asserting `.env.example` mirrors `settings` (none exists today; not introduced here).
- A live end-to-end run against a real OpenRouter/Modal account (manual e2e, not CI).
- Re-benchmarking or editing the MODAL_MODELS.md catalog (§1-§5) — only the §6 wiring wording is touched.

## Log

### [SWE] 2026-06-26 — Implementation

**Files modified**
- `.env.example` — reorganized the inference section into one contiguous LLM Provider block behind
  `LLM_PROVIDER` (default `gemini`); free-tier notes for all three; per-var guidance for
  `OPENROUTER_MODEL` + the four Modal endpoint vars; the two Modal credential pairs split into a
  labelled `(1) ACCOUNT tokens` vs `(2) ENDPOINT vars` (with OPTIONAL both-or-neither proxy tokens,
  now commented out so a copied `.env` is `--unauthenticated`-ready); points the Modal block at
  MODAL_MODELS.md (no catalog). Logging block relocated below the providers.
- `README.md` — hero line + Milestone-1 status note un-"later" the providers (links ADR-0005);
  `## Requirements` reframed to "an API key for one LLM provider"; new lean `## LLM providers`
  section (3-line free-tier blurb, opt-in, default keeps `.env` working, tool-calling+streaming
  caveat naming both shipped defaults, one-line Modal opt-in linking to MODAL_MODELS.md).
- `MODAL_MODELS.md` — §6 rewritten from "sketched/planned" to the shipped wiring (`LLM_PROVIDER=modal`,
  the four var names, the Provider Seam `agent/factory._build_model()` building `base_url={url}/v1`,
  the implemented §6.3 auth nuance incl. `--unauthenticated`/`api_key="EMPTY"` + the both-or-neither
  guard). §1-§5 catalog untouched.
- `tasks/040-providers-docs-and-env.md` — status `in-progress`; acceptance checkboxes ticked.

**Tests**
- Unit: 729 passing, 0 failing. Integration: 8 passing (capstones). Full `make ci`: 737 passing.
- No new tests written — docs/config-only task, no decidable code contract (settings/factory/cli are
  tasks 037-039, already covered). Out-of-scope per the task: no `.env.example`↔`settings` mirror test.

**Acceptance criteria** — all 7 met (see ticked boxes above). All verified by inspection + the e2e
checks below; none are `[HUMAN]`.

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check all pass ...
============================= 737 passed in 8.25s ==============================

# e2e: reorganized .env.example loaded through the real Settings reader
parses OK -> True
llm_provider (default) = gemini
gemini_model           = gemini-2.5-flash
openrouter_model       = qwen/qwen3-coder:free
modal_endpoint_model   = openai/gpt-oss-120b
modal_endpoint_url      = '' (no default)
proxy tokens commented out -> both empty: True

# stale-token sweep across committed docs
$ grep -rn "MODAL_PROXY_TOKEN_KEY" .env.example README.md MODAL_MODELS.md
(no matches)
```

**Notes**
- **Stale `MODAL_PROXY_TOKEN_KEY` reconciliation:** grepped the *whole* repo. The committed docs
  (`MODAL_MODELS.md` §5.4 / §6, `.env.example`) already use the consistent `MODAL_PROXY_TOKEN_ID` /
  `MODAL_PROXY_TOKEN_SECRET` names — task 037's SWE had already fixed them, so no doc change was
  needed for that item; verified by the grep above. The only remaining `MODAL_PROXY_TOKEN_KEY`
  occurrences are in historical task logs (037/039) and the user's gitignored local `.env` — both
  out of bounds (do NOT edit the user's `.env`).
- README headings matched the assumed structure (`## Configure`, `## Requirements`, the status
  blurb), so no adaptation was needed; the new `## LLM providers` section sits right after
  `## Configure` and its `#llm-providers` anchor is referenced from both the status note and
  Requirements.
- Glossary terms **LLM Provider** and **Provider Seam** used as-is (not recreated); no contradiction
  with ADR-0005 (the both-or-neither, `--unauthenticated`/`EMPTY`, and no-fallback facts all match).
- Decided to comment out the OPTIONAL Modal proxy tokens in `.env.example` (they were active
  `=changeme` before): this makes a freshly-copied `.env` express the both-or-neither invariant
  correctly (neither set → `--unauthenticated`-ready) instead of landing in a "both set to changeme"
  state. Verified via the Settings e2e load above. No code touched.

### [Tester] 2026-06-26 16:28 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`uv lock --check` ✓ · `ruff format --check` 112 files ✓ · `ruff check` all pass ✓)
- Unit + integration: 737 passed / 0 failed (`make ci`, exit 0)
- Warnings: 0 (`filterwarnings=["error"]`; grep of CI log for warning/deprecat → none)

**E2E adversarial pass** (docs/config task → doc-correctness + example-config loading)
- Happy path: copied `.env.example` → real `Settings(_env_file=...)` reader (ambient provider vars
  stripped for isolation) → parses OK; `llm_provider=gemini`, `openrouter_model='qwen/qwen3-coder:free'`,
  `modal_endpoint_model='openai/gpt-oss-120b'`, `modal_endpoint_url=''`, both proxy tokens empty. (PASS)
- Break path 1 (boundary — half-set proxy token): `Settings(llm_provider=modal, url+model, only
  MODAL_PROXY_TOKEN_ID)` then `_provider_config_error()` → friendly both-or-neither message naming both
  vars + pointing to `.env.example`; symmetric for only `_SECRET`. Matches the `.env.example` "set BOTH
  or omit BOTH" promise. (PASS)
- Break path 2 (state edge — neither token, `--unauthenticated` shape): same config, neither token →
  `None` (valid). This is exactly the shape a copied `.env.example` (proxy tokens commented out)
  produces. (PASS)
- Break path 3 (missing required config — `LLM_PROVIDER=modal` with URL left commented/empty):
  `_provider_config_error()` → `"Decode: LLM_PROVIDER=modal needs MODAL_ENDPOINT_URL set ... (see
  .env.example)."` — friendly, names the absent var, no traceback. (PASS)
- Test-isolation note: an unisolated first run returned `None` for the half-set case because
  `Settings()` was still reading the user's gitignored local `.env`; re-ran with `_env_file=None` +
  ambient vars stripped → correct both-or-neither behavior confirmed. Not a code defect.
- Doc-correctness adversarial: every claim the three docs make is backed by shipped code —
  `cli.py:91` both-or-neither (`bool(id) != bool(secret)`), `factory.py:126` `base_url={url}/v1`,
  `factory.py:134` `Modal-Key`/`Modal-Secret` default headers, `factory.py:138` `api_key="EMPTY"`.
  No doc overstates the implementation.

**Acceptance criteria**
- [x] PASS — `.env.example` presents 3 providers behind `LLM_PROVIDER` w/ free-tier notes; ACCOUNT
      (`MODAL_TOKEN_*`, labelled "(1) ACCOUNT tokens") vs ENDPOINT proxy-token headers
      (`MODAL_PROXY_TOKEN_*`, "(2) ENDPOINT vars … PROXY tokens (OPTIONAL)") unmistakably split; proxy
      tokens noted optional/`--unauthenticated`. Evidence: `.env.example:5-45`.
- [x] PASS — `README.md` lean `## LLM providers` section names Modal/OpenRouter/Gemini + free-tier
      story + `LLM_PROVIDER=<name>` opt-in + "default gemini keeps existing `.env` working"; M1 status
      no longer says OpenRouter/Modal are "later" (now "selectable LLM providers … run it for free").
      Evidence: `README.md:7,61-81`.
- [x] PASS — tool-calling + streaming caveat for BOTH providers, "if you swap models, pick a
      tool-capable one", names shipped `OPENROUTER_MODEL=qwen/qwen3-coder:free` +
      `MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b`. Evidence: `README.md:77`; mirrored `.env.example:21-22`.
- [x] PASS — one-line Modal opt-in that LINKS to `MODAL_MODELS.md`; no catalog/full-CLI-walkthrough
      duplicated (only a one-line pointer naming `modal endpoint create` / `modal workspace
      proxy-tokens create`). Evidence: `README.md:79`; link target file exists.
- [x] PASS — `MODAL_MODELS.md` §6 reflects shipped wiring: `LLM_PROVIDER=modal`, the four final var
      names, Provider Seam `base_url={url}/v1`, both-headers vs `--unauthenticated`/`api_key="EMPTY"`,
      both-or-neither guard cited. §1-§5 byte-unchanged (`git diff -U0` hunks all within lines 257-284;
      §6 starts L255, §7 L287). Evidence: `MODAL_MODELS.md:255-285`.
- [x] PASS — glossary terms **LLM Provider** (`docs/glossary.md:37`) + **Provider Seam** (`:38`) used
      as-is; no contradiction with ADR-0005 (both-or-neither, `EMPTY`, no-fallback, two-credential-scope
      facts all match).
- [x] PASS — `make ci` green, docs/`.env.example` only, no test regressions (737 passed, exit 0).

**Stale-name sweep (committed docs)**
- `grep MODAL_PROXY_TOKEN_KEY .env.example README.md MODAL_MODELS.md` → 0 hits.
- `grep -E "MODAL_BASE_URL|MODAL_MODEL[^S]|MODAL_KEY|MODAL_SECRET"` (same 3 files) → 0 hits.

**Scope**
- `git diff --name-only` = `.env.example`, `MODAL_MODELS.md`, `README.md`, `tasks/040-…md` only.
  No code/settings/factory/cli leakage.

**Evidence**
```
$ make ci
uv lock --check → Resolved 176 packages
uv run ruff format --check → 112 files already formatted
uv run ruff check → All checks passed!
============================= 737 passed in 9.85s ==============================
make ci exit=0   (CI-log warning/deprecation grep → none)

# .env.example through the real Settings reader (isolated)
parses OK -> True · llm_provider=gemini · openrouter_model='qwen/qwen3-coder:free'
modal_endpoint_model='openai/gpt-oss-120b' · modal_endpoint_url='' · both proxy tokens empty -> True
ALL ASSERTIONS PASSED
```

**Other issues found**
- None blocking. Pre-existing (out of scope, not introduced here): not every `Settings` field is
  mirrored in `.env.example` (e.g. `bash_timeout_s`, `max_output_lines`, `web_fetch_timeout_s`); the
  inference-section reorg neither adds nor removes from that gap. Worth a future tidy task, not a FAIL.

**VERDICT: PASS**
