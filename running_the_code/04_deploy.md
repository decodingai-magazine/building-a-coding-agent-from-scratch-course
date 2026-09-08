# Deploy — the headless harness on Modal

The REPL needs you at the keyboard. `decode run` doesn't: it runs one task to completion unattended
and prints the answer on stdout. Once a run needs no keyboard, it needs no laptop either — this page
puts that headless harness on **[Modal](https://modal.com?source=decodingai&campaign=harnesseng)** and
triggers it three ways: **from the CLI**, **from a webhook**, and **on a cron schedule**.

**Remote is a launch-vs-execute split** ([ADR-0020](../docs/adr/0020-remote-headless-on-modal.md)):
your laptop only *launches*; a Modal container *executes* `decode run` — the very same console script
your laptop runs, in a gVisor container, from an image built in code. No server, no Dockerfile, no
idle cost: a deployed app with nothing running bills nothing.

Budget: ~15 minutes, one provider key, a Modal account.

---

## 1. What you are deploying: `decode run`

Before it goes anywhere, the headless harness runs on your laptop:

```bash
uv run decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs inline with no approval prompt, and `ask_user` is a no-op.
  Unattended means unattended; a task that needs a human answer mid-run belongs in the REPL
  ([ADR-0019 §1](../docs/adr/0019-kitaru-replay-runtime.md)).
- **stdout is the answer, alone.** Notices go to stderr, the detail to `.decode/logs/decode.log`. So
  `decode run … | pbcopy` is safe.
- **Same everything else as the REPL** — the provider-key guard, `--model`, `--repo` with a sandbox
  mode ([03_sandboxing.md](03_sandboxing.md)), Hand-back on completion, Opik tracing.
- **`--max-requests N`** is the one knob the REPL lacks: past N model requests the run stops with one
  stderr line and exit 1. Nobody watches a background run, so this is the token bill's ceiling.
  `RUNTIME_MAX_REQUESTS` sets the default; unset = unbounded.

Everything below is this command, executed in a Modal container instead of your shell. The Modal
Headless App (`decode-headless`, `src/decode/remote/app.py`) is one Function, `run_task`, that runs
`decode run` as a subprocess — plus two thin callers of it, `nightly` (cron) and `webhook` (POST).

---

## 2. Set up

### 2a. Prerequisites

| Need | Why | Get it |
|---|---|---|
| The core setup | `uv`, the repo, `make install`, a provider key in `.env` | [01_install_and_usage.md](01_install_and_usage.md) |
| A Modal account + CLI tokens on this machine | `decode remote` talks to your Modal workspace | `uv run modal token set --token-id … --token-secret …` — printed by [modal.com](https://modal.com?source=decodingai&campaign=harnesseng) at signup ($30 free credits). Account tokens are not decode settings; `.env` does nothing for them. |
| A Modal **proxy token** pair | the webhook's auth (§4) | `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` in `.env` — minted in [02_modal_endpoints.md](02_modal_endpoints.md#authentication--proxy-tokens) |
| `SANDBOX_GIT_TOKEN` *(optional)* | lets `--sandbox-mode modal` runs push a branch back | a scoped, revocable PAT — [03_sandboxing.md](03_sandboxing.md#the-sandbox-git-token-sandbox_git_token) |

No extra install: `modal` ships with decode's dependencies, so `uv run modal …` already works.

### 2b. The `decode-headless` Secret

The container has no `.env`. It takes its **entire** environment from one Modal Secret,
`decode-headless`, which outranks `.env` in `Settings` precedence — so `DECODE_ENV` stays `local`
in-container and no Environment Bucket is involved ([ADR-0020 §4](../docs/adr/0020-remote-headless-on-modal.md)).
Create it once — key names below, values only ever from your shell, never committed:

```bash
set -a && . ./.env && set +a          # values come from your own .env, never echoed

uv run modal secret create decode-headless \
  GEMINI_API_KEY="$GEMINI_API_KEY" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN"

uv run modal secret list              # listed; values are write-only
```

| Key | Required | Why |
|---|---|---|
| `GEMINI_API_KEY` — or your provider's | ✅ | the run has to reach a model. Another provider = its own keys + `LLM_PROVIDER` (`OPENROUTER_API_KEY`; or `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` + the proxy token pair for a [02_modal_endpoints.md](02_modal_endpoints.md) endpoint) |
| `SANDBOX_GIT_TOKEN` | optional | only `--sandbox-mode modal` runs ship branches; without it a run still answers, the branch just stays in the container |
| `KITARU_API_URL` / `KITARU_API_KEY` / `KITARU_AGENT_ID` | optional | record every remote run as a Kitaru Session — the recording setup is [06_evals_replays.md §3](06_evals_replays.md#3-get-sessions-in--record-new-import-old). Absent, the Recording Seam degrades to the bare agent with one stderr line and the run still exits 0. |

Add `--force` to the same command to update a secret (it replaces the whole surface, so pass every
key again).

### 2c. Deploy

Every trigger runs against the **deployed** app — the laptop never builds an image or runs an
ephemeral app. So deploy once first. This is what builds the image (`debian_slim` + `uv sync` for the
locked deps, then this repo's source baked on top — [ADR-0020 §2](../docs/adr/0020-remote-headless-on-modal.md)),
and what you must **re-run after any change to decode's source**, since the source is baked in.
`decode remote deploy` wraps `modal deploy -m decode.remote.app` and must run from a checkout of this
repo:

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

`run_task` is the run; `nightly` is the cron (inert until you deploy with a schedule, §5); `webhook`
is the POST endpoint (🔑 = proxy auth on, §4). Every trigger takes the same knobs — `task`, `repo`,
`sandbox-mode` (`none` | `modal`), `model`, `max-requests`, `timeout-seconds` — and every run leaves
the same traces: the answer in `decode remote logs`, and (`modal` mode + `SANDBOX_GIT_TOKEN`) a
`decode/<session-id>` branch on origin.

---

## 3. Run a task from the CLI

### 3a. One run (`decode remote run`)

```bash
uv run decode remote run "run bash to print uname -a and pwd and report both" --sandbox-mode none
```

The answer streams to stdout as the container produces it; the summary line lands on stderr. Not
deployed yet? One friendly line names `decode remote deploy` and nothing is billed.

Want: a **gVisor Linux** kernel and an in-container path — the proof the run happened on Modal and
not on your laptop:

```
- **`uname -a`**: `Linux modal 4.19.0-gvisor #1 SMP … x86_64 GNU/Linux`
- **`pwd`**: `/harness`
Decode: run finished — exit=0 sandbox=none session=346fbde2-… branch=None
```

A second example — a real change to a real repo, shipped back as a branch:

```bash
uv run decode remote run "add a hello line to README and commit" \
  --repo https://github.com/you/your-repo.git --sandbox-mode modal --max-requests 60
git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'     # the branch is there
```

| `--sandbox-mode` | Where `bash` runs | What `--repo` does | Hand-back |
|---|---|---|---|
| `none` (default) | the gVisor container itself (`/harness`) | the **harness** clones it to `/scratch/repo` and launches decode there — decode never sees `--repo`, so its [ADR-0012 §3](../docs/adr/0012-isolated-workspace.md) guard stands | none: the clone dies with the container |
| `modal` | a nested Modal Sandbox, `/workspace`, spawned by the container's own ambient Modal identity | passed straight through to `decode run --repo` | yes — pushes `decode/<session-id>` when `SANDBOX_GIT_TOKEN` is in the Secret |
| `docker` | — | — | rejected client-side, ONE line, before any container starts (no Docker daemon on Modal) |

`--max-requests N` reaches `decode run --max-requests N` inside the container: past N model requests
the run stops with one `Decode: the run stopped at its request ceiling …` line and exit 1, Hand-back
included. `--timeout-seconds` bounds the clock (default 1800); this bounds the token bill.

### 3b. N attempts at one task, in parallel (`decode remote attempts`)

```bash
uv run decode remote attempts "add a hello line to README and commit" \
  --repo https://github.com/you/your-repo.git --attempts 3 --sandbox-mode modal
```

Every attempt's task gets the same paragraph appended — *"Commit your work when you are done. Do NOT
push and do NOT open a pull request."* — so the Hand-back is the only ship path and the N branches
stay comparable. Want:

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

Three attempts take about as long as one (59 s vs 72 s measured) — N cold containers sharing one
pre-built image. Past `--attempts 1`, `--repo` is required (attempts are compared as the branches
they ship). Add `--detach` to print the N function-call ids and exit in ~7 s, laptop closed:

```bash
uv run decode remote attempts "…" --repo <url> --attempts 2 --sandbox-mode modal --detach
# come back later:
uv run decode remote logs                      # = modal app logs decode-headless
git ls-remote <url> 'refs/heads/decode/*'
```

A row reads `shipped` only when the branch actually **reached origin**. No `SANDBOX_GIT_TOKEN` (or one
that can't write there) and `none` mode's discarded clone both read `NOT SHIPPED`, with the reason
under the table; the run still answers and exits 0 — the Hand-back fails soft
([ADR-0016 §4](../docs/adr/0016-drop-credential-proxy.md)).

---

## 4. Run a task from a webhook

The deploy output names the URL (`https://<workspace>--decode-headless-webhook.modal.run`). The body
takes the same knobs as `decode remote run`, as JSON — only `task` is required. The endpoint
`spawn`s the run and answers at once: the run takes minutes, the caller waits seconds.

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
 "status": "spawned", "watch": ["modal app logs decode-headless",
 "uv run kitaru session list --agent decode --origin recorded",
 "git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'"]}
