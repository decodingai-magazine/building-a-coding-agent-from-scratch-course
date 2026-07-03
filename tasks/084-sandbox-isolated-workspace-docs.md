---
id: 084-sandbox-isolated-workspace-docs
feature: isolated-workspace
status: pending
---

# Docs — AGENTS.md invariants + Sandboxing e2e rows, README, .env.example, MODAL note

Tags: `sandbox`, `docs`
Depends on: #083
Blocks: —

Reconcile every prose surface to the isolated-workspace reality (ADR-0012), including the built git
hand-back. Docs only — the suite stays green. Mirrors the 076 docs task.

## Scope

- **`AGENTS.md`:**
  - Rewrite the **Sandbox invariant**: a sandbox mode is a fully isolated workspace — ONE
    `SandboxExecutor` over two thin backend adapters (docker/modal), fresh-exec both; `/workspace` ≡
    host `.decode/sandbox` ≡ a git clone of `--repo` (or empty); **file tools operate on the sandbox
    filesystem through the backend seam** (docker: bind-mount pathlib; modal: SandboxFilesystem +
    remote find/grep), not on a mirror. Add the **Harness-home invariant**: harness artifacts
    (`.decode/sessions`, `MEMORY.md`, logs, `.decode/skills`, permission file) anchor to the launch
    cwd; only the agent's tool scope (`deps.cwd`) moves into the workspace. Add the **Hand-back
    invariant**: the harness ships the Workspace's results as a `decode/<session-id>` branch pushed
    back to the repo — **host-side only, no credential ever enters the sandbox**; the local branch
    always exists even if the push fails.
  - Rewrite the **Testing-E2E "Sandboxing" rows**: retire persistent-shell / empty-scratch language;
    document the unified fresh-exec Workspace, `--repo`/`--local`, file tools in the Workspace,
    docker's live mount vs modal's bootstrap-upload + export, the revival-restore note, LSP
    (none+docker on; modal best-effort-off), gated `web_fetch`, the auto-ship on exit, and a
    **`/ship`** row ("Type this: `/ship` — Working looks like: prints `decode/<id>` + the push
    outcome; `none` mode → 'no sandbox workspace'"). Keep the Credential-Proxy row.
- **`README.md`:** rewrite `## Sandboxing` to the isolated-Workspace model + a `--repo` quickstart
  ending with the hand-back — "on exit (or `/ship`) decode pushes a `decode/<id>` branch back to your
  repo with your own git creds" (branch naming, push-to-remote vs local, never-lose-results).
- **`.env.example`:** rewrite the sandbox block; document `SANDBOX_REPO`; note the hand-back needs no
  new var (uses `--repo` + ambient git creds); retire stale scratch/persistent-shell/sync comments.
- **MODAL note:** account- vs endpoint-token distinction retained; note the Workspace is bootstrap-
  uploaded + exported (not `add_local_dir`-seeded, not mtime-synced), file ops use `SandboxFilesystem`,
  and the hand-back push is host-side (never in the sandbox).

## Acceptance criteria

- [ ] The three AGENTS.md invariants (Sandbox + Harness-home + Hand-back) match shipped behavior; no
  persistent-shell / empty-scratch / mtime-sync language remains.
- [ ] The AGENTS.md Testing-E2E sandbox rows describe the unified Workspace, `--repo`, file-tools-in-
  Workspace, docker-mount vs modal-bootstrap/export, LSP posture, gated `web_fetch`, auto-ship, and
  `/ship` — each accurate against shipped code (branch naming, host-side push, layered durability,
  `none`-mode friendly line).
- [ ] README `## Sandboxing` + `--repo` quickstart + the hand-back section accurate and
  self-consistent.
- [ ] `.env.example` documents `SANDBOX_REPO`, no stale comments; every `SANDBOX_*` var matches
  `config/settings.py`.
- [ ] No `src/` change; `make ci` green, 0 warnings.

## Out of scope

- Glossary + ADR-0012 (grooming commit). Capstone (085). Auto-PR docs (deferred to M14).

## Log
