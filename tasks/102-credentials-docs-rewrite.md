---
id: 102
feature: env-bucket-secrets
status: pending
---

# CREDENTIALS.md rewrite + README cohesion pass (the env bucket, end to end)

Depends on: 096–101. Implements ADR-0015's documentation surface. **Docs-only — no `src/` changes.**

## Scope

**`CREDENTIALS.md` — a real rewrite around the Environment Bucket.** The old Part 1 (the model-key knob) is
deleted code; Parts 2b/3 were built on the deleted `{{ github-token.value }}` template. New shape, keeping the
house style (A/B scenarios, "Working looks like", negatives, cleanup, automated backstop):

- Intro table contrasts the **two surviving mechanisms**: **Environment Bucket** (config hydration into
  `Settings`, gated by `DECODE_ENV`, both surfaces) vs **Credential Proxy** (header injection, headless + docker).
  Delete the retired-alias note block (`RUNTIME_CREDENTIALS_PROXY_ENABLED`) and every `RUNTIME_SECRET_*` mention,
  replaced by a **loud clean-break paragraph**: the old knobs are gone and are *silently ignored*; the migration is
  `make sync-secrets ENV=<env>` + `DECODE_ENV`. (This paragraph is the compensation for shipping the removal
  without a fail-fast guard — it is the only thing standing between a reader and a silent no-op.)
- **New Part 1 — the Environment Bucket walkthrough:** `make sync-secrets ENV=staging` (showing the key-name
  diff), then `DECODE_ENV=staging decode run …` with the provider key **absent** from the process env. Negatives:
  missing bucket / Kitaru daemon down → the one friendly `make sync-secrets` line, in **both** surfaces; the
  no-backfill property (a key present only in `.env` fails loudly at a remote env); process env wins over the bucket.
- **Part 2 (Credential Proxy) updated:** 2b uses a `{{ settings_field }}` rule resolved from the hydrated
  `Settings` (no `kitaru secrets set` step for proxy rules — at a remote env the field arrives via the bucket);
  2c (`SANDBOX_GIT_TOKEN`) unchanged in substance. **Keep the two hard-won warnings from the current doc:** every
  proxy prompt must force the `bash` tool (`web_fetch` is host-side and bypasses the proxy entirely), and
  `--repo` only clones into an *empty* Workspace (`rm -rf .decode/sandbox` first).
- **Part 3** composes bucket + proxy. **Part 5** points at the surviving test files.
- State Kitaru's role plainly: its **only** `get_secret` seam in decode is the Environment-Bucket settings source.

**`README.md`** — cohesion pass: the "Environments & secrets" section (seeded in 097) reads as one story
(`DECODE_ENV` → bucket → `make sync-secrets` → friendly failure); the Credential Proxy section is consistent with
098; no stale knob names anywhere.

**Cross-checks** (no changes expected — verify): `.env.example`'s final state matches 096–101; the glossary
entries are consistent with the prose; ADR-0015 and its amendment pointers are referenced where the mechanisms
are explained.

## Acceptance Criteria

- [ ] `grep -rn "RUNTIME_SECRET\|RUNTIME_CREDENTIALS_PROXY\|secret-name.key\|{{ github-token\|decode-llm-creds" README.md CREDENTIALS.md .env.example docs/glossary.md` returns nothing (`docs/adr/` history exempt).
- [ ] `CREDENTIALS.md` Part 1 walks the Environment Bucket end to end with copy-pasteable commands, at least two negatives (missing bucket; the `.env` no-backfill property), and the clean-break migration paragraph.
- [ ] The Credential Proxy tutorial's rule example uses the `{{ settings_field }}` form and requires no `kitaru secrets` command.
- [ ] The `bash`-not-`web_fetch` warning and the `rm -rf .decode/sandbox` step both survive the rewrite (they are the two footguns that already cost a debugging session each).
- [ ] Every referenced command / file / test path in both docs exists in the tree.
- [ ] `make ci` green.

## Out of scope

- The `manual-e2e-qa` skill payload; `MODAL_MODELS.md`; any code or test change (if the rewrite exposes a code
  gap, file a new task).

## Log