```

Then `uv run decode remote logs` for the answer, `git ls-remote` for the branch.

- **Proxy auth is the lock.** A request without a valid `Modal-Key` / `Modal-Secret` pair is refused
  at Modal's edge, before the Function runs — the same proxy token pair
  [02_modal_endpoints.md](02_modal_endpoints.md) mints for the open-model endpoints. Without it the
  URL would be a public "spend my tokens" button.
- **A bad run costs nothing.** `sandbox_mode: "docker"` or an empty `task` is a `400` carrying the
  same one-line message every other surface prints; nothing is spawned.
- **The endpoint holds no Secret.** It spawns `run_task` on the deployed app — the run's container
  gets `decode-headless`, the endpoint's does not.
- **Hook it to anything that can POST**: a GitHub Actions step (`curl` with the two headers from
  repository secrets), a ticket bot, Zapier, a Slack slash command.

---

## 5. Run a task on a schedule (cron)

The schedule and the job are **deploy-time** configuration, read from your shell by
`decode remote deploy` and shipped with the deployment. No `DECODE_NIGHTLY_CRON` exported = no
schedule registered, and a plain deploy is exactly what it was before:

```bash
DECODE_NIGHTLY_CRON="0 2 * * *" \
DECODE_NIGHTLY_TASK="Find every TODO comment, fix the ones under 20 lines, commit each fix separately." \
DECODE_NIGHTLY_REPO=https://github.com/you/your-repo.git \
DECODE_NIGHTLY_SANDBOX_MODE=modal \
DECODE_NIGHTLY_MAX_REQUESTS=120 \
uv run decode remote deploy
```

Want, right after the deploy:

```
Decode: nightly job registered — cron='0 2 * * *' (UTC) task='Find every TODO comment, …'
```

| Variable | Meaning |
|---|---|
| `DECODE_NIGHTLY_CRON` | crontab syntax, **UTC** — the switch: unset it and no schedule exists |
| `DECODE_NIGHTLY_TASK` | the prompt; required once a cron is set (a cron with no task dies on the laptop, not at 2am) |
| `DECODE_NIGHTLY_REPO` / `_SANDBOX_MODE` / `_MODEL` | the same knobs as `decode remote run`'s `--repo` / `--sandbox-mode` / `--model` |
| `DECODE_NIGHTLY_MAX_REQUESTS` / `_TIMEOUT_SECONDS` | the run's two ceilings — tokens and clock |

The job travels as a `modal.Secret.from_dict` env on the `nightly` Function (a Secret is simply how a
deploy-time env reaches a container; nothing in it is a credential). Each morning:
`uv run decode remote logs` for the answer, `git ls-remote <repo> 'refs/heads/decode/*'` for the
branch. Modal's dashboard has a *run now* button on any scheduled Function if you don't want to wait
for 2am. To stop the schedule, redeploy without `DECODE_NIGHTLY_CRON` — Modal has no pause.

Both laptop-free triggers are thin callers of the same `run_task`, so a scheduled or POSTed run is
byte-for-byte a `decode remote run` nobody had to type
([ADR-0020 Amendment §8](../docs/adr/0020-remote-headless-on-modal.md)).

---

## 6. Watch, cost, redeploy

```bash
uv run decode remote logs           # = modal app logs decode-headless — every run's answer + notices
uv run modal app list               # decode-headless deployed (plus stopped ephemeral apps — free)
uv run modal app stop decode-headless
```

| Item | Cost |
|---|---|
| Headless containers + nested sandboxes | usage-based only — per run-second, nothing idle |
| The deployed app, no Function running | nothing — which is why it is left deployed |
| Provider tokens | the only standing cost; `--max-requests` caps a run nobody watches |

**A code change needs a redeploy.** The source is baked into the image at `decode remote deploy`
time; until you re-run it, every trigger runs last deploy's decode.

## 7. Troubleshooting

| Symptom | What it means |
|---|---|
| `Decode: the decode-headless app is not deployed …` | `decode remote run` / `attempts` before the first `decode remote deploy` (or after `modal app stop`). Deploy; nothing was billed. |
| `Decode: Modal credentials are missing or rejected …` | no account tokens on this machine: `uv run modal token set …`. `.env` does nothing for these. |
| `Decode: set GEMINI_API_KEY in your environment` in the run's log | the `decode-headless` Secret lacks the active provider's key — recreate it with `--force`. |
| A remote run behaves like last week's code | the source is baked into the image at deploy — re-run `decode remote deploy` after any decode change. |
| `sandbox mode 'docker' cannot run on Modal` | expected: a Modal container has no Docker daemon. Use `none` or `modal`. |
| Attempts read `NOT SHIPPED` | `none` mode discards its clone; `modal` mode needs a `SANDBOX_GIT_TOKEN` in the Secret that can push to that repo. |
| `[kitaru] not recording this run: …` | recording is on but the Kitaru keys in the Secret are missing or stale; the run itself is fine. Fix per [06_evals_replays.md §3](06_evals_replays.md#3-get-sessions-in--record-new-import-old). |
| The webhook returns `401` / `403` | wrong or missing `Modal-Key` / `Modal-Secret` — re-mint the proxy token pair in [02_modal_endpoints.md](02_modal_endpoints.md#authentication--proxy-tokens). |
| The nightly deploy dies on the laptop | `DECODE_NIGHTLY_CRON` set without `DECODE_NIGHTLY_TASK` — by design, so it fails where you can see it. |

## Go further

- Run headless **inside a sandbox** on your laptop first: [03_sandboxing.md](03_sandboxing.md)
  (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`), including the sandbox git token the
  `decode-headless` Secret carries.
- **Record** every run — laptop or Modal — as a Kitaru Session, then replay it:
  [06_evals_replays.md](06_evals_replays.md). Move the replay Worker to Modal too:
  [07_evals_replays_deploy.md](07_evals_replays_deploy.md).
- Hydrate a laptop run's secrets from an Environment Bucket instead of `.env`:
  [01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional).
- [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md) (headless) and
  [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) (why this remote shape, and what it replaced).
