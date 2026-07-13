---
id: 098
feature: env-bucket-secrets
status: done
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

- [x] `grep -rn "kitaru" src/decode/sandbox/` returns nothing; `grep -rn "get_secret" src/decode/` matches only `config/settings.py`.
- [x] `build_credential_map([rule with "Bearer {{ sandbox_git_token }}"])` returns the header with the unwrapped secret value, with no kitaru import and no network — proven by a unit test with a monkeypatched settings field.
- [x] An unknown `{{ nonexistent_field }}` template raises a clear error naming the field; an empty resolved value raises rather than injecting an empty header.
- [x] `github_token_rules()` behaviour is byte-identical (its existing tests are untouched and green).
- [x] Log output still carries names only — no resolved value appears in any log record (asserted).
- [x] No doc under `README.md` / `CREDENTIALS.md` / `docs/glossary.md` mentions `{{ secret-name.key }}` or a kitaru step for proxy rules; ADR-0011 §6 + ADR-0012 §10 carry the amendment pointers.
- [x] `make ci` green.

## Out of scope

- `github_token_rules()` / `SANDBOX_GIT_TOKEN` semantics, the mitmproxy container topology, the addon, and
  `tests/integration/test_credential_proxy.py`'s claims (unchanged — re-run, don't rewrite).
- The `CREDENTIALS.md` cohesive rewrite (102).

## Log

### [SWE] 2026-07-13 20:05 — Implementation

**Files modified**
- `src/decode/sandbox/proxy.py` — `{{ field_name }}` templates resolved from the hydrated `settings`; `_resolve_templates` + the lazy `from kitaru import get_secret` deleted; new `_settings_value()` (SecretStr unwrap, stringify, loud on unknown/empty).
- `src/decode/sandbox/proxy_addon.py` — comment reworded so `src/decode/sandbox/` is grep-clean of "kitaru".
- `README.md` — Credential Proxy "any other host" bullet: a `{{ settings_field }}` template, no secret-store step.
- `CREDENTIALS.md` — Part 2 "how much of this is Kitaru?" note (now: none), 2b (Settings field + rule), Part 3 (both mechanisms via the Environment Bucket), Part 4 cleanup.
- `docs/adr/0011-…md` §6 / `docs/adr/0012-…md` §10 — dated amendment pointers (append-only).
- `tests/unit/decode/sandbox/test_proxy.py` — template tests rewritten (SecretStr + plain field, unknown field, non-field attribute, None/empty value, merge, names-only logging); kitaru-fetch/memoisation tests deleted; subprocess seam assertion (no kitaru in `sys.modules`).
- `tests/unit/decode/runtime/test_sandbox_proxy.py` — flow-seam resolution from hydrated settings + loud failure before any container.
- `tests/integration/test_credential_proxy.py`, `tests/integration/test_sandbox_capstone.py` — mechanical: patched kitaru secret → monkeypatched Settings field (claims unchanged).

**Tests**
- Unit: 1493 passing, 0 failing.
- Integration: green, incl. `tests/integration/test_credential_proxy.py` (5 tests RUN against a real docker daemon — real mitmproxy topology).
- `make ci`: **1613 passed in 568.60s**.

**Acceptance criteria**
- [x] `grep -rn "kitaru" src/decode/sandbox/` → no matches (exit 1). `grep -rnw "get_secret" src/decode/` → only `config/settings.py:118,120` (the bare `get_secret` seam; the unqualified grep also matches `SecretStr.get_secret_value()`, which is a different API and pre-exists everywhere).
- [x] Bearer `{{ sandbox_git_token }}` → unwrapped value, no kitaru, no network — `test_proxy.py::test_build_credential_map_resolves_a_secretstr_field_into_the_host_header_value` + the subprocess seam test.
- [x] Unknown field / empty value raise naming the field — `test_build_credential_map_rejects_an_unknown_settings_field`, `…_rejects_an_unset_field`, `…_rejects_an_empty_field_value`.
- [x] `github_token_rules()` untouched; its tests green.
- [x] Names-only logging asserted (`test_build_credential_map_logs_names_never_values`).
- [x] Docs scrubbed; ADR-0011 §6 + ADR-0012 §10 carry dated amendments; glossary verified (already correct).
- [x] `make ci` green.

**Evidence**
```
$ grep -rn "kitaru" src/decode/sandbox/ ; echo "exit=$?"
exit=1

$ grep -rnw "get_secret" src/decode/
src/decode/config/settings.py:118:            from kitaru import get_secret  # lazy: a remote env is the only importer of kitaru here
src/decode/config/settings.py:120:            values = get_secret(bucket).values

$ SANDBOX_GIT_TOKEN=ghp_e2e_live_token uv run python -c "<build_credential_map on a templated rule>"
LOG [sandbox] proxy rule 'acme-api' resolved (hosts=['api.acme.test'], headers=['Authorization'])
MAP: {'api.acme.test': {'Authorization': 'Bearer ghp_e2e_live_token'}}
kitaru imported? False
LOUD: proxy rule template {{ acme_token }} names 'acme_token', which is not a Settings field — …

$ make ci
======================= 1613 passed in 568.60s (0:09:28) =======================
```

**Notes**
- `runtime/flow.py::_sandbox_proxy()` already carried the correct rationale ("Proxy rules read the already-hydrated `settings` (ADR-0015 §5)") — 097 fixed it; verified, no change needed.
- The AC grep forced the sandbox package to be grep-clean of the literal word "kitaru", so two *prose* mentions (a `proxy_addon.py` comment predating this task, and my own new docstring line) were reworded. No behaviour rides on it.
- `tests/integration/test_credential_proxy.py` kept every claim; only the fixture moved (patched secret → monkeypatched field), since the template form it drives changed.
- CREDENTIALS.md Part 3 also still documented the deleted `RUNTIME_SECRET_STORE_CONFIG` knob; since the same command block was being edited for the proxy-rule scrub, it now uses `DECODE_ENV` + `make sync-secrets`. The cohesive rewrite remains task 102.
