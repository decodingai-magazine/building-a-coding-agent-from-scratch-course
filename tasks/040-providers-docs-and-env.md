---
id: 040-providers-docs-and-env
feature: multi-provider-gateway
status: pending
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

- [ ] `.env.example` presents the three providers behind `LLM_PROVIDER` with free-tier notes; the new
      vars carry guidance; the **account-token** vars (`MODAL_TOKEN_*`) and the new **endpoint
      proxy-token** vars (`MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET`) are unmistakably
      distinguished (a reader can tell which is the CLI auth vs the endpoint request headers); the
      proxy tokens are noted as optional (`--unauthenticated`).
- [ ] `README.md` has a lean "LLM providers" section naming Modal / OpenRouter / Gemini with their
      free-tier story, the opt-in (`LLM_PROVIDER=<name>` + secret), and that the default `gemini` keeps
      existing `.env` files working; the Milestone-1 status note no longer calls OpenRouter/Modal
      "later".
- [ ] The tool-calling + streaming caveat is documented for both OpenRouter and Modal, including the
      "if you swap models, pick a tool-capable one" warning, and names the shipped `OPENROUTER_MODEL`
      and `MODAL_ENDPOINT_MODEL` defaults as known-good.
- [ ] README gives a one-line Modal opt-in path and **links** to `MODAL_MODELS.md` for model selection +
      endpoint setup + wiring; it does **not** duplicate the model catalog or the full CLI walkthrough.
- [ ] `MODAL_MODELS.md` §6 reflects the shipped wiring (`LLM_PROVIDER=modal`, the four final var names,
      the Provider Seam `base_url`/`/v1` path, and the implemented §6.3 auth nuance incl. the
      `--unauthenticated` / `api_key="EMPTY"` case) rather than "planned/sketched"; §1-§5 are unchanged.
- [ ] Canonical glossary terms (**LLM Provider**, **Provider Seam**) used throughout; no contradiction
      with ADR-0005.
- [ ] `make ci` green (docs + `.env.example` only; no test regressions).

## Out of scope
- Code changes (settings / factory / cli) — tasks 037-039.
- An automated test asserting `.env.example` mirrors `settings` (none exists today; not introduced here).
- A live end-to-end run against a real OpenRouter/Modal account (manual e2e, not CI).
- Re-benchmarking or editing the MODAL_MODELS.md catalog (§1-§5) — only the §6 wiring wording is touched.

## Log
