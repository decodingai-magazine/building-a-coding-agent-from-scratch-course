# Sandboxing — isolated Workspaces, `--repo` & the git hand-back

Default (`SANDBOX_MODE=none`): `bash` is a host subprocess, file tools edit your working directory. With a **Sandbox Mode**, the agent's whole tool scope — file tools **and** `bash` — moves into an isolated Workspace; decode's own artifacts (sessions, memory, logs, permission file) stay in the launch directory ([ADR-0012](../docs/adr/0012-isolated-workspace.md)):

| `SANDBOX_MODE` | Where the tools run | The Workspace |
|---|---|---|
| `none` (default) | host subprocess + direct file tools | none — no Docker/Modal needed |
| `docker` | one session-persistent **local** container | `/workspace` = live bind mount of host `.decode/sandbox/` |
| `modal` | one session-persistent **remote** [`modal.Sandbox`](https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng) | nothing runs on your machine; `/workspace` uploaded at launch, exported back on exit / `/ship` |

**Fresh-exec** semantics in both modes: the filesystem persists across calls, `cd`/`export` don't (chain: `cd /workspace/app && …`). The sandbox starts at launch (`sandbox:<mode>` banner segment); `bash` stays gated by the approval prompt.

## Setup

| Mode | Prerequisite | Install / authenticate |
| --- | --- | --- |
| `docker` | Docker running locally | [docker.com](https://www.docker.com/products/docker-desktop/), start Docker Desktop |
| `modal` | Modal account tokens in the **process env** | `modal token set --token-id … --token-secret …` (writes `~/.modal.toml`). Not decode settings — `.env` does nothing for them. |

| Env var | What it's for |
| --- | --- |
| `SANDBOX_MODE` | `none` (default) · `docker` · `modal` |
| `SANDBOX_REPO` | repo cloned into the Workspace; `--repo` overrides |
| `SANDBOX_WORKSPACE_DIR` | host dir that **is** the Workspace (default `.decode/sandbox`) |
| `SANDBOX_IMAGE` | default `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`; each backend adds **git + `gh`** (docker per session, ~20s; modal in a cached image layer) |
| `SANDBOX_TIMEOUT_S` | modal sandbox lifetime |
| `SANDBOX_GIT_USER_NAME` / `_EMAIL` | in-Workspace commit identity |
| `SANDBOX_GIT_TOKEN` | opt-in: lets the **model** push / open PRs from inside the Workspace ([below](#the-sandbox-git-token-sandbox_git_token)). Fine-grained, repo-scoped PAT. |

## Work on any repo, and get a branch back

```bash
SANDBOX_MODE=docker decode --repo git@github.com:you/project.git
#   … the agent reads, edits, and runs bash entirely inside /workspace …
/ship          # or just quit — decode pushes a `decode/<session-id>` branch back
```

- **`--repo <url-or-path>`** (or `SANDBOX_REPO`; `--local` for a fast local clone) clones committed `HEAD` with your ambient git credentials. Bad repo → empty Workspace + one line. `--repo` without a sandbox mode → config error. Headless too: `SANDBOX_MODE=docker decode run --repo <url> "<task>"`. **Clones only into an empty Workspace** — a populated `.decode/sandbox` is reused, `--repo` ignored, no `origin` to push to. `rm -rf .decode/sandbox` to force a fresh clone.
- **Hand-back on exit or `/ship`** — commits uncommitted model work (model commits never rewritten), points `decode/<session-id>` at the result, pushes it. Every git command runs **host-side** with your ambient credentials — **no credential enters the sandbox**, with or without `SANDBOX_GIT_TOKEN`. Failed push → local branch stays in `.decode/sandbox`, named in the output. Unchanged Workspace → skipped.
- **Startup guards** — unavailable backend (Docker daemon down, missing `modal token set`) → one line, REPL and headless alike.
- **Isolation** — docker guards against *accidental* misbehavior (shared kernel on Linux; Docker Desktop's VM adds one on macOS); **modal** is for genuinely untrusted code (nothing executes on your machine). gVisor/Kata = daemon-config upgrades ([ADR-0011 isolation table](../docs/adr/0011-sandboxing-and-credential-proxy.md#isolation-backends-compared--why-docker--modal)).

## The sandbox git token (`SANDBOX_GIT_TOKEN`)

Hand-back needs no credential in the sandbox. `SANDBOX_GIT_TOKEN` is the opt-in for the larger ask: the **model itself** runs `git push` / `gh pr create` from inside the Workspace. One knob, both backends ([ADR-0016 §2](../docs/adr/0016-drop-credential-proxy.md)). Set it and the token enters the Worker's env as `GITHUB_TOKEN`:

| | How the value gets in | What it buys |
|---|---|---|
| `docker` | value-less `-e GITHUB_TOKEN` on `docker run`; value rides the docker **client's env**, never a host-visible argv | model can `git push` / `gh pr create` from `/workspace` |
| `modal` | a `modal.Secret` on the sandbox | same |
| `none` | n/a — the host, your ambient credentials | n/a |

Both backends chain the same git credential-helper (`x-access-token:$GITHUB_TOKEN`); `gh` reads `GITHUB_TOKEN` natively. Unset or empty → no env var, no helper, `docker run` argv byte-identical to the no-token case.

> **⚠️ A sandboxed process CAN read `$GITHUB_TOKEN`** ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md), *Consequences*). A prompt-injected agent can `echo $GITHUB_TOKEN`. Mitigation is policy: a **fine-grained, repo-scoped, revocable** PAT, revoked when done. Stronger property: leave `SANDBOX_GIT_TOKEN` unset, use host-side hand-back.

Token source = `Settings`, i.e. `.env` on a laptop — or, in a Modal container, that deployment's Secret ([04 §2b](04_deploy.md#2b-the-decode-headless-env-secret)), so the token never has to be in your shell.

> **Where a tool call runs.** Only `bash` and the file/search tools run in the Worker. **`web_fetch` runs host-side** (plain `httpx` in the decode process, [`tools/web.py`](../src/decode/tools/web.py)) — host network, no sandbox env. To exercise the sandbox, say **"use the bash tool"** and check the log for `running tool: bash`.

Walkthrough below: each case is an A/B. Prerequisite: `docker info >/dev/null && echo "docker ok"`.

### OFF — the default: no credential in the sandbox, and the branch still comes back

```bash
rm -rf .decode/sandbox        # ← REQUIRED before any --repo run; see below

env -u SANDBOX_GIT_TOKEN SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line and commit it"
```

Working: the model commits inside `/workspace`; on completion hand-back pushes `decode/<session-id>` with your host credentials ([`sandbox/handback.py`](../src/decode/sandbox/handback.py), [ADR-0012 §8](../docs/adr/0012-isolated-workspace.md)). Prove the Worker is credential-free, during the run, from a second terminal:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep -i token     # EMPTY — no GITHUB_TOKEN
docker exec $WORKER git config --global --get credential.helper   # EMPTY — no helper
```

> ### `rm -rf .decode/sandbox` before *any* `--repo` run
>
> `--repo` clones only into an empty Workspace; a populated `.decode/sandbox` is reused and `--repo` silently ignored — no `origin`, push dies with `'origin' does not appear to be a git repository`. Check: `git -C .decode/sandbox remote -v` (a clone has `origin`; leftover scratch has none).

### ON — the model pushes the branch and opens the PR itself

```bash
rm -rf .decode/sandbox        # ← OFF left a populated Workspace behind

SANDBOX_GIT_TOKEN=<fine-grained-PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

Working: branch pushed and PR opened from inside the sandbox. Verify the injection during the run:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep GITHUB_TOKEN                       # → GITHUB_TOKEN=<your PAT>  ← expected
docker exec $WORKER git config --global --get credential.helper   # → the x-access-token helper
ps aux | grep -F "<your-PAT>"                                     # EMPTY — never in a host argv
```

The first line printing your PAT is the cost of this design; the third line is the property the docker path keeps (value in the client's env, not in `docker run`'s argv). `SANDBOX_MODE=modal` is the same with a `modal.Secret`; same warning.

### Negatives — every way it must stay off

| Command | Working looks like |
|---|---|
| `SANDBOX_GIT_TOKEN= SANDBOX_MODE=docker uv run decode run "hi"` (explicit empty) | nothing injected — gated on the value, not presence: no `GITHUB_TOKEN`, no helper, argv identical to unset |
| `SANDBOX_GIT_TOKEN=<PAT> uv run decode run "hi"` (mode `none`) | no-op — no Worker env to inject into |
| Stale `SANDBOX_CREDENTIAL_PROXY_ENABLED=true` / `SANDBOX_PROXY_IMAGE=…` in `.env` | silently ignored — both keys deleted ([ADR-0016 §1](../docs/adr/0016-drop-credential-proxy.md)); `docker ps` shows the Worker alone |
| Docker daemon stopped, `SANDBOX_MODE=docker` | one stderr line, exit non-zero |

### Cleanup

```bash
rm -rf .decode/sandbox                                # the Workspace the walkthrough left behind
docker ps -a --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   # empty — the Worker is reaped
```

Revoke the PAT from ON.

### The automated backstop

Same claims, no PAT, no network:

```bash
# Token injection — the value-less -e passthrough, the shared credential helper, the empty-token
# byte-identical argv. Both backends, no docker, no modal.
uv run pytest tests/unit/decode/sandbox/test_docker_backend.py \
              tests/unit/decode/sandbox/test_modal_backend.py \
              tests/unit/decode/sandbox/test_workspace.py -v

# The capstone's one-mechanism claims: both backends share ONE helper + ONE gate; unset → no machinery.
uv run pytest tests/integration/test_sandbox_capstone.py -k token -v
```

[`test_sandbox_capstone.py`](../tests/integration/test_sandbox_capstone.py): one helper constant, one token gate, the secret in no argv; unset → no machinery; with a docker daemon or Modal credentials, live injection with a dummy token (no GitHub call).

## Troubleshooting

Guards check presence only; fire in the REPL and the headless pre-flight:

| What you see | What it means | Fix |
| --- | --- | --- |
| `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable` | Docker stopped | start Docker Desktop, or `SANDBOX_MODE=none`. |
| `Decode: SANDBOX_MODE=modal but Modal credentials are missing` | no Modal account tokens in the process env | `modal token set …`. `.env` does nothing for these. |
| `Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace …` | `--repo` with `SANDBOX_MODE=none` | set `docker` or `modal`, or drop `--repo`. |
| `--repo` ignored, no `origin` to push to | Workspace already populated | `rm -rf .decode/sandbox`. |
| `cd` / `export` don't carry to the next `bash` call | fresh-exec | chain: `cd /workspace/app && …`. |

Everything else: [00_troubleshooting.md](00_troubleshooting.md).

## Go further

- Headless in a sandbox (`decode run --repo …`), then on Modal where `SANDBOX_MODE=modal` is a *nested* sandbox and `SANDBOX_GIT_TOKEN` rides a Modal Secret: [04_deploy.md](04_deploy.md).
- Replay a recorded run inside this same docker Workspace: [06_evals_replays.md §5](06_evals_replays.md#5-start-a-worker-on-your-laptop-the-thing-that-executes-replays).
- Feed the token from a deployment's Modal Secret instead of `.env`: [04 §2b](04_deploy.md#2b-the-decode-headless-env-secret).
