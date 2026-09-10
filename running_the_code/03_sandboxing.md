# 03 — Sandboxing: isolated Workspaces, `--repo`, and the git hand-back

By default `bash` runs on your host and file tools edit your working directory. A **Sandbox Mode** moves the whole tool scope, file tools and `bash`, into an isolated `/workspace`. decode's own artifacts (sessions, memory, logs) stay in the launch directory ([ADR-0012](../docs/adr/0012-isolated-workspace.md)).

| `SANDBOX_MODE` | Where tools run | Prerequisite |
|---|---|---|
| `none` (default) | host | — |
| `docker` | one local container per session; `/workspace` = bind mount of `.decode/sandbox/`; guards against accidents, not untrusted code | [Docker Desktop](https://www.docker.com/products/docker-desktop/) running |
| `modal` | one remote [Modal Sandbox](https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng); nothing runs on your machine | `uv run modal token set …` ([02 §1](02_modal_endpoints.md#1-create-the-endpoint)) |

Fresh-exec in both modes: the filesystem persists across calls, `cd` / `export` do not (chain: `cd /workspace/app && …`).

## 1. Work on any repo, get a branch back

```bash
rm -rf .decode/sandbox      # --repo clones only into an EMPTY Workspace; a populated one is reused silently
SANDBOX_MODE=docker decode --repo https://github.com/<you>/<repo>
#   … the agent reads, edits, and runs bash inside /workspace …
/ship          # or just quit: decode pushes a decode/<session-id> branch to origin
```

Headless is the same: `SANDBOX_MODE=docker decode run --repo <url> "<task>"`.

- `--summary-json <path>` additionally writes one JSON object for that run — `session_id`, `exit_reason` (`completed` / `request_limit` / `error`), `requests`, tokens, `cost_usd`, the `handback` branch, and the answer — which is how the eval benchmark reads a run's outcome without parsing traces. Nothing else about the run changes.

- `--repo <url-or-path>` (or `SANDBOX_REPO`) clones `HEAD` with your ambient git credentials. `--local` = fast local clone.
- **Hand-back** on exit or `/ship` commits uncommitted model work, pushes `decode/<session-id>`. Every git command runs **host-side**: no credential enters the sandbox. Failed push: the branch stays in `.decode/sandbox`. Unchanged Workspace: skipped.

> ✅ `git ls-remote <url> 'refs/heads/decode/*'` lists the new branch.

Other knobs (`.env`): `SANDBOX_WORKSPACE_DIR` (default `.decode/sandbox`), `SANDBOX_IMAGE` (each backend adds git + `gh`), `SANDBOX_TIMEOUT_S` (modal lifetime), `SANDBOX_GIT_USER_NAME` / `_EMAIL` (in-Workspace commit identity), `SANDBOX_GIT_TOKEN` (below).

## 2. Optional: let the model push (`SANDBOX_GIT_TOKEN`)

`SANDBOX_GIT_TOKEN` is the opt-in for the **model itself** running `git push` / `gh pr create` from inside the Workspace. Both backends inject it into the worker env as `GITHUB_TOKEN` plus a git credential helper ([ADR-0016 §2](../docs/adr/0016-drop-credential-proxy.md)).

> ⚠️ **A sandboxed process can read `$GITHUB_TOKEN`.** A prompt-injected agent can `echo` it. Use a fine-grained, repo-scoped PAT and revoke it when done.

```bash
rm -rf .decode/sandbox
SANDBOX_GIT_TOKEN=<fine-grained-PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

> ✅ Branch pushed and PR opened from inside the sandbox. During the run, from a second terminal:
>
> ```bash
> WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
> docker exec $WORKER env | grep GITHUB_TOKEN     # → GITHUB_TOKEN=<your PAT>   (the cost of this design)
> ps aux | grep -F "<your-PAT>"                   # EMPTY — never in a host argv
> ```
>
> Unset the token, rerun the first block: both `docker exec` lines print nothing, and hand-back still ships the branch.

Only `bash` and file/search tools run in the Worker; `web_fetch` runs host-side.

Cleanup: `rm -rf .decode/sandbox`, revoke the PAT. Same claims without a PAT or network: `uv run pytest tests/integration/test_sandbox_capstone.py -k token -v`.

## 3. Troubleshooting

| What you see | Fix |
| --- | --- |
| `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable` | start Docker Desktop, or `SANDBOX_MODE=none`. |
| `Decode: SANDBOX_MODE=modal but Modal credentials are missing` | `uv run modal token set …`. |
| `Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace …` | `--repo` needs `docker` or `modal`. |
| `'origin' does not appear to be a git repository` on push | Workspace was already populated, `--repo` ignored: `rm -rf .decode/sandbox`. |

Everything else: [00_troubleshooting.md](00_troubleshooting.md).

---

**Next:** [04_deploy.md](04_deploy.md) — run the same headless `decode run` on Modal, from the CLI, a webhook, or a cron.
