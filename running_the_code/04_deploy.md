# Deploy — the headless harness on Modal

Run `decode run` on [Modal](https://modal.com?source=decodingai&campaign=harnesseng) instead of your laptop, triggered from the CLI, a webhook, or a cron schedule. The laptop only launches; a Modal container executes the same `decode` console script ([ADR-0020](../docs/adr/0020-remote-headless-on-modal.md)). No server; a deployed app with nothing running costs nothing.

---

## 1. What you are deploying: `decode run`

From locally executing your agent loop via:

```bash
uv run decode run "list the python files under src and summarize what the cli module does"
```

To executing it remotely as:

```bash
uv run decode remote run "list the python files under src and summarize what the cli module does"
```

---

## 2. Set up

### 2a. Prerequisites

| Need                             | Why                                                      | Get it                                                                                                                                                                                                              |
| -------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Core setup                       | `uv`, the repo, `make install`, a provider key in `.env` | [01_install_and_usage.md](01_install_and_usage.md)                                                                                                                                                                  |
| Modal account + CLI tokens       | `decode remote` talks to your Modal workspace            | `uv run modal token set --token-id … --token-secret …` — from [modal.com](https://modal.com?source=decodingai&campaign=harnesseng) at signup ($30 free credits). Not decode settings; `.env` does nothing for them. |
| Modal **proxy token** pair       | webhook auth (§4)                                        | `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` in `.env` — [02_modal_endpoints.md](02_modal_endpoints.md#authentication--proxy-tokens)                                                                         |
| `SANDBOX_GIT_TOKEN` _(optional)_ | lets `--sandbox-mode modal` runs push a branch back      | scoped, revocable PAT — [03_sandboxing.md](03_sandboxing.md#the-sandbox-git-token-sandbox_git_token)                                                                                                                |

No Kitaru account is needed for anything on this page: recording is opt-in and separate ([06](06_evals_replays.md)).

No extra install: `modal` ships with decode's dependencies.

### 2b. The `decode-headless-<env>` Secret

One environment = one deployment = one Secret ([ADR-0021](../docs/adr/0021-decode-env-is-a-naming-suffix.md)).
`DECODE_ENV` names all three, and it is set at DEPLOY, never inside the Secret. Set it once — in `.env`
(sourced by every block below) or exported in this shell — and every command on this page, `create` /
`deploy` / `run` / `logs`, lands on the same deployment:

```bash
echo 'DECODE_ENV=prod' >> .env      # or, for this shell only:  export DECODE_ENV=prod
```

The blocks below never hardcode the suffix: the Secret name is built from `$DECODE_ENV`, so the Secret you
create and the app you deploy cannot disagree. The guard is _inside_ the name — `${DECODE_ENV:?…}` — which
makes the `create` itself refuse to run when the variable is empty or unset, instead of quietly writing a
Secret called `decode-headless-`. (A guard on its own line would not do that: pasted into an interactive
shell, a failed `:?` prints its message and the shell runs the next line anyway.) `local` is what decode
assumes when `DECODE_ENV` is genuinely unset — these blocks just make you say it out loud.

The container has no `.env`; **its Secret is its whole config surface**, so it must carry every key that run
needs. Two rules follow: pass every key on every create (a Secret is replaced, never patched — hence `--force`,
which also makes the block re-runnable), and **do not put `DECODE_ENV` in it** — the deployment already knows
which environment it is, and a Secret that disagrees is refused at startup with one line.

Pick the block for the provider you run. Each loads your `.env` into the shell, then copies that provider's
keys, plus two optional extras (drop either line if you don't use it — an empty value reads as unset, so the
create is still safe):

| Optional key                        | What it buys                                                                                                                                                                                                                                                                                                         |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SANDBOX_GIT_TOKEN`                 | only `--sandbox-mode modal` runs use it, to push a `decode/<session-id>` branch back. Without it the run still answers, it just ships no branch.                                                                                                                                                                     |
| `OPIK_API_KEY` (+ `OPIK_WORKSPACE`) | traces every remote run ([ADR-0014](../docs/adr/0014-opik-observability.md)). Presence-based: unset = a byte-identical no-op. **Do not set `OPIK_PROJECT_NAME`** — it derives to `decode-<env>`, which is what keeps a `local` deployment's traces out of `prod`'s. Self-hosted Opik also needs `OPIK_URL_OVERRIDE`. |

**Gemini** (`LLM_PROVIDER=gemini`, the default):

```bash
set -a && . ./.env && set +a

uv run modal secret create "decode-headless-${DECODE_ENV:?set it in .env or export it first}" \
  LLM_PROVIDER=gemini \
  GEMINI_API_KEY="$GEMINI_API_KEY" \
  GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}" \
  OPIK_API_KEY="$OPIK_API_KEY" \
  OPIK_WORKSPACE="${OPIK_WORKSPACE:-default}" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force

uv run modal secret list
```

**Modal — your own served model** (`LLM_PROVIDER=modal`, [02_modal_endpoints.md](02_modal_endpoints.md)). The
endpoint is a _separate_ deployed app; these four values point the harness at it, and the proxy pair is what
gets past its 🔑:

```bash
set -a && . ./.env && set +a

uv run modal secret create "decode-headless-${DECODE_ENV:?set it in .env or export it first}" \
  LLM_PROVIDER=modal \
  MODAL_ENDPOINT_URL="$MODAL_ENDPOINT_URL" \
  MODAL_ENDPOINT_MODEL="${MODAL_ENDPOINT_MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}" \
  MODAL_PROXY_TOKEN_ID="$MODAL_PROXY_TOKEN_ID" \
  MODAL_PROXY_TOKEN_SECRET="$MODAL_PROXY_TOKEN_SECRET" \
  OPIK_API_KEY="$OPIK_API_KEY" \
  OPIK_WORKSPACE="${OPIK_WORKSPACE:-default}" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force

uv run modal secret list
```

Served `--unauthenticated`? Drop the two `MODAL_PROXY_TOKEN_*` lines — omit both or set both.

**OpenRouter** (`LLM_PROVIDER=openrouter`):

```bash
set -a && . ./.env && set +a

uv run modal secret create "decode-headless-${DECODE_ENV:?set it in .env or export it first}" \
  LLM_PROVIDER=openrouter \
  OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  OPENROUTER_MODEL="${OPENROUTER_MODEL:-openrouter/free}" \
  OPIK_API_KEY="$OPIK_API_KEY" \
  OPIK_WORKSPACE="${OPIK_WORKSPACE:-default}" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force

uv run modal secret list
```

To change a value later, edit `.env` and re-run the same block — then **redeploy**. A `--force` create writes a
_new_ Secret object, and a running deployment keeps the one it was bound to until `decode remote deploy` runs
again.

Recording every remote run as a Kitaru Session is optional and orthogonal: add `KITARU_AGENT_ID` +
`KITARU_API_URL` to the Secret ([06 §3](06_evals_replays.md)). Without them the container imports no kitaru at
all, at any `DECODE_ENV`.

### 2c. Deploy

Every trigger runs against the **deployed** app. Deploy builds the image (`debian_slim` + `uv sync` + this repo's source baked in) — **re-run after any change to decode's source, and after any change to the Secret**. Must run from a checkout of this repo:

```bash
uv run decode remote deploy       # reads DECODE_ENV (§2b) → decode-headless-$DECODE_ENV
```

Want, at the end of the output:

```
Decode: deploying decode-headless-prod (DECODE_ENV=prod).
├── 🔨 Created function run_task.
├── 🔨 Created function nightly.
└── 🔨 Created web function webhook => https://<workspace>--decode-headless-prod-webhook.modal.run 🔑
✓ App deployed in 46.102s! 🎉
```

`DECODE_ENV` is read here, from this shell (or your `.env`), and **baked into the image** — it is what named the app and the Secret, so the three can never disagree. Prefer it inline (`DECODE_ENV=prod uv run decode remote deploy`) if you would rather not export it. Every `decode remote` command resolves the same name, so a shell without `DECODE_ENV=prod` reaches `decode-headless-local` and reports _that_ app as not deployed — which is the failure this naming makes legible instead of silent.

`run_task` = the run; `nightly` = the cron (inert until deployed with a schedule, §5); `webhook` = the POST endpoint (🔑 = proxy auth on, §4). Every trigger takes the same knobs — `task`, `repo`, `sandbox-mode` (`none` | `modal`), `model`, `max-requests`, `timeout-seconds` — and leaves the same traces: the answer in `decode remote logs`, and (`modal` mode + `SANDBOX_GIT_TOKEN`) a `decode/<session-id>` branch on origin.

---

## 3. Run a task from the CLI

### 3a. One run (`decode remote run`)

```bash
uv run decode remote run "run bash to print uname -a and pwd and report both" --sandbox-mode none
```

Answer streams to stdout; summary line on stderr. Not deployed yet: one line names `decode remote deploy`, nothing billed.

Want — a gVisor kernel and an in-container path:

```
- **`uname -a`**: `Linux modal 4.19.0-gvisor #1 SMP … x86_64 GNU/Linux`
- **`pwd`**: `/harness`
Decode: run finished — exit=0 sandbox=none session=346fbde2-… branch=None
```

A change to a real repo, shipped back as a branch:
(Change the `--repo` with something of yours or fork ours.)

```bash
uv run decode remote run "add a hello line to README and commit" \
  --repo https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course \
  --sandbox-mode modal \
  --max-requests 60
```

Then verify the output branches:

```bash
git ls-remote https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course 'refs/heads/decode/*'
```

| `--sandbox-mode` | Where `bash` runs                        | What `--repo` does                                                 | Hand-back                                                                |
| ---------------- | ---------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| `none` (default) | the gVisor container itself (`/harness`) | the harness clones it to `/scratch/repo` and launches decode there | none: the clone dies with the container                                  |
| `modal`          | a nested Modal Sandbox, `/workspace`     | passed through to `decode run --repo`                              | pushes `decode/<session-id>` when `SANDBOX_GIT_TOKEN` is in the Secret   |
| `docker`         | —                                        | —                                                                  | rejected client-side, one line, no container (no Docker daemon on Modal) |

`--max-requests N` → `decode run --max-requests N` in the container: past N requests, one `Decode: the run stopped at its request ceiling …` line, exit 1, Hand-back still runs. `--timeout-seconds` bounds the clock (default 1800).

### 3b. N attempts at one task, in parallel (`decode remote attempts`)

```bash
uv run decode remote attempts "add a hello line to README and commit" \
  --repo https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course --attempts 3 --sandbox-mode modal
```

Each attempt's task gets _"Commit your work when you are done. Do NOT push and do NOT open a pull request."_ appended, so the Hand-back is the only ship path and the N branches stay comparable. Want:

```
#    session                               branch            shipped?     exit
------------------------------------------------------------------------------
1    3f662b01-3ab6-49d0-b2b6-9ebc58acb14e  decode/3f662b01   shipped      0
2    676f965f-b240-4975-a2dd-61a0ebb7c83b  decode/676f965f   shipped      0
3    a82766aa-594b-4c32-b5ef-e9da8fd24096  decode/a82766aa   shipped      0
Compare them:
  git ls-remote https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course 'refs/heads/decode/*'
  git diff origin/decode/3f662b01..origin/decode/676f965f
```

Three attempts take about as long as one (59 s vs 72 s measured). Past `--attempts 1`, `--repo` is required. `--detach` prints the N call ids and exits in ~7 s:

```bash
uv run decode remote attempts "…" --repo <url> --attempts 2 --sandbox-mode modal --detach
# later:
uv run decode remote logs                      # = modal app logs decode-headless-<env>
git ls-remote <url> 'refs/heads/decode/*'
```

`shipped` = the branch reached origin. No `SANDBOX_GIT_TOKEN` (or one that can't push there) and `none` mode both read `NOT SHIPPED`, reason under the table; the run still answers and exits 0.

---

## 4. Run a task from a webhook

URL from the deploy output. Body = the `decode remote run` knobs as JSON; only `task` is required. The endpoint spawns the run and returns at once.

```bash
export WEBHOOK_URL=<your_webhook_url>
set -a && . ./.env && set +a         # MODAL_PROXY_TOKEN_ID / MODAL_PROXY_TOKEN_SECRET

curl -s -X POST "$WEBHOOK_URL" \
  -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET" \
  -H 'content-type: application/json' \
  -d '{"task": "add a hello line to README and commit", "repo": "https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course",
       "sandbox_mode": "modal", "max_requests": 60}'
```

Want:

```json
{
  "call_id": "fc-01M231EV2K80GW2V5XKY2GVXSS",
  "sandbox_mode": "modal",
  "repo": "https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course",
  "status": "spawned",
  "watch": [
    "modal app logs decode-headless-prod",
    "git ls-remote https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course 'refs/heads/decode/*'"
  ]
}
```

Then `uv run decode remote logs` for the answer, `git ls-remote` for the branch.

- **Proxy auth is the lock.** No valid `Modal-Key` / `Modal-Secret` pair → refused at Modal's edge, before the Function runs.
- **A bad request costs nothing.** `sandbox_mode: "docker"` or an empty `task` → `400`, nothing spawned.
- **The endpoint holds no Secret.** It spawns `run_task`; only the run's container gets `decode-headless-<env>`.
- **Callers:** a GitHub Actions step (`curl` with the two headers from repository secrets), a ticket bot, Zapier, a Slack slash command.

---

## 5. Run a task on a schedule (cron)

Schedule and job are read from your shell **at deploy** and ship with the deployment. No `DECODE_NIGHTLY_CRON` = no schedule.

```bash
DECODE_NIGHTLY_CRON="0 2 * * *" \
DECODE_NIGHTLY_TASK="Find every TODO comment, fix the ones under 20 lines, commit each fix separately." \
DECODE_NIGHTLY_REPO=https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course \
DECODE_NIGHTLY_SANDBOX_MODE=modal \
DECODE_NIGHTLY_MAX_REQUESTS=120 \
uv run decode remote deploy
```

Want:

```
Decode: nightly job registered — cron='0 2 * * *' (UTC) task='Find every TODO comment, …'
```

| Variable                                           | Meaning                                                                    |
| -------------------------------------------------- | -------------------------------------------------------------------------- |
| `DECODE_NIGHTLY_CRON`                              | crontab syntax, **UTC** — unset = no schedule                              |
| `DECODE_NIGHTLY_TASK`                              | the prompt; required once a cron is set (checked at deploy, on the laptop) |
| `DECODE_NIGHTLY_REPO` / `_SANDBOX_MODE` / `_MODEL` | = `decode remote run`'s `--repo` / `--sandbox-mode` / `--model`            |
| `DECODE_NIGHTLY_MAX_REQUESTS` / `_TIMEOUT_SECONDS` | token and clock ceilings                                                   |

Each morning: `uv run decode remote logs` for the answer, `git ls-remote <repo> 'refs/heads/decode/*'` for the branch. Modal's dashboard has a _run now_ button on any scheduled Function. To stop: redeploy without `DECODE_NIGHTLY_CRON` (Modal has no pause).

---

## 6. Watch, cost, redeploy

```bash
uv run decode remote logs           # = modal app logs decode-headless-<env>
uv run modal app list
uv run modal app stop "decode-headless-${DECODE_ENV:?}"
```

| Item                          | Cost                                       |
| ----------------------------- | ------------------------------------------ |
| Containers + nested sandboxes | per run-second, nothing idle               |
| Deployed app, nothing running | nothing                                    |
| Provider tokens               | `--max-requests` caps a run nobody watches |

**A code change needs a redeploy** — the source is baked into the image at `decode remote deploy`.

## 7. Troubleshooting

| Symptom                                                            | Fix                                                                                                                                                             |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Decode: the decode-headless-<env> app is not deployed …`          | `uv run decode remote deploy` at that `DECODE_ENV` (also after `modal app stop`). Nothing was billed.                                                           |
| `Decode: Modal credentials are missing or rejected …`              | `uv run modal token set …`. `.env` does nothing for these.                                                                                                      |
| `Decode: set GEMINI_API_KEY in your environment` in the run's log  | the Secret lacks the provider key — recreate it (§2b), then **redeploy**: a `--force` create writes a new Secret object the running deployment is not bound to. |
| `Decode: this deployment is DECODE_ENV=… but its Secret carries …` | remove `DECODE_ENV` from the Secret (§2b), or redeploy at the env the Secret names.                                                                             |
| Runs land in the wrong Opik project / sandbox app                  | `DECODE_ENV` at deploy names both — redeploy with the one you meant.                                                                                            |
| A remote run behaves like last week's code                         | re-run `decode remote deploy`.                                                                                                                                  |
| `sandbox mode 'docker' cannot run on Modal`                        | use `none` or `modal`.                                                                                                                                          |
| Attempts read `NOT SHIPPED`                                        | `none` mode discards its clone; `modal` mode needs a `SANDBOX_GIT_TOKEN` that can push to that repo.                                                            |
| Webhook returns `401` / `403`                                      | wrong or missing `Modal-Key` / `Modal-Secret` — [02_modal_endpoints.md](02_modal_endpoints.md#authentication--proxy-tokens).                                    |
| Nightly deploy dies on the laptop                                  | `DECODE_NIGHTLY_CRON` set without `DECODE_NIGHTLY_TASK`.                                                                                                        |

## Go further

- Headless inside a sandbox on your laptop: [03_sandboxing.md](03_sandboxing.md) (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`).
- Record every run and replay it later: [06_evals_replays.md](06_evals_replays.md) — recording is opt-in and orthogonal to this deployment. The Modal-hosted Kitaru Worker: [07_evals_replays_deploy.md](07_evals_replays_deploy.md).
- [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) — why this remote shape.
