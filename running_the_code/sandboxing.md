# Sandboxing — isolated Workspaces, `--repo` & the git hand-back

By default (`SANDBOX_MODE=none`) `bash` runs as a host subprocess and the file tools edit your working directory directly. Set a **Sandbox Mode** and the agent's *whole* tool scope — file tools **and** `bash` — moves into a fully **isolated Workspace**, while decode's own artifacts (sessions, memory, logs, permission file) stay in your launch directory ([ADR-0012](../docs/adr/0012-isolated-workspace.md)):

| `SANDBOX_MODE` | Where the tools run | The Workspace |
|---|---|---|
| `none` (default) | host subprocess + direct file tools | none — zero change, no Docker/Modal needed |
| `docker` | one session-persistent **local** container | `/workspace` is a **live bind mount** of the host `.decode/sandbox/` |
| `modal` | one session-persistent **remote** [`modal.Sandbox`](https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng) | nothing runs on your machine; `/workspace` is bootstrap-uploaded at launch and exported back on exit / `/ship` |

Both modes are one unified executor with **fresh-exec** semantics: the filesystem persists across calls, but `cd`/`export` don't (chain them: `cd /workspace/app && …`). The sandbox starts eagerly at launch (a `sandbox:<mode>` banner segment), and `bash` stays gated exactly as before — the sandbox is defense-in-depth *beneath* the approval prompt.

## Work on any repo, and get a branch back

```bash
SANDBOX_MODE=docker decode --repo git@github.com:you/project.git
#   … the agent reads, edits, and runs bash entirely inside /workspace …
/ship          # or just quit — decode pushes a `decode/<session-id>` branch back to the repo
```

- **`--repo <url-or-path>`** (or `SANDBOX_REPO`; add `--local` for a fast local clone) clones at committed `HEAD` using your ambient git credentials. A bad repo degrades to an empty Workspace with one friendly line; `--repo` without a sandbox mode is a friendly config error. Works headless too: `SANDBOX_MODE=docker decode run --repo <url> "<task>"`. **It clones only into an *empty* Workspace** — a populated `.decode/sandbox` is reused, never re-cloned (that would discard in-progress work), so `--repo` against a leftover Workspace is ignored and there is no `origin` to push to. `rm -rf .decode/sandbox` to force a fresh clone.
- **Hand-back on exit or `/ship`** — decode commits any uncommitted model work (model commits are preserved, never rewritten), points a `decode/<session-id>` branch at the result, and pushes it. Every git command runs **host-side**, with your ambient git credentials — **hand-back puts no credential in the sandbox** (that holds whether or not you set [`SANDBOX_GIT_TOKEN`](#the-sandbox-git-token-sandbox_git_token)). A failed push still leaves the local branch in `.decode/sandbox` and names it; an unchanged Workspace is skipped.
- **Startup guards** — a selected backend that isn't available fails with one friendly line (Docker daemon down, or missing `modal token set` credentials), in the REPL and the headless pre-flight alike.
- **Isolation honesty** — docker is a boundary for *accidental* misbehavior (shared kernel on Linux; Docker Desktop's VM adds one on macOS); **modal** is the rung for genuinely untrusted code (nothing executes on your machine). gVisor/Kata are zero-code daemon-config upgrades; see [ADR-0011's isolation table](../docs/adr/0011-sandboxing-and-credential-proxy.md#isolation-backends-compared--why-docker--modal).

Tunables (all optional, documented in [`.env.example`](../.env.example)): `SANDBOX_IMAGE` (default `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` — python + uv preinstalled; each backend adds **git + the `gh` CLI**, so a model can commit, push, *and* open the PR — docker installs them per session (~20s), modal bakes them into a cached image layer), `SANDBOX_TIMEOUT_S` (modal lifetime), `SANDBOX_GIT_USER_NAME`/`_EMAIL` (the in-Workspace commit identity), `SANDBOX_GIT_TOKEN` (below).

## The sandbox git token (`SANDBOX_GIT_TOKEN`)

Hand-back (above) needs **no** credential in the sandbox — it pushes host-side. `SANDBOX_GIT_TOKEN` is the opt-in for the strictly larger ask: letting the **model itself** run `git push` / `gh pr create` from inside the Workspace. Set it and the token is direct-injected into the Worker's env as `GITHUB_TOKEN` in both backends, plus a git credential-helper so HTTPS pushes and `gh` authenticate ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md)) — exact mechanics, live proofs, and every negative case: [credentials.md Part 2](credentials.md#part-2--the-sandbox-git-token-sandbox_git_token).

> **A sandboxed process can read `$GITHUB_TOKEN`** — a prompt-injected agent too. Use a **fine-grained, repo-scoped, revocable** PAT, and revoke it when you're done. Leave the variable **unset** and the sandbox holds no credential at all — hand-back still ships your branch.

decode used to hide this token behind a mitmproxy sidecar (the **Credential Proxy**). It is **deleted** ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md)): it only ever worked in one of the three sandbox modes, egress was cooperative anyway (`curl --noproxy '*'` walked around it), and the machinery cost more than it protected. One mechanism now, both backends — a security story that is *true as written*.

## Go further

- The end-to-end credential walkthrough — token set, token unset, and every negative case: [credentials.md](credentials.md).
- Run a sandboxed agent headless (`decode run --repo …`): [runtime.md](runtime.md).
- Move the *whole agent* (not just its tools) to the cloud: [infra.md](infra.md).
