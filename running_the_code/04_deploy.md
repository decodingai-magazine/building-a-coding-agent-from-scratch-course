# Deploy — the headless harness on Modal

Run `decode run` on [Modal](https://modal.com?source=decodingai&campaign=harnesseng) instead of your laptop, triggered from the CLI, a webhook, or a cron schedule. The laptop only launches; a Modal container executes the same `decode` console script ([ADR-0020](../docs/adr/0020-remote-headless-on-modal.md)). No server; a deployed app with nothing running costs nothing.

---

## 1. What you are deploying: `decode run`

```bash
uv run decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs without approval prompts; `ask_user` is a no-op. A task that needs a human answer mid-run belongs in the REPL.
- **stdout is the answer, alone.** Notices go to stderr, detail to `.decode/logs/decode.log`. `decode run … | pbcopy` is safe.
- **Same knobs as the REPL** — provider-key guard, `--model`, `--repo` + a sandbox mode ([03_sandboxing.md](03_sandboxing.md)), Hand-back on completion, Opik tracing.
- **`--max-requests N`** — stop after N model requests (stderr line, exit 1). `RUNTIME_MAX_REQUESTS` sets the default; unset = unbounded.

The Modal Headless App (`decode-headless`, `src/decode/remote/app.py`) is one Function, `run_task`, that runs this command as a subprocess, plus two callers of it: `nightly` (cron) and `webhook` (POST).

---

## 2. Set up

### 2a. Prerequisites

| Need | Why | Get it |
|---|---|---|
| Core setup | `uv`, the repo, `make install`, a provider key in `.env` | [01_install_and_usage.md](01_install_and_usage.md) |
| Modal account + CLI tokens | `decode remote` talks to your Modal workspace | `uv run modal token set --token-id … --token-secret …` — from [modal.com](https://modal.com?source=decodingai&campaign=harnesseng) at signup ($30 free credits). Not decode settings; `.env` does nothing for them. |
| Modal **proxy token** pair | webhook auth (§4) | `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` in `.env` — [02_modal_endpoints.md](02_modal_endpoints.md#authentication--proxy-tokens) |
| `SANDBOX_GIT_TOKEN` *(optional)* | lets `--sandbox-mode modal` runs push a branch back | scoped, revocable PAT — [03_sandboxing.md](03_sandboxing.md#the-sandbox-git-token-sandbox_git_token) |

No extra install: `modal` ships with decode's dependencies.

### 2b. The `decode-headless` Secret

The container has no `.env`; its whole environment is one Modal Secret, `decode-headless`. Create once — values from your shell, never committed:

```bash
set -a && . ./.env && set +a

uv run modal secret create decode-headless \
  GEMINI_API_KEY="$GEMINI_API_KEY" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN"

uv run modal secret list              # values are write-only
```

| Key | Why |
|---|---|
| `GEMINI_API_KEY` — or your provider's | Another provider = its keys + `LLM_PROVIDER` (`OPENROUTER_API_KEY`; or `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` + the proxy token pair, [02_modal_endpoints.md](02_modal_endpoints.md)) |
| `SANDBOX_GIT_TOKEN` *(optional)* | only `--sandbox-mode modal` runs ship branches; without it the run still answers |

Update with `--force` on the same command (replaces the whole Secret — pass every key again). `DECODE_ENV` unset = `local`: the Secret is the whole config surface. Running the deployment at `DECODE_ENV=prod` comes later, in [07 §2c](07_evals_replays_deploy.md#2c-decode_envprod-for-both-modal-apps).

### 2c. Deploy

Every trigger runs against the **deployed** app. Deploy builds the image (`debian_slim` + `uv sync` + this repo's source baked in) — **re-run after any change to decode's source**. Must run from a checkout of this repo:

```bash
uv run decode remote deploy
```

Want, at the end of the output:

```
├── 🔨 Created function run_task.
├── 🔨 Created function nightly.
└── 🔨 Created web function webhook => https://<workspace>--decode-headless-webhook.modal.run 🔑
✓ App deployed in 46.102s! 🎉
```

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

```bash
uv run decode remote run "add a hello line to README and commit" \
  --repo https://github.com/you/your-repo.git --sandbox-mode modal --max-requests 60
git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'
```

| `--sandbox-mode` | Where `bash` runs | What `--repo` does | Hand-back |
|---|---|---|---|
| `none` (default) | the gVisor container itself (`/harness`) | the harness clones it to `/scratch/repo` and launches decode there | none: the clone dies with the container |
| `modal` | a nested Modal Sandbox, `/workspace` | passed through to `decode run --repo` | pushes `decode/<session-id>` when `SANDBOX_GIT_TOKEN` is in the Secret |
| `docker` | — | — | rejected client-side, one line, no container (no Docker daemon on Modal) |

`--max-requests N` → `decode run --max-requests N` in the container: past N requests, one `Decode: the run stopped at its request ceiling …` line, exit 1, Hand-back still runs. `--timeout-seconds` bounds the clock (default 1800).

### 3b. N attempts at one task, in parallel (`decode remote attempts`)

```bash
uv run decode remote attempts "add a hello line to README and commit" \
  --repo https://github.com/you/your-repo.git --attempts 3 --sandbox-mode modal
```

Each attempt's task gets *"Commit your work when you are done. Do NOT push and do NOT open a pull request."* appended, so the Hand-back is the only ship path and the N branches stay comparable. Want:

```
#    session                               branch            shipped?     exit
------------------------------------------------------------------------------
1    3f662b01-3ab6-49d0-b2b6-9ebc58acb14e  decode/3f662b01   shipped      0
2    676f965f-b240-4975-a2dd-61a0ebb7c83b  decode/676f965f   shipped      0
3    a82766aa-594b-4c32-b5ef-e9da8fd24096  decode/a82766aa   shipped      0
Compare them:
  git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'
  git diff origin/decode/3f662b01..origin/decode/676f965f
```

Three attempts take about as long as one (59 s vs 72 s measured). Past `--attempts 1`, `--repo` is required. `--detach` prints the N call ids and exits in ~7 s:

```bash
uv run decode remote attempts "…" --repo <url> --attempts 2 --sandbox-mode modal --detach
# later:
uv run decode remote logs                      # = modal app logs decode-headless
git ls-remote <url> 'refs/heads/decode/*'
```

`shipped` = the branch reached origin. No `SANDBOX_GIT_TOKEN` (or one that can't push there) and `none` mode both read `NOT SHIPPED`, reason under the table; the run still answers and exits 0.

---

## 4. Run a task from a webhook

URL from the deploy output. Body = the `decode remote run` knobs as JSON; only `task` is required. The endpoint spawns the run and returns at once.

```bash
export WEBHOOK_URL=https://<workspace>--decode-headless-webhook.modal.run
set -a && . ./.env && set +a         # MODAL_PROXY_TOKEN_ID / MODAL_PROXY_TOKEN_SECRET

curl -s -X POST "$WEBHOOK_URL" \
  -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET" \
  -H 'content-type: application/json' \
  -d '{"task": "add a hello line to README and commit", "repo": "https://github.com/you/your-repo.git",
       "sandbox_mode": "modal", "max_requests": 60}'
