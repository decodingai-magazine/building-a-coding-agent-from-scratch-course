---
id: 098
feature: env-bucket-secrets
status: pending
---

# Credential Proxy rules resolve from Settings ({{ field_name }} templates, no kitaru)

Depends on: 097. Implements ADR-0015 §6.

## Scope

`DEFAULT_PROXY_RULES` header templates stop naming Kitaru secrets and start naming **Settings field names**;
`build_credential_map()` becomes a pure function of the already-hydrated `settings` object — no kitaru, no
network. After this task, `kitaru.get_secret` is called from exactly **one** place in the codebase: the
Environment-Bucket settings source.

**`src/decode/sandbox/proxy.py`**

- Replace `_TEMPLATE_RE`'s `{{ name.key }}` form with a single-identifier `{{ field_name }}` form (e.g.
  `{"Authorization": "Bearer {{ sandbox_git_token }}"}`); **delete** `_resolve_templates` and the
  `from kitaru import get_secret` lazy import.
- `build_credential_map(rules)` resolves each template host-side from `decode.config.settings.settings`:
  `getattr` by field name, unwrap `SecretStr` via `.get_secret_value()`, stringify plain fields. An unknown
  field name or an empty/`None` secret raises a clear error — **never** a silent skip (mirror of today's
  missing-secret contract). Still merges same-host rules, still logs rule/host/header **names** only, still
  returns `{}` for empty rules (passthrough proxy).
- Update the module docstring and the `DEFAULT_PROXY_RULES` comment example to the new form. Note the GitHub
  push+PR case remains `github_token_rules()` — unaffected, literal values from `settings.sandbox_git_token`;
  the Basic-base64 transform is exactly why it is code, not a template.
- `SandboxProxyRule` docstring: templates reference Settings fields, resolved from the hydrated config (which
  at a remote env arrived via the Environment Bucket).

**`src/decode/runtime/flow.py`**

- `_sandbox_proxy()` docstring: drop the "nests inside `_config_from_secret_store` so proxy rules read
  secret-hydrated config" rationale — rules now read the process-hydrated settings.

**Docs (scrub)**

- `README.md` Credential Proxy section: the "any other host" bullet now says "a `{{ settings_field }}` header
  template resolved from the hydrated `Settings`" — no `kitaru secrets set` step for proxy rules.
- `CREDENTIALS.md` Parts 2b/3: fix the `{{ github-token.value }}` instructions minimally so nothing documents
  the deleted form (the full tutorial rewrite is 102).
- `docs/glossary.md` **Proxy Rule** entry: already updated to the `{{ settings_field }}` form in the plan
  commit — verify only.
- `docs/adr/0011-sandboxing-and-credential-proxy.md` §6 and `docs/adr/0012-isolated-workspace.md` §10: append
  dated amendment pointers — proxy-rule templates no longer resolve via `kitaru.get_secret`; they name Settings
  fields (ADR-0015 §6). **Append-only.**

**Tests**

- `tests/unit/decode/sandbox/test_proxy.py`: rewrite the template tests — resolution from a monkeypatched
  settings field (incl. a `SecretStr`), unknown-field error, empty-value error, merge + names-only logging
  unchanged; delete the kitaru-fetch/memoisation tests. The suite gets simpler — no store stubbing.
- `tests/unit/decode/runtime/test_sandbox_proxy.py`: update the flow-level engagement tests to the new
  signature/behaviour.
- Add the seam assertion: `decode.sandbox.proxy` imports no kitaru (a `sys.modules` check after importing and
  calling `build_credential_map` on a templated rule).

## Acceptance Criteria

- [ ] `grep -rn "kitaru" src/decode/sandbox/` returns nothing; `grep -rn "get_secret" src/decode/` matches only `config/settings.py`.
- [ ] `build_credential_map([rule with "Bearer {{ sandbox_git_token }}"])` returns the header with the unwrapped secret value, with no kitaru import and no network — proven by a unit test with a monkeypatched settings field.
- [ ] An unknown `{{ nonexistent_field }}` template raises a clear error naming the field; an empty resolved value raises rather than injecting an empty header.
- [ ] `github_token_rules()` behaviour is byte-identical (its existing tests are untouched and green).
- [ ] Log output still carries names only — no resolved value appears in any log record (asserted).
- [ ] No doc under `README.md` / `CREDENTIALS.md` / `docs/glossary.md` mentions `{{ secret-name.key }}` or a kitaru step for proxy rules; ADR-0011 §6 + ADR-0012 §10 carry the amendment pointers.
- [ ] `make ci` green.

## Out of scope

- `github_token_rules()` / `SANDBOX_GIT_TOKEN` semantics, the mitmproxy container topology, the addon, and
  `tests/integration/test_credential_proxy.py`'s claims (unchanged — re-run, don't rewrite).
- The `CREDENTIALS.md` cohesive rewrite (102).

## Log
