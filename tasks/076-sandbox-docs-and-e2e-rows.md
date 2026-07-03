---
id: 076-sandbox-docs-and-e2e-rows
feature: sandboxing
status: done
---

# Docs: AGENTS.md Testing-E2E rows, sandbox invariants, README, MODAL notes

Tags: `docs`
Depends on: #075
Blocks: #077

This task documents the shipped sandbox feature (ADR-0011) across the operator-facing docs, mirroring
task 055 (LSP docs) / 048 (compaction docs) / 040 (provider docs). No code — docs + the manual-QA probes.

## Scope

- **AGENTS.md "Testing E2E" table** — add rows exercising each surface like a user:
  - **bash in docker** (`SANDBOX_MODE=docker`, `read a file then run \`export X=1 && cd /tmp\`, then a
    second bash \`echo $X && pwd\``) → the persistent shell shows `1` and `/tmp` (state persisted); the
    repo is at `/workspace`. **Manual-QA peek:** while a session is live, `docker ps` shows the container;
    `docker exec -it <id> bash` peeks inside it.
  - **bash in modal** (`SANDBOX_MODE=modal`, `run \`ls\``) → an **empty** `/workspace` (the local tree is
    absent); `git clone …` then build works (fs persists); the tool description told the model it's
    remote scratch.
  - **decode run in a sandbox** (`SANDBOX_MODE=docker decode run "…"`) → the headless bypass run executes
    bash in the container; a `decode replay` **re-executes** sandbox bash (side effects re-run, not
    cached).
  - **credential proxy** (headless + docker, `SANDBOX_CREDENTIAL_PROXY_ENABLED=true` + a rule + a Kitaru
    secret) → an authenticated tool call from the worker succeeds though the worker holds no token; the
    secret is only in the proxy container.
  - Each row names the friendly-guard failure (daemon down / modal unauthenticated → one stderr line,
    non-zero exit, no traceback).
- **AGENTS.md sandbox invariants** — reconcile the two existing bullets with the shipped design:
  "Sandbox is the one real abstraction" (now 3 executors behind `run`; don't leak docker/modal types
  upward) and "Secrets never reach the model or the sandbox payload" (now realized by the Credential
  Proxy — worker holds handles/nothing, not raw keys). Reference ADR-0011.
- **README** — a Sandboxing section: the three modes, the default (`none`, zero change), the startup
  guard, the persistent-shell docker semantics + the empty-scratch modal semantics, and the
  headless+docker credential-proxy operator setup (`kitaru secrets set …`, a `SandboxProxyRule`,
  `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`). State the isolation honesty (Docker = accidental-misbehavior
  boundary; Modal = untrusted-code rung) and point at ADR-0011's isolation table.
- **MODAL notes** (`MODAL_MODELS.md` or a sibling note): clarify the account tokens (`modal token set`,
  used by the sandbox) vs the endpoint/proxy tokens (the LLM provider) — the sandbox uses the **account**
  tokens; cross-link `.env.example`.

## Acceptance criteria

- [x] AGENTS.md gains the four Testing-E2E rows (docker bash, modal bash, decode-run-in-sandbox, credential
  proxy), each with a concrete "type this / working looks like", and the `docker ps` / `docker exec -it`
  manual-QA peek for the docker row.
  Round 1 FAIL → Round 2 PASS (Tester 2026-07-03): the round-1 unescaped `|` at AGENTS.md:205 is now
  escaped (`docker exec <worker-id> env \| grep -i token`). Re-rendered with cmark-gfm: all four sandbox
  rows parse as genuine 3-column (4 delimiter pipes each), the Credential Proxy row's third cell renders
  in full through the **Guard:** clause, no dangling backtick, nothing leaked after `</table>`. Zero
  unescaped pipes remain in any code span of any changed-doc table row.
- [x] The two AGENTS.md sandbox invariants are reconciled with ADR-0011 (3 executors behind one seam; the
  proxy realizes the secrets invariant) and reference ADR-0011.
- [x] README has a Sandboxing section covering the three modes, the default, the guard, the per-mode bash
  semantics, and the credential-proxy operator setup; it states the isolation honesty + links the ADR
  table.
- [x] MODAL notes distinguish account tokens (sandbox) from endpoint/proxy tokens (LLM provider).
- [x] Any doc-drift guard (e.g. the `.env.example` drift test) stays green; `make ci` green, 0 warnings.
- [x] A grep confirms docs use the canonical glossary terms (Sandbox, Sandbox Mode, Worker, Credential
  Proxy, Proxy Rule) and introduce no synonym.

