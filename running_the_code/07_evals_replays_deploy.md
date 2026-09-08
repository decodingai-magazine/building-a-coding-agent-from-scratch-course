# Deploy — the Kitaru Worker on Modal

[06_evals_replays.md](06_evals_replays.md) runs replays on a **Kitaru Worker** in your laptop shell:
close the lid and the replay queue stalls. This page moves that Worker to
**[Modal](https://modal.com?source=decodingai&campaign=harnesseng)** — the same `kitaru worker start`,
in a gVisor container instead of your shell — so replays and experiments keep executing off-laptop
([ADR-0020 §5](../docs/adr/0020-remote-headless-on-modal.md)). Nothing else changes: the managed
workspace still schedules, a Worker still executes, and every `kitaru replay …` / `kitaru experiment …`
command from 06 works unchanged, pointed at a different Agent Version.

Budget: ~20 minutes. Needs 06 done once (a recorded Session to replay, an evaluator) and the Modal
account from [04_deploy.md §2a](04_deploy.md#2a-prerequisites).

---

## 1. What you are deploying

| Piece | Laptop Worker (06) | Modal Worker (this page) |
|---|---|---|
| The process | `kitaru worker start` in your shell | `scripts/modal_kitaru_worker.py` — a long-running Modal Function running `kitaru worker start`; app `decode-kitaru-worker` |
| Spawns | **agent version 2**: `SANDBOX_MODE=docker`, repo clone, Harness Home `~/.decode-kitaru-worker` | **agent version 3**: `SANDBOX_MODE=none`, in-image paths `/.uv/.venv/bin/decode` + `/harness` — the container *is* the isolation, so no Docker daemon is needed (and none exists there) |
| Its environment | your sourced `.env` | the `decode-kitaru-worker` Modal Secret |
| Claims | `agent` + `evaluator` + `importer` | `agent` + `evaluator` — never `importer`: importer jobs read export files that exist only on your machine |
| Lifetime | while the shell lives | until Modal's **24 h** function ceiling; one command re-launches it |

The image is the one the headless app builds (`decode.remote.image`, shared verbatim — [ADR-0020 §2](../docs/adr/0020-remote-headless-on-modal.md)),
so the in-image paths agent version 3 is registered with cannot drift from the container. **The two
Workers coexist, but each can only run its own Agent Version** — a Modal Worker that claims a v2
(docker) replay fails it, and a laptop Worker that claims a v3 replay fails it too. Scope the claims
(§3) whenever both are polling.

---

## 2. Set up

### 2a. Prerequisites

| Need | Get it |
|---|---|
| Modal account tokens on this machine | `uv run modal token set …` — [04_deploy.md §2a](04_deploy.md#2a-prerequisites) |
| A recorded Session + an evaluator on the workspace | [06_evals_replays.md §3–4](06_evals_replays.md#3-get-sessions-in--record-new-import-old) |
| A control plane API key (`ZENPROKEY_…`) | §2b — the one thing a container cannot get by `kitaru login` |

### 2b. Mint the container credential (a `ZENPROKEY_…`)

A container cannot run `kitaru login`, so the key travels in the Secret. On a **managed** workspace it
must be a **control plane** key (`ZENPROKEY_…`): a workspace-local key is rejected server-side with
`Local API keys are rejected under control plane authentication.` The kitaru client exchanges a
control plane key for a session token and keeps renewing it, so a worker stays authenticated for its
whole life.

> **Not minted yet — [`tasks/153`](../tasks/153-mint-control-plane-key-close-pending-gate.md).** Until
> it is, the Modal Worker refuses to start with one friendly line naming the missing variable (and a
> headless run with the Kitaru keys in its Secret degrades to "not recording", exit 0). **The same key
> closes both.**

Three ways to get one, best first:

```bash
# (A) RECOMMENDED — a scoped, expiring, revocable service-account key.
#     The device token in your kitaru credential store is refused on the key routes; exchange it for a
#     one-hour automation token first, which those routes DO accept.
TOKEN=$(curl -s -H "Authorization: Bearer <control-plane token from ~/.config/kitaru/credentials.json>" \
        https://cloudapi.zenml.io/auth/api_token | tr -d '"')
ORG=<your zenml pro organization uuid>
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts \
  -d '{"username":"decode-kitaru-worker","description":"Modal-hosted Kitaru Worker (ADR-0020 §5)"}'
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts/decode-kitaru-worker/api_keys \
  -d '{"name":"modal-worker","expires_in_minutes":43200}'        # → .key = ZENPROKEY_…

# (B) fallback — POST /users/me/api_keys instead: also expiring and revocable, but it acts as YOU.
# (C) NOT recommended — ship ~/.config/kitaru/credentials.json + KITARU_CONFIG_DIR into the container.
#     It works for ~30 days, but that is your personal, org-wide device credential in a container.
```

Put it in `.env` as `KITARU_API_KEY=ZENPROKEY_…` — it is read from there in the next step.

### 2c. The `decode-kitaru-worker` Secret

The Worker takes its entire environment from one Modal Secret. Create it once — key names below,
values from your shell, never committed:

```bash
set -a && . ./.env && set +a          # values come from your own .env, never echoed

uv run modal secret create decode-kitaru-worker \
  KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KITARU_API_KEY" \
  GEMINI_API_KEY="$GEMINI_API_KEY"          # deliberately NO KITARU_AGENT_ID

uv run modal secret list                    # listed; values are write-only
```

| Key | Required | Why |
|---|---|---|
| `KITARU_API_URL` | ✅ | which workspace to claim from |
| `KITARU_API_KEY` | ✅ | the `ZENPROKEY_…` from §2b — a container has no `kitaru login` store |
| `GEMINI_API_KEY` — or your provider's | ✅ | a replayed run has to reach a model, and the run spec attaches no secret of its own (06 §5): the Worker's env is what the replayed `decode run` sees |
| `KITARU_AGENT_ID` | ❌ **never** | the 403 trap, below |

**The missing `KITARU_AGENT_ID` is the whole point, not an oversight.** A Kitaru Worker injects a
*task-scoped* token into every run it spawns. With an agent id also in the env, decode's Recording
Seam probes an agents route that token cannot use and the replay hard-fails with
`403: Task credentials are not accepted on this route` — pitfall #3 in
[06_evals_replays.md §7](06_evals_replays.md#7-field-notes--the-pitfalls-we-actually-hit). The Worker
Function scrubs the variable defensively at startup with one logged line, but the Secret's
composition is the rule and the scrub is only the backstop. (The headless app's `decode-headless`
Secret *does* carry the agent id — it records user-launched runs, it never spawns replays. Two
Secrets, deliberately asymmetric.)

Add `--force` to the same command to update the Secret (it replaces the whole surface, so pass every
key again).

### 2d. Register agent version 3

Register the run spec this Worker spawns, once — `SANDBOX_MODE=none`, in-image paths:

```bash
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check --dry-run   # look first
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check
uv run kitaru agent version list decode
```

`--skip-bin-check` means "these paths live in the worker image" — the script then never stats,
resolves or creates them (they must be absolute for exactly that reason). The paths come from
`decode.remote.image`, so the image and the registration cannot drift apart.

**Pin `decode@3` when you replay, never "latest":** `latest_version` reads 4, and version 4 is a
byte-identical duplicate of 3 created by accident during QA (Kitaru versions are immutable, so it
stays). Note the id `kitaru agent version list` prints for version 3 — §3 needs it.

---

## 3. Deploy and start

```bash
uv run modal deploy scripts/modal_kitaru_worker.py
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 \
  --agent-version-id 01a029bf-0ae3-7de1-b594-4bc71a7ba91a          # = agent decode@3
```

`modal deploy` builds the image (a code change needs a re-deploy, exactly like the headless app);
`modal run --detach` starts the Worker and returns at once — the Function keeps polling with your
laptop closed.

`--agent-version-id` narrows the claim to `agent=<id>`. Use it whenever your laptop Worker is also
polling: both watch the same queue, and each can only run its **own** Agent Version. The matching
narrowing on the laptop side is `kitaru worker start --claim agent=<v2 id>`.

Without a credential the Worker refuses to start rather than polling silently for a day:

```
Decode: neither KITARU_API_KEY nor KITARU_API_TOKEN is set in this container. A container has no
`kitaru login` store, so the worker would poll unauthenticated for a day — add a control plane API
key (ZENPROKEY_…) to the decode-kitaru-worker secret.
```

Want, instead:

```bash
uv run kitaru worker list                     # a row 'decode-modal-worker', live: True
```

---

## 4. Verify with a replay

The same baseline replay as [06 §6](06_evals_replays.md#6-replay-then-compare), pinned to the
Modal Worker's version:

```bash
uv run kitaru replay create <RECORDED_SESSION_ID> --agent decode@3 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --evaluate-baselines
uv run kitaru job watch <JOB_ID>              # want: a terminal state with AGENT-level output
uv run kitaru replay get <REPLAY_ID>          # status + result_session_id
```

Want: the replay reaches a terminal state. An *agent-level* failure (a provider `503`) still proves
the pipe — the Worker claimed, spawned and ran decode. A spawn error (`ModuleNotFoundError`, command
not found) is the one that means the image or the run spec is wrong (06 §7.8).

Experiments run the same way: `kitaru experiment run start … --agent decode@3`.

---

## 5. Watch, stop, relaunch, cost

```bash
uv run modal app logs decode-kitaru-worker     # the Worker's own output, plus every claimed task's
uv run modal app stop decode-kitaru-worker
uv run modal app list                          # both apps deployed (plus stopped ephemeral apps — free)
```

The Worker dies at Modal's **24 h** function ceiling; re-launch it with the one `modal run --detach`
command from §3. Whatever it still held at that moment is kitaru's own task-timeout story,
deliberately not engineered around.

| Item | Cost |
|---|---|
| Modal-hosted Kitaru Worker | usage-based while it is up; the 24 h ceiling is also a cost ceiling |
| Managed Kitaru workspace | someone else's uptime, not your bill |
| Provider tokens | every replay is a real model run |

## 6. Troubleshooting

| Symptom | What it means |
|---|---|
| The Worker exits at once naming `KITARU_API_KEY` | the Secret has no control plane key — §2b, then `modal secret create … --force`. |
| `Local API keys are rejected under control plane authentication.` | a `KITKEY_…` (workspace-local) key in the Secret; a managed workspace needs a `ZENPROKEY_…`. |
| `kitaru worker list` shows no `decode-modal-worker` | not started (`modal run --detach …`), or it hit the 24 h ceiling — check `modal app logs decode-kitaru-worker`, relaunch. |
| A `decode@3` replay stays queued | the Modal Worker is down, or it was started with a different `--agent-version-id`. |
| A `decode@2` replay fails on the Modal Worker | a docker version claimed by a container with no daemon — scope the claims (§3) and re-create the replay with `--agent decode@3`. |
| `403: Task credentials are not accepted on this route` | `KITARU_AGENT_ID` reached the Worker's env — remove it from the Secret (`--force`), the scrub log line will confirm. |
| `ModuleNotFoundError` / command not found in the replay | the image or the registered paths are wrong — re-`modal deploy` and re-check §2d against `decode.remote.image`. |

## Go further

- [06_evals_replays.md](06_evals_replays.md) — the full loop the Worker serves: investigate, cohort,
  evaluator, replay, compare, experiment; the `kitaru-replay-experiment` skill for designing a
  what-if.
- [04_deploy.md](04_deploy.md) — the headless harness on Modal, whose runs (with the Kitaru keys in
  its Secret) become the Sessions this Worker replays.
- [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) §3 and §5 — why the Worker is a Modal
  Function and not a server, and why agent version 3 exists.
