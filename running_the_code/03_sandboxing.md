# 03. Sandboxing

By default, `bash` runs on your host and file tools edit your working directory. A **Sandbox Mode** moves the whole tool scope (file tools and `bash`) into an isolated `/workspace`. decode's own artifacts (sessions, memory, logs) stay in the launch directory ([ADR-0012](../docs/adr/0012-isolated-workspace.md)).

| `SANDBOX_MODE`   | Where tools run                                                                                                                                    | Prerequisite                                                                                                     |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `none` (default) | host                                                                                                                                               | —                                                                                                                |
| `docker`         | one local container per session; `/workspace` = bind mount of `.decode/sandbox/`; guards against accidents, not untrusted code                     | [Docker](https://www.docker.com) running                                                                         |
| `modal`          | one remote [Modal Sandbox](https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng) per session; nothing runs on your machine | `uv run modal token set …` ([More in the Modal endpoints setup](02_modal_endpoints.md#1-get-a-token-from-modal)) |

## 1. Work on any repo, get a branch back

You can kick off a decode session in an empty sandbox:

```bash
SANDBOX_MODE=docker decode
```

Or in a sandbox that pulls a GitHub repository with your Python code and automatically prepares the virtual env for your agent to work in:

```bash
rm -rf .decode/sandbox      # --repo clones only into an EMPTY Workspace; a populated one is reused silently
SANDBOX_MODE=docker decode --repo https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course
```

Then ask it to list the current directory:
![](../assets/sandbox_docker.png)

Or ask decode what operating system it is running on:
![](../assets/docker_sandbox_showing_output.png)

This is what it looks like in Docker Desktop:
![](../assets/sandbox_docker_session.png)

It works similarly in headless mode: `SANDBOX_MODE=docker decode run --repo https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course "list the current dir"`.

Other knobs (`.env`): `SANDBOX_WORKSPACE_DIR` (default `.decode/sandbox`), `SANDBOX_IMAGE` (each backend adds git + `gh`), `SANDBOX_TIMEOUT_S` (modal lifetime), `SANDBOX_GIT_USER_NAME` / `_EMAIL` (in-Workspace commit identity), `SANDBOX_GIT_TOKEN` (below).

## 2. Optional: Push code to GitHub from the sandbox (`SANDBOX_GIT_TOKEN`)

To give the coding agent write access to your repository (to create branches, push commits, and open PRs) while it works inside the sandbox, you need to issue a Git token, such as a GitHub personal access token (PAT). [More here](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

To plug it into decode, set the `SANDBOX_GIT_TOKEN` env var in your `.env` or inject it directly at runtime:

```bash
rm -rf .decode/sandbox
SANDBOX_GIT_TOKEN=<fine-grained-PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

Now your agent can create new branches, push code to them, and open PRs against main.

For context, `SANDBOX_GIT_TOKEN` is injected into the worker env as `GITHUB_TOKEN`, along with a Git credential helper ([ADR-0016 §2](../docs/adr/0016-drop-credential-proxy.md)).

> [!WARNING]
> **A sandboxed process can read `$GITHUB_TOKEN`.** A prompt-injected agent can `echo` it. Use a fine-grained, repo-scoped PAT and revoke it when done.

## 3. Cleanup

1. Remove the sandbox artifacts: `rm -rf .decode/sandbox`.
2. Revoke the PAT.

## 4. Run remote sandboxes via Modal

First, follow the Modal setup steps in [02_modal_endpoints.md](./02_modal_endpoints.md) (this takes ~5–10 minutes).

Then you can kick off a session in a remote Modal sandbox by switching `SANDBOX_MODE` to `modal`:

```shell
SANDBOX_MODE=modal decode
```

Or operate within a GitHub repository:

```shell
SANDBOX_MODE=modal decode --repo https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course
```

For write commands, make sure that `SANDBOX_GIT_TOKEN` is set in your `.env`.

Test it by asking decode what operating system it is running on:

![](../assets/modal_sandbox_showing_output.png)

To see your running sandboxes, go to Modal -> Apps -> `decode-sandbox-local` or `decode-sandbox-prod`:

![](../assets/modal_sandbox_dashboard.png)

## 5. Troubleshooting

| What you see                                                                      | Fix                                                                                         |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable`              | Start Docker Desktop, or set `SANDBOX_MODE=none`.                                           |
| `Decode: SANDBOX_MODE=modal but Modal credentials are missing`                    | Run `uv run modal token set …`.                                                             |
| `Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace …` | `--repo` needs `docker` or `modal`.                                                         |
| `'origin' does not appear to be a git repository` on push                         | The Workspace was already populated, so `--repo` was ignored. Run `rm -rf .decode/sandbox`. |

Everything else: [00_troubleshooting.md](00_troubleshooting.md).

---

**Next:** [04_deploy.md](04_deploy.md) — run the same headless `decode run` on Modal, from the CLI, a webhook, or a cron.
