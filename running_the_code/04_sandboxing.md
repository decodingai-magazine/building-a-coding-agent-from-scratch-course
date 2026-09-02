# Sandboxing — isolated Workspaces, `--repo` & the git hand-back

By default (`SANDBOX_MODE=none`) `bash` runs as a host subprocess and the file tools edit your working directory directly. Set a **Sandbox Mode** and the agent's *whole* tool scope — file tools **and** `bash` — moves into a fully **isolated Workspace**, while decode's own artifacts (sessions, memory, logs, permission file) stay in your launch directory ([ADR-0012](../docs/adr/0012-isolated-workspace.md)):

| `SANDBOX_MODE` | Where the tools run | The Workspace |
|---|---|---|
| `none` (default) | host subprocess + direct file tools | none — zero change, no Docker/Modal needed |
| `docker` | one session-persistent **local** container | `/workspace` is a **live bind mount** of the host `.decode/sandbox/` |
| `modal` | one session-persistent **remote** [`modal.Sandbox`](https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng) | nothing runs on your machine; `/workspace` is bootstrap-uploaded at launch and exported back on exit / `/ship` |

Both modes are one unified executor with **fresh-exec** semantics: the filesystem persists across calls, but `cd`/`export` don't (chain them: `cd /workspace/app && …`). The sandbox starts eagerly at launch (a `sandbox:<mode>` banner segment), and `bash` stays gated exactly as before — the sandbox is defense-in-depth *beneath* the approval prompt.

## Setup

Nothing here is needed for the first lessons — [01_install_and_usage.md](01_install_and_usage.md) gets the agent running with `SANDBOX_MODE=none`. Add the backend you want:

| Mode | Prerequisite | Install / authenticate |
| --- | --- | --- |
| `docker` | Docker running locally | [docker.com](https://www.docker.com/products/docker-desktop/), then start Docker Desktop |
| `modal` | Modal account tokens in the **process env** | `modal token set --token-id … --token-secret …` (writes `~/.modal.toml`). These are **not** decode settings — putting them in `.env` does nothing. |

| Env var | What it's for |
| --- | --- |
| `SANDBOX_MODE` | `none` (default) · `docker` · `modal` |
| `SANDBOX_REPO` | repo cloned into the Workspace; `--repo` overrides it |
| `SANDBOX_WORKSPACE_DIR` | the host dir that **is** the Workspace (default `.decode/sandbox`) |
| `SANDBOX_IMAGE`, `SANDBOX_TIMEOUT_S`, `SANDBOX_GIT_USER_NAME`, `SANDBOX_GIT_USER_EMAIL` | tunables — see below |
| `SANDBOX_GIT_TOKEN` | opt-in: lets the **model** push / open PRs from inside the Workspace ([below](#the-sandbox-git-token-sandbox_git_token)). A GitHub fine-grained, repo-scoped PAT. |

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

Hand-back (above) needs **no** credential in the sandbox — it pushes host-side. `SANDBOX_GIT_TOKEN` is the opt-in for the strictly larger ask: letting the **model itself** run `git push` / `gh pr create` from inside the Workspace. Set it and the token is direct-injected into the Worker's env as `GITHUB_TOKEN` in both backends, plus a git credential-helper so HTTPS pushes and `gh` authenticate ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md)) — exact mechanics, live proofs, and every negative case: [06_credentials.md Part 2](06_credentials.md#part-2--the-sandbox-git-token-sandbox_git_token).

> **A sandboxed process can read `$GITHUB_TOKEN`** — a prompt-injected agent too. Use a **fine-grained, repo-scoped, revocable** PAT, and revoke it when you're done. Leave the variable **unset** and the sandbox holds no credential at all — hand-back still ships your branch.

## Troubleshooting

Sandbox guards check **presence only** and fire in both the REPL and the headless pre-flight:

| What you see | What it means | Fix |
| --- | --- | --- |
| `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable` | `docker` selected with Docker stopped | start Docker Desktop, or set `SANDBOX_MODE=none`. |
| `Decode: SANDBOX_MODE=modal but Modal credentials are missing` | no Modal **account** tokens in the process env | `modal token set …`. `.env` does nothing for these. |
| `Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace …` | `--repo` passed with `SANDBOX_MODE=none` | set `SANDBOX_MODE=docker` or `modal`, or drop `--repo`. |
| `--repo` seems ignored, and there's no `origin` to push to | the Workspace was already populated — decode never re-clones over in-progress work | `rm -rf .decode/sandbox` to force a fresh clone. |
| `cd` or `export` from one `bash` call doesn't apply to the next | fresh-exec semantics: the filesystem persists, the process doesn't | chain them: `cd /workspace/app && …`. |

Everything else — provider keys, rate limits, skills, sessions — is in [00_troubleshooting.md](00_troubleshooting.md).

## Go further

- The end-to-end credential walkthrough — token set, token unset, and every negative case: [06_credentials.md](06_credentials.md).
- Run a sandboxed agent headless (`decode run --repo …`): [03_runtime.md](03_runtime.md).
- Replay a recorded run inside this same docker Workspace, on a Kitaru Worker: [03_runtime.md](03_runtime.md#replay-a-recorded-session-on-a-kitaru-worker); where the remote pieces live now: [07_infra.md](07_infra.md).
