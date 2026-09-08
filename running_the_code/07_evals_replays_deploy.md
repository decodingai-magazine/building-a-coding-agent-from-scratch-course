# Deploy — the Kitaru Worker on Modal

Run the Kitaru Worker from [06_evals_replays.md §5](06_evals_replays.md#5-start-a-worker-on-your-laptop-the-thing-that-executes-replays) as a [Modal](https://modal.com?source=decodingai&campaign=harnesseng) Function instead of your laptop shell, so replays and experiments keep executing with the laptop closed ([ADR-0020 §5](../docs/adr/0020-remote-headless-on-modal.md)). Every `kitaru replay …` / `kitaru experiment …` command from 06 works unchanged, pointed at a different Agent Version.

Budget: ~20 minutes. Needs 06 done once (a recorded Session, an evaluator) and Modal tokens ([04_deploy.md §2a](04_deploy.md#2a-prerequisites)).

---

## 1. What you are deploying

| | Laptop Worker (06) | Modal Worker (this page) |
|---|---|---|
| Process | `kitaru worker start` in your shell | `scripts/modal_kitaru_worker.py` — a long-running Function running `kitaru worker start`; app `decode-kitaru-worker` |
| Spawns | **agent version 2**: `SANDBOX_MODE=docker`, repo clone, Harness Home `~/.decode-kitaru-worker` | **agent version 3**: `SANDBOX_MODE=none`, in-image paths `/.uv/.venv/bin/decode` + `/harness` — the container is the isolation; no Docker daemon exists there |
| Environment | sourced `.env` | the `decode-kitaru-worker` Modal Secret (+ the `decode-prod` bucket at `DECODE_ENV=prod`, §2c) |
| Claims | `agent` + `evaluator` + `importer` | `agent` + `evaluator` — never `importer` (export files exist only on your machine) |
| Lifetime | while the shell lives | Modal's **24 h** function ceiling; one command re-launches |

Image = the headless app's (`decode.remote.image`, shared verbatim), so version 3's in-image paths cannot drift from the container. **Each Worker can only run its own Agent Version**: a Modal Worker claiming a v2 replay fails it; a laptop Worker claiming v3 fails it. Scope claims (§3) when both poll.

---

## 2. Set up

### 2a. Prerequisites

| Need | Get it |
|---|---|
| Modal account tokens | `uv run modal token set …` — [04_deploy.md §2a](04_deploy.md#2a-prerequisites) |
| A recorded Session + an evaluator | [06_evals_replays.md §3–4](06_evals_replays.md#3-get-sessions-in--record-new-import-old) |
| A control plane API key (`ZENPROKEY_…`) | §2b |
| The `decode-prod` bucket, for `DECODE_ENV=prod` | `make sync-secrets ENV=prod` — [06 §9](06_evals_replays.md#9-environments--decode_env-and-the-environment-bucket-optional) |

### 2b. Mint the container credential (a `ZENPROKEY_…`)

A container cannot `kitaru login`; the key travels in the Secret. On a managed workspace it must be a control plane key (`ZENPROKEY_…`) — a workspace-local key is rejected with `Local API keys are rejected under control plane authentication.` The client exchanges it for a session token and keeps renewing.

> **Not minted yet — [`tasks/153`](../tasks/153-mint-control-plane-key-close-pending-gate.md).** Until then the Modal Worker refuses to start with one line naming the missing variable, and a headless run with Kitaru keys in its Secret degrades to "not recording", exit 0. The same key closes both.

Three ways, best first:

```bash
# (A) RECOMMENDED — scoped, expiring, revocable service-account key.
#     The device token in your kitaru credential store is refused on the key routes; exchange it for a
#     one-hour automation token first.
TOKEN=$(curl -s -H "Authorization: Bearer <control-plane token from ~/.config/kitaru/credentials.json>" \
        https://cloudapi.zenml.io/auth/api_token | tr -d '"')
ORG=<your zenml pro organization uuid>
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts \
  -d '{"username":"decode-kitaru-worker","description":"Modal-hosted Kitaru Worker (ADR-0020 §5)"}'
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts/decode-kitaru-worker/api_keys \
  -d '{"name":"modal-worker","expires_in_minutes":43200}'        # → .key = ZENPROKEY_…

# (B) fallback — POST /users/me/api_keys: also expiring and revocable, but acts as YOU.
# (C) NOT recommended — ship ~/.config/kitaru/credentials.json + KITARU_CONFIG_DIR into the container:
#     your personal, org-wide device credential in a container (~30 days).
```

Put it in `.env` as `KITARU_API_KEY=ZENPROKEY_…`.

### 2c. `DECODE_ENV=prod` for both Modal apps

A container has no `.env`; its environment is a Modal Secret, and the Secret's `DECODE_ENV` picks the config surface ([ADR-0020 §11](../docs/adr/0020-remote-headless-on-modal.md), [06 §9](06_evals_replays.md#9-environments--decode_env-and-the-environment-bucket-optional)):

| `DECODE_ENV` in the Secret | Where `Settings` reads from | Sandbox app / Opik project |
|---|---|---|
| unset (`local`) | the Secret itself — must carry every key ([04 §2b](04_deploy.md#2b-the-decode-headless-secret)) | `decode-sandbox-local` / `decode-local` |
| `prod` (or `staging`, `dev`) | the `decode-<env>` Environment Bucket; the Secret carries only the bootstrap | `decode-sandbox-prod` / `decode-prod` |

Mirror `.env` into the bucket once, then recreate both Secrets with the bootstrap only. The `ZENPROKEY_…` from §2b is what lets a container read the bucket:

```bash
make sync-secrets ENV=prod            # provider keys, KITARU_AGENT_ID, SANDBOX_GIT_TOKEN → decode-prod
set -a && . ./.env && set +a

uv run modal secret create decode-headless \
  DECODE_ENV=prod \
  KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KITARU_API_KEY" --force

uv run modal secret create decode-kitaru-worker \
  DECODE_ENV=prod \
  KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KITARU_API_KEY" --force   # deliberately NO KITARU_AGENT_ID

uv run modal secret list
```

| Key | `decode-headless` | `decode-kitaru-worker` | Why |
|---|---|---|---|
| `DECODE_ENV` | `prod` | `prod` | hydrate from `decode-prod`; names the nested sandbox app and the Opik project |
| `KITARU_API_URL` | ✅ | ✅ | the workspace: bucket source for both, claim source for the Worker |
| `KITARU_API_KEY` | ✅ | ✅ | the `ZENPROKEY_…` from §2b |
| `KITARU_AGENT_ID` | via the bucket | ❌ **never** in the Secret | the headless app records user-launched runs under it. A Worker Task's token is task-scoped; the Recording Seam ignores a configured id under a Worker Task (so the bucket carrying it is harmless), and the Function scrubs it from the env at startup — the Secret still omits it by composition. |
| provider keys, `SANDBOX_GIT_TOKEN` | via the bucket | via the bucket | the run spec attaches no secret (06 §5); at `prod` the bucket is the Worker's provider source |

No redeploy after a Secret change: it is read when a container starts. Until `tasks/153` is closed keep `DECODE_ENV` out of both Secrets — a `prod` container that cannot load the bucket exits 1 at startup.

### 2d. Register agent version 3

```bash
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check --dry-run   # look first
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check
uv run kitaru agent version list decode
```

`--skip-bin-check` = "these paths live in the worker image"; the script never stats, resolves or creates them (hence absolute). Paths come from `decode.remote.image`.

**Pin `decode@3` when you replay, never "latest":** `latest_version` reads 4, a byte-identical accidental duplicate of 3 (versions are immutable). Note version 3's id from the list — §3 needs it.

---

## 3. Deploy and start

```bash
uv run modal deploy scripts/modal_kitaru_worker.py
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 \
  --agent-version-id 01a029bf-0ae3-7de1-b594-4bc71a7ba91a          # = agent decode@3
```

`modal deploy` builds the image (re-deploy after a code change). `modal run --detach` starts the Worker and returns; it keeps polling with the laptop closed.

`--agent-version-id` narrows the claim to `agent=<id>`. Use it whenever the laptop Worker also polls; the laptop-side equivalent is `kitaru worker start --claim agent=<v2 id>`.

No credential → refuses to start:

```
Decode: neither KITARU_API_KEY nor KITARU_API_TOKEN is set in this container. A container has no
`kitaru login` store, so the worker would poll unauthenticated for a day — add a control plane API
key (ZENPROKEY_…) to the decode-kitaru-worker secret.
```

Want:

```bash
uv run kitaru worker list                     # row 'decode-modal-worker', live: True
```

---

## 4. Verify with a replay

Same baseline replay as [06 §6](06_evals_replays.md#6-replay-then-compare), pinned to `decode@3`:

```bash
uv run kitaru replay create <RECORDED_SESSION_ID> --agent decode@3 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --evaluate-baselines
uv run kitaru job watch <JOB_ID>              # want: terminal state with AGENT-level output
uv run kitaru replay get <REPLAY_ID>          # status + result_session_id
```

An agent-level failure (provider `503`) still proves the pipe. A spawn error (`ModuleNotFoundError`, command not found) means the image or run spec is wrong (06 §7.8).

Experiments: `kitaru experiment run start … --agent decode@3`.

---

## 5. Watch, stop, relaunch, cost

```bash
uv run modal app logs decode-kitaru-worker
uv run modal app stop decode-kitaru-worker
uv run modal app list
```

The Worker dies at Modal's **24 h** ceiling; re-launch with the `modal run --detach` command from §3. In-flight tasks at that moment fall to kitaru's own task timeout.

| Item | Cost |
|---|---|
| Modal-hosted Worker | usage-based while up; 24 h ceiling = cost ceiling |
| Managed workspace | not your bill |
| Provider tokens | every replay is a real model run |

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Worker exits at once naming `KITARU_API_KEY` | §2b, then `modal secret create … --force`. |
| `Decode: DECODE_ENV=prod but the environment bucket 'decode-prod' could not be loaded …` (either app) | no bucket (`make sync-secrets ENV=prod`), or the Secret's `KITARU_API_KEY` is missing / not a `ZENPROKEY_…`. |
| `Decode: set GEMINI_API_KEY in your environment` in a `prod` run | the bucket lacks it — `make sync-secrets ENV=prod`. |
| Runs land in `decode-sandbox-local` / `decode-local` | `DECODE_ENV` unset in the Secret — set it to `prod` (§2c). |
| `Local API keys are rejected under control plane authentication.` | `KITKEY_…` in the Secret; needs `ZENPROKEY_…`. |
| No `decode-modal-worker` in `kitaru worker list` | not started, or hit the 24 h ceiling — `modal app logs decode-kitaru-worker`, relaunch. |
| `decode@3` replay stays queued | Modal Worker down, or started with a different `--agent-version-id`. |
| `decode@2` replay fails on the Modal Worker | no Docker daemon in a container — scope claims (§3), re-create with `--agent decode@3`. |
| `403: Task credentials are not accepted on this route` | the Worker's `KITARU_API_KEY` was refused — re-mint (§2b), recreate the Secret with `--force`. |
| `ModuleNotFoundError` / command not found in the replay | re-`modal deploy`; re-check §2d paths against `decode.remote.image`. |

## Go further

- [06_evals_replays.md](06_evals_replays.md) — the loop this Worker serves; `kitaru-replay-experiment` skill for what-ifs.
- [04_deploy.md](04_deploy.md) — headless harness on Modal; its runs become the Sessions this Worker replays.
- [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) §3, §5.