## Out of scope

- Code changes (all shipped in 071-075).
- The capstone test (077).

## Log

### [SWE] 2026-07-03 09:50 — Implementation

**Files modified (docs only — NO src/ or tests/ changed)**
- `AGENTS.md` — added a **Sandboxing (ADR-0011)** sub-table to "Testing E2E" with four rows (docker
  bash, modal bash, decode-run-in-a-Sandbox + replay re-executes, Credential Proxy), the `docker ps` /
  `docker exec -it` manual-QA peek on the docker row, each row naming its friendly guard; reconciled
  the two sandbox invariant bullets (3 executors behind the `run` seam; Credential Proxy realizes the
  secrets invariant) referencing ADR-0011; fixed the two stale `(Docker/Firecracker)` parentheticals
  (Project Structure tree + Tech Stack row) to the shipped `none`/`docker`/`modal` reality.
- `README.md` — new `## Sandboxing` section (three modes + default `none` = zero change; startup guard
  with the verbatim daemon/creds lines; docker persistent-shell vs modal empty-scratch; isolation
  honesty + a deep link to ADR-0011's isolation table; the headless+docker Credential Proxy operator
  setup: a `SandboxProxyRule`, a `kitaru secrets set` secret, `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`).
  Dropped "sandboxing" from the stale "later milestones" list and linked the new section.
- `.env.example` — polished the `# --- Sandboxing ---` block's Credential-Proxy comment (the flag alone
  injects nothing: also add a Proxy Rule + a Kitaru secret; README pointer). All five vars intact
  (drift guard green).
- `MODAL_MODELS.md` — §5.1 blockquote distinguishing Modal **account** tokens (`modal token set`;
  authenticate the CLI *and* the Modal Sandbox, `SANDBOX_MODE=modal`) from the **endpoint/proxy** tokens
  (how decode calls the served LLM); tightened the §6 note to name the Sandbox + ADR-0011.

**Verification against SHIPPED code (not just the task file)**
- Guard lines quoted **verbatim** from `cli.py` (`_SANDBOX_DOCKER_UNREACHABLE_MESSAGE`,
  `_SANDBOX_MODAL_NO_CREDENTIALS_MESSAGE`).
- Settings names/defaults confirmed in `config/settings.py` (`sandbox_mode=none`,
  `sandbox_image=python:3.12-slim`, `sandbox_timeout_s=600`, `sandbox_credential_proxy_enabled=False`,
  `sandbox_proxy_image=mitmproxy/mitmproxy`).
- **Correction vs the hand-off's suggested peek:** `docker_executor.py` adds **no** container label
  (`_docker_run_args` = `run -d --rm -v <cwd>:/workspace -w /workspace <image> sleep infinity`). Docs
  therefore use plain `docker ps` (+ an `ancestor=python:3.12-slim` filter hint), **not**
  `docker ps --filter label=decode.sandbox` (which would match nothing).
- Per-mode `bash` semantics matched to `tools/bash.py` (`_DOCKER_DESCRIPTION_SUFFIX` /
  `_MODAL_DESCRIPTION_SUFFIX`) and the executors; replay re-exec matched to `runtime/flow.py`
  (`{"cache": False}` on the `bash` checkpoint when `sandbox_mode != "none"`); proxy claims matched to
  `sandbox/proxy.py` (`SandboxProxyRule`, empty `DEFAULT_PROXY_RULES`, `{{ name.key }}` template,
  `DockerCredentialProxy`) and `runtime/flow.py::_sandbox_proxy` (docker + enabled only).

**Tests**
- Unit: full suite green via `make pre-commit` (1194 passing, 0 failing).
- `make ci` green (1234 passing incl. integration + `uv lock --check` + format/lint), exit 0, 0 warnings.
- `.env.example` drift guard (`test_env_example_lists_every_sandbox_var` + siblings) green (12 passed).

**Acceptance criteria** — all six verified (none `[HUMAN]`); see the checked boxes above.

**Evidence**
```
$ uv run pytest tests/unit/decode/config/test_settings.py -q
51 passed in 1.64s

$ make pre-commit
1194 passed in 84.55s

$ make ci
======================= 1234 passed in 180.88s (0:03:00) =======================   # exit 0

$ uv run pytest tests/unit/decode/config/test_settings.py -k "env_example or sandbox" -q
12 passed, 39 deselected in 0.50s
```

