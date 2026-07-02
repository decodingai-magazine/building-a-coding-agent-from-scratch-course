---
id: 076-sandbox-docs-and-e2e-rows
feature: sandboxing
status: pending
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

- [ ] AGENTS.md gains the four Testing-E2E rows (docker bash, modal bash, decode-run-in-sandbox, credential
  proxy), each with a concrete "type this / working looks like", and the `docker ps` / `docker exec -it`
  manual-QA peek for the docker row.
- [ ] The two AGENTS.md sandbox invariants are reconciled with ADR-0011 (3 executors behind one seam; the
  proxy realizes the secrets invariant) and reference ADR-0011.
- [ ] README has a Sandboxing section covering the three modes, the default, the guard, the per-mode bash
  semantics, and the credential-proxy operator setup; it states the isolation honesty + links the ADR
  table.
- [ ] MODAL notes distinguish account tokens (sandbox) from endpoint/proxy tokens (LLM provider).
- [ ] Any doc-drift guard (e.g. the `.env.example` drift test) stays green; `make ci` green, 0 warnings.
- [ ] A grep confirms docs use the canonical glossary terms (Sandbox, Sandbox Mode, Worker, Credential
  Proxy, Proxy Rule) and introduce no synonym.

## Out of scope

- Code changes (all shipped in 071-075).
- The capstone test (077).

## Log
