# Deploy — headless `decode run`, on your laptop and on Modal

The REPL needs you at the keyboard. `decode run` doesn't: it runs one task to completion unattended and prints the answer on stdout (pipe-clean). Same agent, different driver — a plain `asyncio.run` around the very `build_agent()` the REPL uses, with no durability layer at all: a crash is a re-run ([ADR-0019 §1](../docs/adr/0019-kitaru-replay-runtime.md)).

Once a run needs no keyboard, it needs no laptop either. **Remote is a launch-vs-execute split** ([ADR-0020](../docs/adr/0020-remote-headless-on-modal.md)): your laptop only launches, **[Modal](https://modal.com?source=decodingai&campaign=harnesseng)** executes — `decode remote …` plus one operator script, zero servers, zero idle cost — and the **managed [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) workspace** is the record/replay control plane. What you keep from every run is the **record**: with one opt-in, every run (REPL turns included) is filed on the workspace as a **Kitaru Session** — every LLM and tool call, node by node — and a recorded Session is the thing a **Replay** re-executes later, on a Worker, with or without a change.

This page is the runbook for all of it, laptop to cloud: `decode run` (§1), recording (§2), the remote pieces (§3), their two secrets (§4), the headless app and its four triggers — by hand, N attempts, cron, webhook (§5) — and replays on a Worker, yours or Modal-hosted (§6).

**Prerequisites:** the core setup from [01_install_and_usage.md](01_install_and_usage.md). §1 needs nothing else — recording is off by default and costs nothing when it is. §3 onward adds a Modal account on this machine (`uv run modal token set …` — account tokens are not decode settings) and, for recording, a `KITARU_AGENT_ID` (§2).

---

## 1. Run a task (`decode run`)

```bash
decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs inline with no approval prompt, and `ask_user` is a no-op. There is no pause and no wait — unattended means unattended; a task that needs a human answer mid-run belongs in the REPL ([ADR-0019 §1](../docs/adr/0019-kitaru-replay-runtime.md)).
- **stdout is the answer, alone.** Notices (a hand-back line, a recording warning) go to stderr; the detail is in `.decode/logs/decode.log`. So `decode run … | pbcopy` is safe.
- **Same everything else as the REPL** — the provider-key guard, `--model` (Model Override), `--repo`/`--local` with a sandbox mode ([03_sandboxing.md](03_sandboxing.md)), Hand-back on completion, and Opik tracing. `RUNTIME_ENABLED=false` disables the subcommand with one friendly line.
- **`--max-requests N` is the one knob the REPL does not have.** Nobody watches a background run, so its only stop condition would be the model's own. Past N model requests the run stops with one stderr line (`Decode: the run stopped at its request ceiling …`) and exit 1; the Hand-back still ships whatever the Workspace holds. `RUNTIME_MAX_REQUESTS` sets the default for every run; unset = unbounded, exactly like the REPL.
- **`TASK` is optional** — because a Kitaru Worker passes the prompt in the environment, not on the command line (§6). With no task anywhere you get one line naming both ways to supply one.

## 2. Record runs as Kitaru Sessions (opt-in)

Recording is presence-based and lives in exactly one function, the **Recording Seam** (`src/decode/runtime/recording.py`). Two variables switch it on:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io   # adapter-owned (or just `kitaru login`)
export KITARU_AGENT_ID=<uuid of the workspace's `decode` agent>     # decode's ONE recording knob
uv run decode run "explain what this repo does"
uv run kitaru session get <SESSION_ID>          # the run, node by node
```

- **Both surfaces record.** The REPL wraps the same way, with `session_name` = the decode session id, so a conversation's turns group together on the workspace.
- **`KITARU_API_URL` must be *exported*, not merely written in `.env`.** decode never reads it: the adapter's own client resolves the connection (env, else your `kitaru login` store). `set -a && . .env && set +a` is the shortcut.
- **Off is byte-identical.** With `KITARU_AGENT_ID` empty, no kitaru module is even imported.
- **Unreachable workspace degrades, it never blocks.** A user-launched run drops to the bare agent, prints ONE stderr line (`[kitaru] not recording this run: … continuing on the bare agent`), and still exits 0 — recording is an observer, never an availability dependency. A run spawned by a Kitaru **Worker** hard-fails instead: an unrecorded replay is a lying experiment.
- **A Worker's hard-fail is ONE line too, wherever it happens.** The workspace can also refuse the Kitaru **Session** the adapter creates lazily *inside* the run (a 403 on the agents route, a 422 for an unknown task, a typo'd `KITARU_TASK_ID`); `decode run` turns that into the same `Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: …` line and a non-zero exit, with the traceback in `.decode/logs/decode.log` only. A 403 adds the `KITARU_AGENT_ID` diagnosis ([06_evals_replays.md §7](06_evals_replays.md#7-field-notes--the-pitfalls-we-actually-hit)). A failure the **agent** raised (a provider 503) is never reworded as a recording failure — the Worker log stays honest about which half broke.

---

## 3. The remote pieces

| Piece | What | Launch it with |
|---|---|---|
| **Managed [Kitaru](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand) workspace** | `https://f5ee9622-kitaru.cloudinfra.zenml.io` — recorded **Kitaru Sessions**, **Cohorts**, **Replays**, registered **Agent Versions**, the **Environment Bucket** secret. Someone else's uptime; it executes nothing. | `uv run kitaru status` · [§2](#2-record-runs-as-kitaru-sessions-opt-in), [01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional) |
| **Modal Headless App** (`decode-headless`) | `decode.remote.app` — a Function that runs `decode run` as a **subprocess of the same console script your laptop runs**, in a gVisor container. Launched from the `decode` CLI itself: `decode remote deploy` once, then `decode remote run` (one synchronous run), `decode remote attempts` (N fire-and-forget attempts at one task → N comparable `decode/<session-id>` branches), a **nightly cron**, a **webhook**. Sandbox `none` / `modal` only; `docker` is rejected client-side (no Docker daemon on Modal). | [§5](#5-run-the-headless-app-on-modal) |
| **Modal-hosted Kitaru Worker** (`decode-kitaru-worker`) | `scripts/modal_kitaru_worker.py` — a long-running Function running `kitaru worker start`, so **replays execute off your laptop**. Claims `agent` + `evaluator` work and spawns **agent version 3**. Dies at Modal's 24 h function ceiling; you re-launch it with one command. | `uv run modal deploy scripts/modal_kitaru_worker.py` + `uv run modal run --detach …` — [§6b](#6b-on-modal-agent-version-3) |
| **Kitaru Worker on your laptop** | the other, unchanged option: `kitaru worker start` in *your* shell, spawning **agent version 2** (`SANDBOX_MODE=docker`, repo clone). The two Workers coexist — scope their claims so they don't race. | [§6a](#6a-on-your-laptop-agent-version-2) |
| **Agent Version** | the immutable run spec a Worker spawns. **v2** = laptop/docker; **v3** = `SANDBOX_MODE=none` with the in-image paths `/.uv/.venv/bin/decode` + `/harness` (the container *is* the isolation). Both registered by `scripts/register_kitaru_agent.py`. | [§6](#6-replay-a-recorded-session-on-a-kitaru-worker) |
| **[Modal](https://modal.com?source=decodingai&campaign=harnesseng)** | three things: the remote **Sandbox** (`SANDBOX_MODE=modal`), open-model **serving**, and **hosting the harness itself**. Not as a server: as containers you fire and forget. | [03_sandboxing.md](03_sandboxing.md), [02_modal_endpoints.md](02_modal_endpoints.md) |
| **Opik** | tracing + evals, untouched by any of this ([ADR-0014](../docs/adr/0014-opik-observability.md)). | [05_evals.md](05_evals.md) |

**Both Modal apps build their image in code** ([ADR-0020 §2](../docs/adr/0020-remote-headless-on-modal.md)) —
`decode.remote.image`, shared verbatim by both: `debian_slim` + `uv_sync` for the locked deps, then
this repo's source baked on top. No Dockerfile, no registry, no build cache to fight. The price of
baking source at build time: **a code change needs a re-`decode remote deploy` before the next run**.

Nothing here is required to use decode. Recording is opt-in, remote execution is opt-in, and with
`KITARU_AGENT_ID` unset and no Modal secret the rest of this page is irrelevant.

---

## 4. Secrets — two, deliberately asymmetric

Both apps take their entire environment from a **Modal Secret** (`modal.Secret.from_name`). Secret env
outranks `.env` in `Settings` precedence, so `DECODE_ENV` stays `local` in-container and no Environment
Bucket is involved ([ADR-0020 §4](../docs/adr/0020-remote-headless-on-modal.md)). Create them once —
**key names below, values only ever from your shell, never committed**:

```bash
set -a && . ./.env && set +a          # values come from your own .env, never echoed

uv run modal secret create decode-headless \
  GEMINI_API_KEY="$GEMINI_API_KEY" \
  KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KITARU_API_KEY" \
  KITARU_AGENT_ID="$KITARU_AGENT_ID" \
  SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN"

uv run modal secret create decode-kitaru-worker \
  KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KITARU_API_KEY" \
  GEMINI_API_KEY="$GEMINI_API_KEY"          # deliberately NO KITARU_AGENT_ID

uv run modal secret list                    # both listed, values are write-only
```

Add `--force` to the same command to update a secret (it replaces the whole surface, so pass every key
again).

| Key | `decode-headless` | `decode-kitaru-worker` | Why |
|---|---|---|---|
| `GEMINI_API_KEY` (or your provider's) | ✅ | ✅ | the run has to reach a model; a replay does too |
| `KITARU_API_URL` | ✅ | ✅ | which workspace to record to / claim from |
| `KITARU_API_KEY` | ✅ | ✅ | a container has no `kitaru login` store — see below |
| `KITARU_AGENT_ID` | ✅ | ❌ **never** | the 403 trap, below |
| `SANDBOX_GIT_TOKEN` | optional | — | only the harness ships branches; scoped, revocable PAT ([03_sandboxing.md](03_sandboxing.md#the-sandbox-git-token-sandbox_git_token), [ADR-0016](../docs/adr/0016-drop-credential-proxy.md)) |

**The `KITARU_AGENT_ID` asymmetry is the whole point, not an oversight.** A Kitaru Worker injects a
*task-scoped* token into every run it spawns. With an agent id also in the env, decode's Recording Seam
probes an agents route that token cannot use and the replay hard-fails with
`403: Task credentials are not accepted on this route` — the pitfall in
[06_evals_replays.md §7.3](06_evals_replays.md#7-field-notes--the-pitfalls-we-actually-hit). The worker
Function scrubs the variable defensively at startup with one logged line, but the secret's composition
is the rule and the scrub is only the backstop.

### The container credential (`KITARU_API_KEY` must be a `ZENPROKEY_…`)

A container cannot run `kitaru login`, so the key travels in the secret. On a **managed** workspace it
must be a **control plane** key (`ZENPROKEY_…`): a workspace-local key is rejected server-side with
`Local API keys are rejected under control plane authentication.` The kitaru client exchanges a control
plane key for a session token and keeps renewing it, so a worker stays authenticated for its whole life.

> **Not minted yet — [`tasks/153`](../tasks/153-mint-control-plane-key-close-pending-gate.md).** Until it
> is, the headless app still runs and answers, degrading with ONE `[kitaru] not recording this run: … 401:
> Missing bearer credential` line and exit 0 (the Recording Seam behaving exactly as
> [ADR-0019 §3](../docs/adr/0019-kitaru-replay-runtime.md) prescribes), and the Modal Worker refuses to
> start with one friendly line naming the missing variable. **The same key closes both.**

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

Then load it and re-verify:

```bash
uv run modal secret create decode-kitaru-worker \
  KITARU_API_URL="$KITARU_API_URL" GEMINI_API_KEY="$GEMINI_API_KEY" \
  KITARU_API_KEY="<ZENPROKEY_…>" --force        # still no KITARU_AGENT_ID
uv run decode remote run "say hello" --sandbox-mode none
uv run kitaru session list --agent decode --origin recorded --size 3      # the run is listed
```

---

## 5. Run the headless app on Modal

Four triggers, one Function, all of them against the **deployed** app — the laptop never builds an
image or runs an ephemeral app. So deploy once first (this is also what builds the shared image — and
what you must re-run after any change to decode's source, since the source is baked in). `decode
remote deploy` wraps `modal deploy -m decode.remote.app` and must run from a checkout of this repo:

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

`run_task` is the run; `nightly` is the cron (inert until you deploy with a schedule, §5c); `webhook`
is the POST endpoint (🔑 = proxy auth on, §5d). Every trigger takes the same knobs — `task`, `repo`,
`sandbox-mode` (`none` | `modal`), `model`, `max-requests`, `timeout-seconds` — and every run leaves the
same three traces: the answer in `decode remote logs`, a `decode/<session-id>` branch on
origin (`modal` mode + `SANDBOX_GIT_TOKEN`), and a recorded Kitaru Session (`kitaru session list --agent
decode --origin recorded`).

### 5a. One synchronous run (`decode remote run`)

```bash
uv run decode remote run "run bash to print uname -a and pwd and report both" --sandbox-mode none
```

The answer streams to stdout as the container produces it; the summary line lands on stderr. Not
deployed yet? One friendly line names `decode remote deploy` and nothing is billed.

Want: a **gVisor Linux** kernel and an in-container path — that is the proof the run happened on Modal
and not on your laptop:

```
- **`uname -a`**: `Linux modal 4.19.0-gvisor #1 SMP … x86_64 GNU/Linux`
- **`pwd`**: `/harness`
Decode: run finished — exit=0 sandbox=none session=346fbde2-… branch=None
```

Swap to `--sandbox-mode modal` and the same task reports `/workspace` instead — the bash landed in a
**nested** Modal Sandbox, spawned by the container's own ambient Modal identity.

Add `--max-requests N` to any surface on this page and it reaches `decode run --max-requests N`
inside the container: past N model requests the run stops with one `Decode: the run stopped at its
request ceiling …` line and exit 1, Hand-back included. `--timeout-seconds` bounds the clock; this
bounds the token bill — the number a run nobody is watching actually runs up (§1).

| `--sandbox-mode` | Where `bash` runs | What `--repo` does | Hand-back |
|---|---|---|---|
| `none` (default) | the gVisor container itself (`/harness`) | the **harness** clones it to `/scratch/repo` and launches decode there — decode never sees `--repo`, so its [ADR-0012 §3](../docs/adr/0012-isolated-workspace.md) guard stands | none: the clone dies with the container |
| `modal` | a nested Modal Sandbox, `/workspace` | passed straight through to `decode run --repo` | yes — pushes `decode/<session-id>` when `SANDBOX_GIT_TOKEN` is in the secret |
| `docker` | — | — | rejected client-side, ONE line, before any container starts |

The docker rejection costs nothing:

```bash
$ uv run decode remote run "print uname" --sandbox-mode docker ; echo EXIT=$?
Error: Decode: sandbox mode 'docker' cannot run on Modal — a Modal container has no Docker daemon. Use
--sandbox-mode none … or --sandbox-mode modal …
EXIT=1
```

### 5b. N attempts at one task, in parallel (`decode remote attempts`)

The attempts spawn against the **deployed** Function, never an ephemeral one — that is what makes
`--detach` real:

```bash
uv run decode remote attempts "add a hello line to README and commit" \
  --repo https://github.com/you/your-repo.git --attempts 3 --sandbox-mode modal
```

Every attempt's task gets the same paragraph appended — *"Commit your work when you are done. Do NOT
push and do NOT open a pull request."* — so the Hand-back is the only ship path and the N branches stay
comparable. Want:

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

Three attempts take about as long as one (59 s vs 72 s measured) — they are N cold containers sharing
one pre-built image, with no warm-up run and no stagger. Confirm the table against origin yourself:

```bash
git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'
```

`--attempts 1` is a plain fire-and-forget single run; past that, `--repo` is required (attempts are
compared as the branches they ship). Add `--detach` to print the N function-call ids and exit in ~7 s,
laptop closed:

```bash
uv run decode remote attempts "…" --repo <url> --attempts 2 --sandbox-mode modal --detach
# come back later:
uv run decode remote logs                      # = modal app logs decode-headless
git ls-remote <url> 'refs/heads/decode/*'
uv run kitaru session list --agent decode --origin recorded
```

A row reads `shipped` only when the branch actually **reached origin**. A secured-but-unpushed branch
(no `SANDBOX_GIT_TOKEN`, or a token that can't write there) and `none` mode's discarded clone both read
`NOT SHIPPED`, with the reason printed under the table. The run still answers and still exits 0 — the
Hand-back fails soft ([ADR-0016 §4](../docs/adr/0016-drop-credential-proxy.md)).

### 5c. A nightly cron job (`nightly`)

§5a and §5b start with you typing `decode remote`. This trigger and the next start without you
([ADR-0020 Amendment §8](../docs/adr/0020-remote-headless-on-modal.md)); both are thin callers of the
same `run_task`, so a scheduled or POSTed run is byte-for-byte a `decode remote run` nobody had to
type.

The schedule and the job are **deploy-time** configuration, read from your shell by `decode remote
deploy` and shipped with the deployment — no `DECODE_NIGHTLY_CRON` exported, no schedule registered,
and a plain deploy is exactly what it was before:

```bash
DECODE_NIGHTLY_CRON="0 2 * * *" \
DECODE_NIGHTLY_TASK="Find every TODO comment, fix the ones under 20 lines, commit each fix separately." \
DECODE_NIGHTLY_REPO=https://github.com/you/your-repo.git \
DECODE_NIGHTLY_SANDBOX_MODE=modal \
DECODE_NIGHTLY_MAX_REQUESTS=120 \
uv run decode remote deploy
```

| Variable | Meaning |
|---|---|
| `DECODE_NIGHTLY_CRON` | crontab syntax, **UTC** — the switch: unset it and no schedule exists |
| `DECODE_NIGHTLY_TASK` | the prompt; required once a cron is set (a cron with no task dies on the laptop, not at 2am) |
| `DECODE_NIGHTLY_REPO` / `_SANDBOX_MODE` / `_MODEL` | the same knobs as `decode remote run`'s `--repo` / `--sandbox-mode` / `--model` |
| `DECODE_NIGHTLY_MAX_REQUESTS` / `_TIMEOUT_SECONDS` | the run's two ceilings — tokens and clock |

The job travels as a `modal.Secret.from_dict` env on the `nightly` Function (a Secret is simply how a
deploy-time env reaches a container; nothing in it is a credential), so the container reads the same
names back. Want, right after the deploy:

```
Decode: nightly job registered — cron='0 2 * * *' (UTC) task='Find every TODO comment, …'
```

Then, each morning: `decode remote logs` for the answer, `git ls-remote <repo>
'refs/heads/decode/*'` for the branch (`modal` mode + `SANDBOX_GIT_TOKEN`), `uv run kitaru session list
--agent decode --origin recorded` for the recording. Modal's dashboard has a *run now* button on any
scheduled Function if you don't want to wait for 2am. To stop the schedule, redeploy without
`DECODE_NIGHTLY_CRON` — Modal has no pause.

### 5d. A webhook (`webhook`)

The deploy output names the URL (`https://<workspace>--decode-headless-webhook.modal.run`); the proxy
token pair comes from [02_modal_endpoints.md](02_modal_endpoints.md) (`MODAL_PROXY_TOKEN_ID` /
`_SECRET` in your `.env`). The body takes the same knobs as `decode remote run` — only `task` is required — the
endpoint `spawn`s the run and answers at once; the run itself takes minutes, the caller waits seconds:

```bash
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

- **Proxy auth is the lock.** `requires_proxy_auth=True` means a request without a valid `Modal-Key` /
  `Modal-Secret` pair is refused at Modal's edge, before the Function runs — the same proxy token pair
  [02_modal_endpoints.md](02_modal_endpoints.md) mints for the open-model endpoints. Without it the URL
  would be a public "spend my tokens" button.
- **A bad run costs nothing.** `sandbox_mode: "docker"` or an empty `task` is a `400` carrying the same
  one-line message every other surface prints; nothing is spawned.
- **The endpoint holds no Secret.** It spawns `run_task` on the deployed app — the run's container gets
  the `decode-headless` Secret, the endpoint's does not.
- **Hook it to anything that can POST**: a GitHub Actions step (`curl` with the two headers from
  repository secrets), a ticket bot, Zapier, a Slack slash command. The answer is the same three places
  as the cron's.

---

## 6. Replay a recorded session on a Kitaru Worker

Replays run **from the top** on a Worker — the Kitaru server schedules, a Worker *you* run executes ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)). The Worker is a process: your laptop shell (§6a) or a Modal container (§6b). Each spawns its **own** Agent Version and cannot run the other's.

A **Baseline Replay** (no `--override`) is the control: it proves the Session still reproduces on the current Agent Version, which is what makes a later what-if — a model swap, a system-prompt change — attributable to the change and not to drift. Overrides, evaluators, cohorts and experiments are the operator journey in [06_evals_replays.md](06_evals_replays.md) and the `kitaru-investigation` / `kitaru-replay-experiment` skills.

**A replay's secrets are not the Environment Bucket.** Kitaru can attach secrets to a registered Agent Version (`kitaru agent version register --secret-id …`), which is how a Worker would hand credentials to the process it spawns. decode's versions deliberately attach **none**: a Worker layers a task's env on top of its own, so the Worker's own environment — your sourced `.env` on the laptop, the `decode-kitaru-worker` Secret on Modal — is what a replayed `decode run` sees, and no live key is ever copied onto the workspace ([ADR-0019 Amendments §2](../docs/adr/0019-kitaru-replay-runtime.md)). The Bucket ([01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional)) is *how `Settings` is filled* at a remote `DECODE_ENV`; `--secret-id` would be *what a replay's process env holds*. Neither feeds the other.

### 6a. On your laptop (agent version 2)

Three steps, once per machine:

```bash
# 1. Register the Agent Version the Worker spawns (adds a version; never a second agent).
uv run python scripts/register_kitaru_agent.py --dry-run   # prints the exact kitaru command
uv run python scripts/register_kitaru_agent.py

# 2. Start a Worker from a shell that HAS your provider credentials. Kitaru layers a task's env
#    on top of the Worker's own, so the keys reach `decode run` without ever leaving this host —
#    which is why the registered version attaches no secret.
set -a && . .env && set +a && kitaru worker start

# 3. Replay one recorded session (baseline: no --override). --tool-policy history replays the
#    recorded tool results instead of calling live tools; without it the server default MAY execute
#    them for real.
kitaru replay create <SESSION_ID> --agent decode@<VERSION> --evaluator <NAME>@<VERSION> \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}'
kitaru replay get <REPLAY_ID>        # status + result_session_id
kitaru session get <RESULT_SESSION>  # the replayed run, node by node
```

The registered version re-creates decode's context rather than simulating it: `decode run` with no inline prompt (the task arrives in `KITARU_TASK_INPUTS`), `SANDBOX_MODE=docker`, and a Workspace that is a fresh clone of this repo. Its working dir is a **Harness Home outside the repo** (`~/.decode-kitaru-worker`), so a replay's sessions, logs and Workspace never land in your working tree — watch it work with `docker ps` and `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

Which model a replay uses is the Worker shell's `LLM_PROVIDER` / model config, so a baseline replay reproduces the recorded run only if you start the Worker with the same provider it recorded against.

### 6b. On Modal (agent version 3)

This Worker is a container instead of your laptop. Register the run spec it spawns once — agent version 3, `SANDBOX_MODE=none`, in-image paths ([ADR-0020 §5](../docs/adr/0020-remote-headless-on-modal.md)):

```bash
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check --dry-run   # look first
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check
uv run kitaru agent version list decode
```

`--skip-bin-check` means "these paths live in the worker image" — the script then never stats, resolves
or creates them (they must be absolute for exactly that reason). The paths come from
`decode.remote.image`, so the image and the registration cannot drift apart. **Pin `decode@3` when
you replay, never "latest":** `latest_version` reads 4, and version 4 is a byte-identical duplicate of 3
created by accident during QA (Kitaru versions are immutable, so it stays).

Then deploy and start the worker:

```bash
uv run modal deploy scripts/modal_kitaru_worker.py
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 \
  --agent-version-id 01a029bf-0ae3-7de1-b594-4bc71a7ba91a          # = agent decode@3
```

`--agent-version-id` narrows the claim to `agent=<id>`. Use it whenever your laptop Worker is also
polling: both watch the same queue, and each can only run its **own** Agent Version — a Modal worker
that claims a v2 (docker) replay fails it, and a laptop worker that claims a v3 replay fails it too.
`importer` work is never claimed here on purpose: importer jobs read export files that exist only on
your machine.

Without a credential the worker refuses to start rather than polling silently for a day:

```
Decode: neither KITARU_API_KEY nor KITARU_API_TOKEN is set in this container. A container has no
`kitaru login` store, so the worker would poll unauthenticated for a day — add a control plane API
key (ZENPROKEY_…) to the decode-kitaru-worker secret.
```

With the `ZENPROKEY_…` from §4 in place ([`tasks/153`](../tasks/153-mint-control-plane-key-close-pending-gate.md)),
these three checks are the gate:

```bash
uv run kitaru worker list                     # want: a row 'decode-modal-worker', live: True

uv run kitaru replay create <RECORDED_SESSION_ID> --agent decode@3 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --evaluate-baselines
uv run kitaru job watch <JOB_ID>              # want: a terminal state with AGENT-level output
```

Want: the replay reaches a terminal state. An *agent-level* failure (a provider `503`) still proves the
pipe — the worker claimed, spawned and ran decode. A spawn error (`ModuleNotFoundError`, command not
found) is the one that means the image or the run spec is wrong
([06_evals_replays.md §7.8](06_evals_replays.md#7-field-notes--the-pitfalls-we-actually-hit)).

Observe and stop it from anywhere:

```bash
modal app logs decode-kitaru-worker
modal app stop decode-kitaru-worker
uv run modal app list                          # both apps deployed (plus the stopped ephemeral
                                               # apps every `modal run` leaves behind — they cost nothing)
```

The worker dies at Modal's **24 h** function ceiling; re-launch it with the one `modal run --detach`
command above. Whatever it still held at that moment is kitaru's own task-timeout story, deliberately
not engineered around.

---

## 7. Costs

| Item | ~/month |
|---|---|
| Modal headless containers + nested sandboxes | usage-based only — you pay per run-second, nothing idle |
| Modal-hosted Kitaru Worker | usage-based while it is up; the 24 h ceiling is also a cost ceiling |
| Managed Kitaru workspace | someone else's uptime, not your bill |

The only standing costs are provider tokens and whatever Modal you actually use. A deployed app with no
running Function costs nothing, which is why both apps are left deployed.

## 8. Troubleshooting

| Symptom | What it means |
|---|---|
| `[kitaru] not recording this run: … is unavailable` | The seam degraded: the workspace could not be reached (or `KITARU_AGENT_ID` is not an agent on it). The run itself is fine. Check `uv run kitaru status` — it prints the resolved `server_url` and whether the stored credential is still valid; re-auth with `kitaru login <url>`. |
| The run records nothing and says nothing | `KITARU_AGENT_ID` is empty, or `KITARU_API_URL` was set in `.env` but never exported — decode does not read that variable, the adapter's client does. |
| `Decode: the decode-headless app is not deployed …` | `decode remote run` / `attempts` before the first `decode remote deploy` (or after `modal app stop`). Deploy; nothing was billed. |
| `Decode: Modal credentials are missing or rejected …` | no account tokens on this machine: `uv run modal token set …`. `.env` does nothing for these. |
| A remote run behaves like last week's code | the source is baked into the image at deploy — re-run `decode remote deploy` after any decode change. |
| A replay stays queued | No Worker is claiming it: `kitaru worker list` should show one `live`. A laptop Worker only runs while its shell does; the Modal Worker dies at 24 h. Also check the Agent Version: a v2 replay needs the laptop Worker, a v3 replay the Modal one. |
| A replay fails at the first model request | The Worker's shell had no provider credential (the run spec attaches none, by design), or that provider is down. Restart it with `set -a && . .env && set +a && kitaru worker start`. |
| A replay fails before the agent starts | Usually the docker daemon (agent v2 pins `SANDBOX_MODE=docker`) or a stale `--command` path after a fresh `make install`. Re-register: `uv run python scripts/register_kitaru_agent.py`. |
| `403: Task credentials are not accepted on this route` | `KITARU_AGENT_ID` in the Worker's env (§4 — never in `decode-kitaru-worker`; `unset` it in a laptop Worker shell). |

## Go further

- Run headless **inside a sandbox** and on any repo: [03_sandboxing.md](03_sandboxing.md) (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`), including the sandbox git token the `decode-headless` Secret carries.
- Hydrate a laptop run's secrets from an Environment Bucket instead of `.env`: [01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional).
- The full evals loop — investigate, cohort, evaluator, replay, compare, experiment: [06_evals_replays.md](06_evals_replays.md).
- [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md) (headless + replay) and [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) (why this remote shape, and what it replaced).