**Notes**
- Docs-only; no runtime behavior changed. CLAUDE.md's `@AGENTS.md` import link left intact.
- Markdown-integrity checks: all four new AGENTS.md rows are 3-column; broken nested/escaped backticks
  removed (the two verbatim modal-guard lines, which contain literal `` ` `` around `modal token set`,
  use double-backtick code spans); all new internal anchors resolve (README `#headless-runtime-decode-run`,
  `#credentials-proxy-...`, ADR `#isolation-backends-compared--why-docker--modal`).
- Pre-existing drift left untouched (out of scope): README's model table lists `MODAL_ENDPOINT_MODEL`
  default as `openai/gpt-oss-120b` while `settings.py` defaults to `Qwen/Qwen3.6-35B-A3B-FP8`. Not a
  sandbox doc — flagging for a possible follow-up task, not fixed here.

### [SWE] 2026-07-03 10:20 — Fixes (round 2, Tester QA)

**Blocker fixed — GFM table cell split by an unescaped `|`.** In the AGENTS.md Credential Proxy
Testing-E2E row, the code span `` `docker exec <worker-id> env | grep -i token` `` carried a raw pipe.
cmark-gfm reads that `|` as a column delimiter, so the row's 3rd cell truncated at "…env " and GFM
silently dropped the rest (the token-free proof, the credential-map/empty-`DEFAULT_PROXY_RULES` note,
the headless+docker/REPL-kitaru-free note, the cooperative-egress note, AND the whole `Guard:` clause,
plus a dangling backtick). Escaped it → `` `docker exec <worker-id> env \| grep -i token` ``.

**Cleanup — leaked internal `ponytail:` code-marker removed from user-facing prose** (content kept):
- `AGENTS.md` row 205 — "…an exfiltration barrier (`ponytail:`)." → "…an exfiltration barrier."
- `README.md` — "`ponytail:` egress is **cooperative**…" → "Egress is **cooperative**…".

**Files modified this round:** `AGENTS.md`, `README.md` (still docs-only; no src/ or tests/).

**Re-verification**
- All four AGENTS.md sandbox rows now have **exactly 4 unescaped pipes** (well-formed 3-column rows);
  row 205's 3rd cell renders in full through the `Guard:` clause, no dangling backtick.
- No `ponytail:` token remains in any of the four docs (grep clean).
- `make format-check` + `make lint-check` clean; `.env.example` drift guard + sandbox settings green
  (12 passed); `make ci` green (**1234 passed in 176.84s**, exit 0).
- `git diff --stat` = the 4 docs + this task file only.

**Evidence**
```
$ make format-check && make lint-check
162 files already formatted
All checks passed!

$ uv run pytest tests/unit/decode/config/test_settings.py -k "env_example or sandbox" -q
12 passed, 39 deselected in 0.37s

$ make ci
======================= 1234 passed in 176.84s (0:02:56) =======================   # exit 0
```

### [Tester] 2026-07-03 12:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make ci` exit 0)
- Unit + integration tests: 1234 passed / 0 failed (`make ci`, 165s)
- `.env.example` drift guard: 12 passed / 0 failed
- Warnings: 0 (`filterwarnings=["error"]`; `make ci` green)
- Scope integrity: `git diff --stat` shows ONLY AGENTS.md, README.md, .env.example, MODAL_MODELS.md,
  tasks/076 — no `src/` or `tests/` file changed. PASS.

**E2E adversarial pass (documentation-accuracy audit)**
- Verbatim guard strings: imported the actual `cli.py` constants and confirmed both
  `_SANDBOX_DOCKER_UNREACHABLE_MESSAGE` and `_SANDBOX_MODAL_NO_CREDENTIALS_MESSAGE` appear
  character-for-character (em-dash + ellipsis included) in BOTH AGENTS.md and README.md. PASS.
- Settings/defaults: `sandbox_mode=none`, `sandbox_image=python:3.12-slim`, `sandbox_timeout_s=600.0`,
  `sandbox_credential_proxy_enabled=False`, `sandbox_proxy_image=mitmproxy/mitmproxy` all match
  `config/settings.py`. PASS.
- No-label correction (live docker peek): spun up the EXACT keeper command from
  `docker_executor._docker_run_args` (`run -d --rm -v <cwd>:/workspace -w /workspace python:3.12-slim
  sleep infinity`). `docker ps` → `IMAGE=python:3.12-slim COMMAND="sleep infinity"`; `docker inspect`
  → `Labels={}` (no custom label — SWE correction CONFIRMED, plain `docker ps` is right);
  `ancestor=python:3.12-slim` filter finds it; `docker exec <id> bash -c 'pwd && ls'` → `/workspace`
  + the repo (AGENTS.md, README.md, …). Container removed (confirmed gone). PASS.
