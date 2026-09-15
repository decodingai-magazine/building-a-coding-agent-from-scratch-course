# 07 — Deploy the Kitaru Worker on Modal

Run the [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) Worker from [06 §4](06_evals_replays.md#4-start-a-worker-on-your-laptop) as a Modal Function, so replays and experiments keep executing with the laptop closed ([ADR-0020 §5](../docs/adr/0020-remote-headless-on-modal.md)). Every `kitaru replay …` / `kitaru experiment …` command from 06 works unchanged, pointed at a different Agent Version. ~20 minutes; needs 06 done once and Modal tokens.

| | Laptop Worker (06) | Modal Worker (this page) |
|---|---|---|
| Process | `kitaru worker start` in your shell | `scripts/modal_kitaru_worker.py`, app `decode-kitaru-worker-<env>` |
| Spawns | **agent version 2**: `SANDBOX_MODE=docker`, repo clone | **agent version 3**: `SANDBOX_MODE=none`, the container is the isolation |
| Config | sourced `.env` | the `decode-kitaru-worker-<env>` Secret |
| Claims | agent + evaluator + importer | agent + evaluator (export files live on your machine) |
| Lifetime | the shell | Modal's 24 h ceiling |

Each Worker runs only its own Agent Version: a Modal Worker claiming a docker-mode replay fails it, and vice versa.

> **The server must be reachable from Modal.** A Modal Worker dials `KITARU_API_URL` out of its
> container, so the local OSS server from [06 §0](06_evals_replays.md#0-pick-a-server)
> (`http://localhost:8000`) is laptop-only — this page needs the managed workspace (when it resumes;
> it is deactivated today) or any other server with a public URL. Register decode on it first:
> `uv run python scripts/bootstrap_kitaru.py --server <url>`.
>
> The version NUMBERS below are the course workspace's history (`decode@2` docker, `decode@3`
> `none`). On a freshly bootstrapped server the same two specs are `decode@1` and `decode@2` — read
> the real ones off `uv run kitaru agent version list decode` and match on `SANDBOX_MODE`, never on
> the number.

## 1. Mint the container credential

A container cannot `kitaru login`; the key rides the Secret. On a managed workspace it must be a control plane key (`ZENPROKEY_…`); a workspace-local `KITKEY_…` is rejected.

> Not minted yet for this course: [`tasks/153`](../tasks/153-mint-control-plane-key-close-pending-gate.md). Until then the Modal Worker refuses to start with one line naming `KITARU_API_KEY`.

```bash
# scoped, expiring, revocable service-account key. The device token in your kitaru credential
# store is refused on the key routes: exchange it for a one-hour automation token first.
TOKEN=$(curl -s -H "Authorization: Bearer <control-plane token from ~/.config/kitaru/credentials.json>" \
        https://cloudapi.zenml.io/auth/api_token | tr -d '"')
ORG=<your zenml pro organization uuid>
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts \
  -d '{"username":"decode-kitaru-worker","description":"Modal-hosted Kitaru Worker"}'
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts/decode-kitaru-worker/api_keys \
  -d '{"name":"modal-worker","expires_in_minutes":43200}'        # → .key = ZENPROKEY_…
```

Fallback: `POST /users/me/api_keys` (acts as you). Do not ship `~/.config/kitaru/credentials.json` into a container.

Put it in `.env`:

```bash
KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
KITARU_API_KEY=ZENPROKEY_...
```

## 2. Create the `decode-kitaru-worker-<env>` Secret

Same rules as [04 §2](04_deploy.md#2-create-the-decode-headless-env-secret). It needs the workspace credential the Worker claims with **and** the provider keys every replay runs on. Never `KITARU_AGENT_ID`: a spawned replay that inherits it probes a route its task token cannot use and fails with `403`.

```bash
set -a && . ./.env && set +a

uv run modal secret create "decode-kitaru-worker-${DECODE_ENV:?set it in .env or export it first}" \
  KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KITARU_API_KEY" \
  LLM_PROVIDER="${LLM_PROVIDER:-gemini}" \
  GEMINI_API_KEY="$GEMINI_API_KEY" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force
```

Swap the provider lines for the block matching your `LLM_PROVIDER` in [04 §2](04_deploy.md#2-create-the-decode-headless-env-secret).

> ✅ `uv run modal secret list` shows `decode-kitaru-worker-<env>`.

## 3. Deploy and start

```bash
uv run modal deploy scripts/modal_kitaru_worker.py                  # → decode-kitaru-worker-$DECODE_ENV
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 \
  --agent-version-id <uuid of decode@3 from `uv run kitaru agent version list decode`>
```

`modal deploy` builds the image (redeploy after a code or Secret change); `modal run --detach` starts the Worker and returns. `--agent-version-id` narrows the claim to that version; use it whenever the laptop Worker also polls (laptop side: `kitaru worker start --claim agent=<v2 id>`).

> ✅ `uv run kitaru worker list` shows `decode-modal-worker`, `live: True`.

## 4. Verify with a replay

Same baseline replay as [06 §5](06_evals_replays.md#5-replay-then-compare), pinned to `decode@3`:

```bash
uv run kitaru replay create <SESSION_ID> --agent decode@3 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --baseline-evaluation-mode if-missing
uv run kitaru job watch <JOB_ID>
uv run kitaru replay get <REPLAY_ID>
```

> ✅ A terminal state with agent-level output (a provider `503` still counts, see [06 §6](06_evals_replays.md#6-troubleshooting)).

Experiments: `kitaru experiment run start … --agent decode@3`.

## 5. Watch, stop, cost

```bash
uv run modal app logs "decode-kitaru-worker-${DECODE_ENV:?}"
uv run modal app stop "decode-kitaru-worker-${DECODE_ENV:?}"
uv run modal app list
```

Past the 24 h ceiling, relaunch with the `modal run --detach` line from §3. Usage-based while up; every replay is a real model run on your provider key.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Worker exits at once naming `KITARU_API_KEY` | §1, re-create the Secret, redeploy. |
| `Local API keys are rejected under control plane authentication.` | `KITKEY_…` in the Secret; needs `ZENPROKEY_…`. |
| `Decode: this deployment is DECODE_ENV=… but its Secret carries …` | remove `DECODE_ENV` from the Secret, or redeploy at the env it names. |
| `Decode: set GEMINI_API_KEY in your environment` in a replay | the Secret lacks the provider key: re-create, redeploy. |
| No `decode-modal-worker` in `kitaru worker list` | not started, or past the 24 h ceiling: check logs, relaunch. |
| `decode@3` replay stays queued | Modal Worker down, or started with a different `--agent-version-id`. |
| A docker-mode replay fails on the Modal Worker | no Docker in a container: re-create against the `none` version (`decode@3` here, `decode@2` on a freshly bootstrapped server). |
| `403: Task credentials are not accepted on this route` | the Worker's `KITARU_API_KEY` was refused: re-mint (§1), re-create the Secret, redeploy. |
| `ModuleNotFoundError` / command not found in the replay | `modal deploy` again. |

---

**Done.** You have the full stack: a coding agent on your own model, sandboxed, deployed, traced, benchmarked, recorded, and replayed off-laptop. Back to the [course README](../README.md) for the lessons.
