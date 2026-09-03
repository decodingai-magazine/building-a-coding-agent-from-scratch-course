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

- **`--repo <url-or-path>`** (or `SANDBOX_REPO`; add `--local` for a fast local clone) clones at committed `HEAD` using your ambient git credentials. A bad repo degrades to an empty Workspace with one friendly line; `--repo` without a sandbox mode is a friendly config error. Works headless too: `SANDBOX_MODE=docker decode run --repo <url> "<task>"` ([04_deploy.md](04_deploy.md)). **It clones only into an *empty* Workspace** — a populated `.decode/sandbox` is reused, never re-cloned (that would discard in-progress work), so `--repo` against a leftover Workspace is ignored and there is no `origin` to push to. `rm -rf .decode/sandbox` to force a fresh clone.
- **Hand-back on exit or `/ship`** — decode commits any uncommitted model work (model commits are preserved, never rewritten), points a `decode/<session-id>` branch at the result, and pushes it. Every git command runs **host-side**, with your ambient git credentials — **hand-back puts no credential in the sandbox** (that holds whether or not you set [`SANDBOX_GIT_TOKEN`](#the-sandbox-git-token-sandbox_git_token)). A failed push still leaves the local branch in `.decode/sandbox` and names it; an unchanged Workspace is skipped.
- **Startup guards** — a selected backend that isn't available fails with one friendly line (Docker daemon down, or missing `modal token set` credentials), in the REPL and the headless pre-flight alike.
- **Isolation honesty** — docker is a boundary for *accidental* misbehavior (shared kernel on Linux; Docker Desktop's VM adds one on macOS); **modal** is the rung for genuinely untrusted code (nothing executes on your machine). gVisor/Kata are zero-code daemon-config upgrades; see [ADR-0011's isolation table](../docs/adr/0011-sandboxing-and-credential-proxy.md#isolation-backends-compared--why-docker--modal).

Tunables (all optional, documented in [`.env.example`](../.env.example)): `SANDBOX_IMAGE` (default `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` — python + uv preinstalled; each backend adds **git + the `gh` CLI**, so a model can commit, push, *and* open the PR — docker installs them per session (~20s), modal bakes them into a cached image layer), `SANDBOX_TIMEOUT_S` (modal lifetime), `SANDBOX_GIT_USER_NAME`/`_EMAIL` (the in-Workspace commit identity), `SANDBOX_GIT_TOKEN` (below).

## The sandbox git token (`SANDBOX_GIT_TOKEN`)

Hand-back (above) needs **no** credential in the sandbox — it pushes host-side. `SANDBOX_GIT_TOKEN` is the opt-in for the strictly larger ask: letting the **model itself** run `git push` / `gh pr create` from inside the Workspace. One knob, one mechanism, **both backends** ([ADR-0016 §2](../docs/adr/0016-drop-credential-proxy.md)). Set it and the token enters the **Worker**'s env as `GITHUB_TOKEN`:

| | how the value gets in | what it buys |
|---|---|---|
| `docker` | a **value-less** `-e GITHUB_TOKEN` on `docker run`; the value rides the docker **client's env**, so it never sits in a host-visible argv (no `ps`, no rendered error can read it) | the model can `git push` / `gh pr create` from inside `/workspace` |
| `modal` | a `modal.Secret` on the sandbox | the same |
| `none` | n/a — `none` *is* the host, with your own ambient credentials | n/a |

Both backends then chain the **same** git credential-helper (`x-access-token:$GITHUB_TOKEN`), so git's
HTTPS transport authenticates; `gh` reads `GITHUB_TOKEN` natively. Unset or empty → **no env var, no
credential helper, and a `docker run` argv byte-identical to the no-token case**. The sandbox holds no
credential at all.

> **⚠️ A sandboxed process CAN read `$GITHUB_TOKEN`.** That is the deliberate cost of deleting the Credential
> Proxy, stated plainly ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md), *Consequences*). A prompt-injected
> agent can `echo $GITHUB_TOKEN`. The mitigation is **policy, not code**: hand it a **fine-grained,
> repo-scoped, revocable** PAT — never a broadly-scoped or organisation-wide one — and revoke it when you are
> done. Want the stronger property? **Leave `SANDBOX_GIT_TOKEN` unset** and take host-side hand-back (OFF, below).

**Where does the token itself come from?** The one config surface: `.env` at `local`, the Environment Bucket at a
remote `DECODE_ENV` ([01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional)) —
`sandbox_git_token()` reads the already-hydrated `Settings`, no second lookup. Mirror it like any other
key and `DECODE_ENV=staging SANDBOX_MODE=docker decode run --repo …` works with the token absent from your
shell entirely.

> **Where a tool call actually runs.** Only `bash` (and the file/search tools) run inside the Worker.
> **`web_fetch` runs host-side** — a plain `httpx` call in the decode process ([`tools/web.py`](../src/decode/tools/web.py)) —
> so it sees the *host's* network and none of the sandbox's environment. Ask the model to "GET this URL" and it
> will reach for `web_fetch`, never touching the Worker or its `GITHUB_TOKEN`. If you mean to exercise the
> sandbox, say **"use the bash tool"** in the prompt and check the run log for `running tool: bash`.

The rest of this section is a manual e2e walkthrough. Every case is an **A/B**: the same command with one thing
flipped, and a different observable. The automated backstop at the end makes the same claims with no network,
no PAT, no daemon. Prerequisite: `docker info >/dev/null && echo "docker ok"`.

### OFF — the default: no credential in the sandbox, and the branch still comes back

```bash
rm -rf .decode/sandbox        # ← REQUIRED before any --repo run; see the box below

env -u SANDBOX_GIT_TOKEN SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line and commit it"
```

Working: the model commits inside `/workspace`, and on completion **hand-back** pushes a `decode/<session-id>`
branch to your repo — with your **ambient host git credentials**, because every hand-back git command is a
*host* subprocess against `.decode/sandbox` ([`sandbox/handback.py`](../src/decode/sandbox/handback.py),
[ADR-0012 §8](../docs/adr/0012-isolated-workspace.md)). Prove the Worker is credential-free, during the run, from a
second terminal:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep -i token     # EMPTY — no GITHUB_TOKEN, nothing
docker exec $WORKER git config --global --get credential.helper   # EMPTY — no helper either
```

**This is the safe default, and it already ships your work back.** You only need ON for the strictly
larger ask: letting the **model itself** push and open the PR.

> ### `rm -rf .decode/sandbox` before *any* `--repo` run — not optional
>
> `--repo` clones **only into an empty Workspace** (above); a populated
> `.decode/sandbox` is reused and `--repo` **silently ignored** — no `origin`, and the push dies with a
> baffling `'origin' does not appear to be a git repository`. Confirm before you blame the token:
> `git -C .decode/sandbox remote -v` — a clone has an `origin`, a leftover scratch tree has none.

### ON — the model pushes the branch and opens the PR itself

```bash
rm -rf .decode/sandbox        # ← OFF left a populated Workspace behind

SANDBOX_GIT_TOKEN=<fine-grained-PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

Working: the branch is pushed and the PR opens — from *inside* the sandbox, off the injected token. Both
`git` **and** `gh` are installed in the Worker (docker installs them per session; modal bakes them into a
cached image layer), so `git push` authenticates through the credential helper and `gh pr create` off
`GITHUB_TOKEN`. Verify the injection during the run:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep GITHUB_TOKEN                       # → GITHUB_TOKEN=<your PAT>  ← YES, it prints
docker exec $WORKER git config --global --get credential.helper   # → the x-access-token helper
ps aux | grep -F "<your-PAT>"                                     # EMPTY — never in a host argv
```

**That first line printing your PAT is the expected result**, and it is the whole honest cost of this design:
the Worker holds the token, so anything running in the Worker can read it. The third line is the one property
the docker path still buys you — the value rides the docker client's env, so it is not in `docker run`'s argv
and cannot be scraped from the host process table.

`SANDBOX_MODE=modal` is the same story with a `modal.Secret`, and the same warning applies (the token is in
the remote sandbox's env).

### Negatives — every way it must stay off

| Command | Working looks like |
|---|---|
| `SANDBOX_GIT_TOKEN= SANDBOX_MODE=docker uv run decode run "hi"` (explicit empty) | **nothing injected** — gated on the *value*, not on presence: no `GITHUB_TOKEN`, no credential helper, and an argv identical to the unset case |
| `SANDBOX_GIT_TOKEN=<PAT> uv run decode run "hi"` (mode `none`) | no-op — `none` *is* the host; there is no Worker env to inject into |
| A stale `SANDBOX_CREDENTIAL_PROXY_ENABLED=true` / `SANDBOX_PROXY_IMAGE=…` in your `.env` | **silently ignored** — both keys are deleted ([ADR-0016 §1](../docs/adr/0016-drop-credential-proxy.md)); no proxy container exists to start. `docker ps` shows the Worker, alone. |
| Docker daemon stopped, `SANDBOX_MODE=docker` | one friendly stderr line, exit non-zero, no flow built |

### Cleanup

```bash
rm -rf .decode/sandbox                                # the Workspace the walkthrough left behind
docker ps -a --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   # empty — the Worker is reaped
```

And revoke the PAT you handed the sandbox in ON. It was scoped and revocable — that was the point.

### The automated backstop

Everything above is covered without a PAT and without network:

```bash
# Token injection — the value-less -e passthrough, the shared credential helper, the empty-token
# byte-identical argv. Both backends, no docker, no modal.
uv run pytest tests/unit/decode/sandbox/test_docker_backend.py \
              tests/unit/decode/sandbox/test_modal_backend.py \
              tests/unit/decode/sandbox/test_workspace.py -v

# The capstone's one-mechanism claims: both backends share ONE helper + ONE gate; unset → no machinery.
uv run pytest tests/integration/test_sandbox_capstone.py -k token -v
```

[`test_sandbox_capstone.py`](../tests/integration/test_sandbox_capstone.py) asserts the manual claims:
one helper constant, one token gate, the secret in **no** argv; unset → no credential machinery; and, when a
docker daemon or Modal credentials exist, live injection with a dummy token (no GitHub call).

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

- Run a sandboxed agent headless (`decode run --repo …`), then off-laptop on Modal, where `SANDBOX_MODE=modal` becomes a *nested* sandbox and `SANDBOX_GIT_TOKEN` rides a Modal Secret: [04_deploy.md](04_deploy.md).
- Replay a recorded run inside this same docker Workspace, on a Kitaru Worker: [04_deploy.md §6](04_deploy.md#6-replay-a-recorded-session-on-a-kitaru-worker).
- Feed the token from an Environment Bucket instead of `.env`: [01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional).