- Code claims mapped to shipped source: bash.py `_DOCKER_/_MODAL_DESCRIPTION_SUFFIX` match the per-mode
  semantics; `flow.py:303-304` sets `{"cache": False}` on the bash checkpoint when `sandbox_mode !=
  "none"` (replay re-exec claim); `flow.py:240` gates `_sandbox_proxy` on docker AND enabled; proxy.py
  `SandboxProxyRule`/empty `DEFAULT_PROXY_RULES`/`{{ name.key }}`/`DockerCredentialProxy` all match; the
  README `github-auth` example equals the commented example in proxy.py:71-79; modal `cwd` is ignored
  (modal_executor.py:118-119); MODAL account-vs-endpoint token split matches `cli.py`
  `_modal_credentials_present` vs `_provider_config_error`. PASS.
- Canonical terms: no synonym (container mode / shell jail / header injector / remote shell / cred
  proxy) in any changed doc; glossary defines Sandbox / Sandbox Mode / Worker / Credential Proxy /
  Proxy Rule and the docs use them. PASS.
- Firecracker: both stale `(Docker/Firecracker)` parentheticals fixed; remaining mentions are the
  non-goal framing consistent with ADR-0011. PASS.
- ADR-0011 `§N` cross-refs (§1/§2/§4/§5/§6) are topically accurate against the Decision numbered points.
- **Markdown integrity (FAIL):** rendered every changed table with GitHub's own cmark-gfm. AGENTS.md:205
  (Credential Proxy row) has an unescaped `|` in the `env | grep -i token` code span → the third cell
  renders truncated at "…`docker exec <worker-id> env " and the entire second half of the cell (incl.
  the **Guard:** clause) is silently DROPPED on GitHub. This is the single blocking defect. All other
  tables/rows well-formed; code fences balanced in all three docs.

**Acceptance criteria**
- [ ] FAIL — AGENTS.md four E2E rows — the Credential Proxy row (line 205) renders truncated on GFM
      (unescaped pipe drops the second half of the third cell, incl. the Guard clause). Fix:
      `docker exec <worker-id> env \| grep -i token`. Other three rows OK.
- [x] PASS — sandbox invariants reconciled with ADR-0011 — 3 executors behind `tools/exec.py::
      CommandExecutor` (verified: `ExecResult`/`CommandExecutor`/`LocalExecutor` at exec.py:36/60/73);
      Credential Proxy realizes the secrets invariant; both reference ADR-0011.
- [x] PASS — README Sandboxing section — three modes + default `none` + verbatim guard lines + per-mode
      bash semantics + credential-proxy operator setup + isolation honesty + ADR-0011 isolation-table
      link (`#isolation-backends-compared--why-docker--modal` resolves). Anchors all resolve.
- [x] PASS — MODAL notes distinguish account tokens (`modal token set`, authenticate CLI + Sandbox) from
      endpoint/proxy tokens; matches `cli.py:161-173` vs `104-135`.
- [x] PASS — drift guard + `make ci` green, 0 warnings (12 passed; 1234 passed, exit 0).
- [x] PASS — canonical glossary terms used, no synonym introduced (grep clean; glossary lines 17,53-56).

**Evidence**
```
$ git diff --stat            # only docs + task file
 .env.example | 8 +-  AGENTS.md | 18 ++-  MODAL_MODELS.md | 15 +-  README.md | 72 ++-  tasks/076… | 83 +-

$ make ci
======================= 1234 passed in 165.49s (0:02:45) =======================   # exit 0

$ uv run pytest tests/unit/decode/config/test_settings.py -k "env_example or sandbox" -q
12 passed, 39 deselected

# cmark-gfm render of AGENTS.md Credential Proxy row, cell 3 (TRUNCATED):
CELL: …injects the header after the request leaves the worker. Prove the worker is token-free: `docker exec
# after escaping the pipe (\|), the same cell ends correctly:
… (ponytail:). Guard: same docker daemon guard; a no-op unless sandbox_mode=docker and SANDBOX_CREDENTIAL_PROXY_ENABLED=true.