```

Want:

```json
{"call_id": "fc-01ABC…", "sandbox_mode": "modal", "repo": "https://github.com/you/your-repo.git",
 "status": "spawned", "watch": ["modal app logs decode-headless", …,
 "git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'"]}
```

Then `uv run decode remote logs` for the answer, `git ls-remote` for the branch.

- **Proxy auth is the lock.** No valid `Modal-Key` / `Modal-Secret` pair → refused at Modal's edge, before the Function runs.
- **A bad request costs nothing.** `sandbox_mode: "docker"` or an empty `task` → `400`, nothing spawned.
- **The endpoint holds no Secret.** It spawns `run_task`; only the run's container gets `decode-headless`.
- **Callers:** a GitHub Actions step (`curl` with the two headers from repository secrets), a ticket bot, Zapier, a Slack slash command.

---

## 5. Run a task on a schedule (cron)

Schedule and job are read from your shell **at deploy** and ship with the deployment. No `DECODE_NIGHTLY_CRON` = no schedule.

```bash
DECODE_NIGHTLY_CRON="0 2 * * *" \
DECODE_NIGHTLY_TASK="Find every TODO comment, fix the ones under 20 lines, commit each fix separately." \
DECODE_NIGHTLY_REPO=https://github.com/you/your-repo.git \
DECODE_NIGHTLY_SANDBOX_MODE=modal \
DECODE_NIGHTLY_MAX_REQUESTS=120 \
uv run decode remote deploy
```

Want:

```
Decode: nightly job registered — cron='0 2 * * *' (UTC) task='Find every TODO comment, …'
```

| Variable | Meaning |
|---|---|
| `DECODE_NIGHTLY_CRON` | crontab syntax, **UTC** — unset = no schedule |
| `DECODE_NIGHTLY_TASK` | the prompt; required once a cron is set (checked at deploy, on the laptop) |
| `DECODE_NIGHTLY_REPO` / `_SANDBOX_MODE` / `_MODEL` | = `decode remote run`'s `--repo` / `--sandbox-mode` / `--model` |
| `DECODE_NIGHTLY_MAX_REQUESTS` / `_TIMEOUT_SECONDS` | token and clock ceilings |

Each morning: `uv run decode remote logs` for the answer, `git ls-remote <repo> 'refs/heads/decode/*'` for the branch. Modal's dashboard has a *run now* button on any scheduled Function. To stop: redeploy without `DECODE_NIGHTLY_CRON` (Modal has no pause).

---

## 6. Watch, cost, redeploy

```bash
uv run decode remote logs           # = modal app logs decode-headless
uv run modal app list
uv run modal app stop decode-headless
```

| Item | Cost |
|---|---|
| Containers + nested sandboxes | per run-second, nothing idle |
| Deployed app, nothing running | nothing |
| Provider tokens | `--max-requests` caps a run nobody watches |

**A code change needs a redeploy** — the source is baked into the image at `decode remote deploy`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `Decode: the decode-headless app is not deployed …` | `uv run decode remote deploy` (also after `modal app stop`). Nothing was billed. |
| `Decode: Modal credentials are missing or rejected …` | `uv run modal token set …`. `.env` does nothing for these. |
| `Decode: set GEMINI_API_KEY in your environment` in the run's log | the Secret lacks the provider key — recreate with `--force`. |
| A remote run behaves like last week's code | re-run `decode remote deploy`. |
| `sandbox mode 'docker' cannot run on Modal` | use `none` or `modal`. |
| Attempts read `NOT SHIPPED` | `none` mode discards its clone; `modal` mode needs a `SANDBOX_GIT_TOKEN` that can push to that repo. |
| Webhook returns `401` / `403` | wrong or missing `Modal-Key` / `Modal-Secret` — [02_modal_endpoints.md](02_modal_endpoints.md#authentication--proxy-tokens). |
| Nightly deploy dies on the laptop | `DECODE_NIGHTLY_CRON` set without `DECODE_NIGHTLY_TASK`. |

## Go further

- Headless inside a sandbox on your laptop: [03_sandboxing.md](03_sandboxing.md) (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`).
- Record every run and replay it later: [06_evals_replays.md](06_evals_replays.md); run this deployment at `DECODE_ENV=prod`: [07_evals_replays_deploy.md](07_evals_replays_deploy.md).
- [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) — why this remote shape.