# live docker peek (keeper container, cleaned up after):
docker ps  -> IMAGE=python:3.12-slim  COMMAND="sleep infinity"
inspect    -> Labels={}                # no custom label (correction confirmed)
exec pwd   -> /workspace + repo files
```

**Other issues found (not blocking on their own)**
- `ponytail:` internal marker leaked into user-facing prose: README.md:379 ("`ponytail:` egress is
  cooperative …") and AGENTS.md:205 ("… (`ponytail:`)"). Factually accurate (maps to proxy.py:25) but
  meaningless jargon to a README reader. Recommend dropping the `ponytail:` token in the README (the
  sentence reads clean without it) while fixing line 205.
- Pre-existing, OUT OF SCOPE for 076 (do NOT block; follow-up): README `MODAL_ENDPOINT_MODEL` default
  drift at BOTH line 89 (table) and line 98 (prose) — `openai/gpt-oss-120b` vs settings.py
  `Qwen/Qwen3.6-35B-A3B-FP8`; the 076 diff does not touch these lines. Also glossary.md:53 names the
  proxy class `DockerProxy` but the code class is `DockerCredentialProxy` (proxy.py:153) — glossary not
  in 076 scope. Both worth a docs-drift follow-up task.

**VERDICT: FAIL** — one blocking markdown-integrity defect (AGENTS.md:205 unescaped pipe truncates the
Credential Proxy row on GitHub). Fix is a one-character escape; re-review will re-render the table and
re-verify AC #1.

### [Tester] 2026-07-03 13:05 — QA re-review (round 2)

Re-verified the SWE's fix to the round-1 blocker. Docs-only, still uncommitted.

**Fix confirmed**
- AGENTS.md:205 — the raw `|` in the Credential Proxy code span is now escaped (`env \| grep -i token`).
  Re-rendered the sandbox sub-table with GitHub's own cmark-gfm: the third cell renders IN FULL, ending
  "…a no-op unless sandbox_mode=docker and SANDBOX_CREDENTIAL_PROXY_ENABLED=true." (the complete
  **Guard:** clause). No dangling backtick; no content leaked after `</table>`.
- All four sandbox rows now parse as GENUINE 3-column — exactly 4 delimiter pipes each (the `\|` is
  counted as escaped, not a delimiter), and a scan of every backtick code span in every table row across
  AGENTS.md / README.md / MODAL_MODELS.md / .env.example found **0** remaining unescaped pipes.
- `ponytail:` removed from both user-facing spots (AGENTS.md:205, README.md:379); cooperative-egress
  content kept — README:379 now reads "Egress is **cooperative** (…), so this is not an exfiltration
  barrier; an internal-only default-deny network is the upgrade path."

**Regression / scope re-check**
- `git diff --stat`: only .env.example, AGENTS.md, MODAL_MODELS.md, README.md + this task file — no
  `src/`/`tests/`. PASS.
- `make ci`: 1234 passed, exit 0, 0 warnings (181s). PASS.
- Out-of-scope drifts remain untouched (correctly deferred, not silently changed): README
  `MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b` at lines 89 + 98 (settings.py defaults `Qwen/Qwen3.6-…`);
  glossary.md:53 still names the class `DockerProxy` vs code `DockerCredentialProxy` — glossary.md not in
  the diff. Both still worth a separate docs-drift follow-up.
- All round-1 PASS criteria spot-checked and still hold (verbatim guard strings, settings/defaults,
  no-label docker peek, per-mode bash semantics, `{"cache": False}` replay claim, proxy claims, canonical
  terms, Firecracker framing, resolved anchors).

**Evidence**
```
$ awk 'NR==205' AGENTS.md | grep -o 'env \\| grep'      -> env \| grep   (escaped)
$ cmark-gfm render, Credential Proxy row cell 3 tail:
  "…Cooperative egress, not an exfiltration barrier. Guard: same docker daemon guard; a no-op unless
   sandbox_mode=docker and SANDBOX_CREDENTIAL_PROXY_ENABLED=true."   (ends with Guard clause: True)
$ unescaped-'|'-in-code-span hits across all changed-doc table rows: 0
$ four sandbox rows: 4 delimiter pipes each -> OK 3-col
$ grep -n ponytail AGENTS.md README.md    -> (none)
$ make ci    -> 1234 passed in 181.75s (0:03:01)   # exit 0
```

**VERDICT: PASS** — the blocking markdown defect is fixed and verified on render, all six acceptance
criteria hold, `make ci` green with 0 warnings, docs-only scope intact, and the two out-of-scope drifts
remain correctly deferred. Ready for PA acceptance review.
