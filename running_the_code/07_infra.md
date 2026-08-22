# Infra — where decode's remote pieces live

> **Status: there is still no server to deploy** — but "remote" is a real place again
> ([ADR-0020](../docs/adr/0020-remote-headless-on-modal.md)). [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)
> deleted the self-hosted GCP+ZenML stack this page used to provision; what replaced it is a
> **launch-vs-execute split**: your laptop only launches, **Modal** executes (two operator scripts,
> zero servers, zero idle cost), and the **managed Kitaru workspace** stays the record/replay control
> plane. The old GCP build survives only as a [retirement note](#appendix--the-retired-self-hosted-gcp-stack)
> at the bottom; git history is its archive.

## The shape today

| Piece | What | Launch it with |
|---|---|---|
| **Managed [Kitaru](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand) workspace** | `https://f5ee9622-kitaru.cloudinfra.zenml.io` — recorded **Kitaru Sessions**, **Cohorts**, **Replays**, registered **Agent Versions**, the **Environment Bucket** secret. Someone else's uptime; it executes nothing. | `uv run kitaru status` · [03_runtime.md](03_runtime.md), [06_credentials.md](06_credentials.md) |
| **Modal Headless App** (`decode-headless`) | `scripts/modal_headless.py` — a Function that runs `decode run` as a **subprocess of the same console script your laptop runs**, in a gVisor container. One synchronous run, or N fire-and-forget attempts at one task → N comparable `decode/<session-id>` branches. Sandbox `none` / `modal` only; `docker` is rejected client-side (no Docker daemon on Modal). | `uv run modal run scripts/modal_headless.py::main --task "…"` · `uv run modal deploy …` + `…::attempts` — [§2](#2-run--verify--the-headless-app) |
| **Modal-hosted Kitaru Worker** (`decode-kitaru-worker`) | `scripts/modal_kitaru_worker.py` — a long-running Function running `kitaru worker start`, so **replays execute off your laptop**. Claims `agent` + `evaluator` work and spawns **agent version 3**. Dies at Modal's 24 h function ceiling; you re-launch it with one command. | `uv run modal deploy scripts/modal_kitaru_worker.py` + `uv run modal run --detach …` — [§3](#3-the-modal-hosted-kitaru-worker) |
| **Kitaru Worker on your laptop** | the other, unchanged option: `kitaru worker start` in *your* shell, spawning **agent version 2** (`SANDBOX_MODE=docker`, repo clone). The two Workers coexist — scope their claims so they don't race. | [08_evals_replays.md §5](08_evals_replays.md#5-start-a-worker-the-thing-that-executes-replays) |
| **Agent Version** | the immutable run spec a Worker spawns. **v2** = laptop/docker; **v3** = `SANDBOX_MODE=none` with the in-image paths `/.uv/.venv/bin/decode` + `/harness` (the container *is* the isolation). Both registered by `scripts/register_kitaru_agent.py`. | [§3](#3-the-modal-hosted-kitaru-worker) |
| **[Modal](https://modal.com?source=decodingai&campaign=harnesseng)** | now three things: the remote **Sandbox** (`SANDBOX_MODE=modal`), open-model **serving**, and — new — **hosting the harness itself**. Not as a server: as containers you fire and forget. | [04_sandboxing.md](04_sandboxing.md), [02_modal_endpoints.md](02_modal_endpoints.md) |
| **Opik** | tracing + evals, untouched by any of this ([ADR-0014](../docs/adr/0014-opik-observability.md)). | [05_evals.md](05_evals.md) |

**Both Modal apps build their image in code** ([ADR-0020 §2](../docs/adr/0020-remote-headless-on-modal.md)) —
`scripts/modal_image.py`, shared verbatim by both: `debian_slim` + `uv_sync` for the locked deps, then
this repo's source baked on top. No Dockerfile, no registry, no build cache to fight. The price of
baking source at build time: **a code change needs a re-`modal deploy` before the next run**.

Nothing here is required to use decode. Recording is opt-in, remote execution is opt-in, and with
`KITARU_AGENT_ID` unset and no Modal secret the whole page is irrelevant.

---

## 1. Secrets — two, deliberately asymmetric

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
| `SANDBOX_GIT_TOKEN` | optional | — | only the harness ships branches; scoped, revocable PAT ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md)) |

**The `KITARU_AGENT_ID` asymmetry is the whole point, not an oversight.** A Kitaru Worker injects a
*task-scoped* token into every run it spawns. With an agent id also in the env, decode's Recording Seam
probes an agents route that token cannot use and the replay hard-fails with
`403: Task credentials are not accepted on this route` — the pitfall in
[08_evals_replays.md §7.3](08_evals_replays.md#7-field-notes--the-pitfalls-we-actually-hit). The worker
Function scrubs the variable defensively at startup with one logged line, but the secret's composition
is the rule and the scrub is only the backstop.

### The container credential (`KITARU_API_KEY` must be a `ZENPROKEY_…`)

A container cannot run `kitaru login`, so the key travels in the secret. On a **managed** workspace it
must be a **control plane** key (`ZENPROKEY_…`): a workspace-local key is rejected server-side with
`Local API keys are rejected under control plane authentication.` The kitaru client exchanges a control
plane key for a session token and keeps renewing it, so a worker stays authenticated for its whole life.

> **⏳ Pending gate.** Everything else on this page has been executed; minting this key has not — it is
> an org-level write to ZenML Pro. Until it exists, the headless app still runs and answers, degrading
> with ONE `[kitaru] not recording this run: … 401: Missing bearer credential` line and exit 0 (that is
> the Recording Seam behaving exactly as [ADR-0019 §3](../docs/adr/0019-kitaru-replay-runtime.md)
> prescribes), and the Modal Worker refuses to start with one friendly line naming the missing variable.
> **The same key closes both.**

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
uv run modal run scripts/modal_headless.py::main --task "say hello" --sandbox-mode none
uv run kitaru session list --agent decode --origin recorded --size 3      # the run is listed
```

---

## 2. Run & verify — the headless app

### One synchronous run

```bash
uv run modal run scripts/modal_headless.py::main \
  --task "run bash to print uname -a and pwd and report both" --sandbox-mode none
```

**`::main` is not optional.** The file has two local entrypoints (`main` and `attempts`), so a bare
`modal run scripts/modal_headless.py` cannot pick one.

Want: a **gVisor Linux** kernel and an in-container path — that is the proof the run happened on Modal
and not on your laptop:

```
- **`uname -a`**: `Linux modal 4.19.0-gvisor #1 SMP … x86_64 GNU/Linux`
- **`pwd`**: `/harness`
Decode: run finished — exit=0 sandbox=none session=346fbde2-… branch=None
```

Swap to `--sandbox-mode modal` and the same task reports `/workspace` instead — the bash landed in a
**nested** Modal Sandbox, spawned by the container's own ambient Modal identity.

| `--sandbox-mode` | Where `bash` runs | What `--repo` does | Hand-back |
|---|---|---|---|
| `none` (default) | the gVisor container itself (`/harness`) | the **harness** clones it to `/scratch/repo` and launches decode there — decode never sees `--repo`, so its [ADR-0012 §3](../docs/adr/0012-isolated-workspace.md) guard stands | none: the clone dies with the container |
| `modal` | a nested Modal Sandbox, `/workspace` | passed straight through to `decode run --repo` | yes — pushes `decode/<session-id>` when `SANDBOX_GIT_TOKEN` is in the secret |
| `docker` | — | — | rejected client-side, ONE line, before any container starts |

The docker rejection costs nothing:

```bash
$ uv run modal run scripts/modal_headless.py::main --task "print uname" --sandbox-mode docker ; echo EXIT=$?
Decode: sandbox mode 'docker' cannot run on Modal — a Modal container has no Docker daemon. Use
--sandbox-mode none … or --sandbox-mode modal …
EXIT=1
```

### N attempts at one task, in parallel

Deploy once (this is what builds the shared image), then fan out. The attempts spawn against the
**deployed** Function, never the ephemeral one — that is what makes `--detach` real:

```bash
uv run modal deploy scripts/modal_headless.py

uv run modal run scripts/modal_headless.py::attempts \
  --task "add a hello line to README and commit" \
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
uv run modal run scripts/modal_headless.py::attempts --task "…" --repo <url> \
  --attempts 2 --sandbox-mode modal --detach
# come back later:
modal app logs decode-headless
git ls-remote <url> 'refs/heads/decode/*'
uv run kitaru session list --agent decode --origin recorded
```

A row reads `shipped` only when the branch actually **reached origin**. A secured-but-unpushed branch
(no `SANDBOX_GIT_TOKEN`, or a token that can't write there) and `none` mode's discarded clone both read
`NOT SHIPPED`, with the reason printed under the table. The run still answers and still exits 0 — the
Hand-back fails soft ([ADR-0016 §4](../docs/adr/0016-drop-credential-proxy.md)).

---

## 3. The Modal-hosted Kitaru Worker

Replays run wherever a **Worker** is; this one is a container instead of your laptop. Register the run
spec it spawns once — agent version 3, `SANDBOX_MODE=none`, in-image paths ([ADR-0020 §5](../docs/adr/0020-remote-headless-on-modal.md)):

```bash
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check --dry-run   # look first
uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
  --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check
uv run kitaru agent version list decode
```

`--skip-bin-check` means "these paths live in the worker image" — the script then never stats, resolves
or creates them (they must be absolute for exactly that reason). The paths come from
`scripts/modal_image.py`, so the image and the registration cannot drift apart. **Pin `decode@3` when
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

**⏳ Pending the `ZENPROKEY_…` from §1**, these three checks are the gate — the commands are ready, the
key is the only missing piece:

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
([08_evals_replays.md §7.8](08_evals_replays.md#7-field-notes--the-pitfalls-we-actually-hit)).

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

## 4. Costs

| Item | ~/month |
|---|---|
| Modal headless containers + nested sandboxes | usage-based only — you pay per run-second, nothing idle |
| Modal-hosted Kitaru Worker | usage-based while it is up; the 24 h ceiling is also a cost ceiling |
| Managed Kitaru workspace | someone else's uptime, not your bill |
| **the ~$16/month GCE VM + static IP** | **$0 — gone, and it stays gone** |

The only standing costs are provider tokens and whatever Modal you actually use. A deployed app with no
running Function costs nothing, which is why both apps are left deployed.

## Go further

- [03_runtime.md](03_runtime.md) — `decode run`, the Recording Seam, one replay end to end.
- [08_evals_replays.md](08_evals_replays.md) — the full operator journey: record → cohort → evaluator →
  replay → compare, and which Worker runs it.
- [04_sandboxing.md](04_sandboxing.md) — what `SANDBOX_MODE=modal` and the Hand-back actually do.
- [ADR-0020](../docs/adr/0020-remote-headless-on-modal.md) — why this shape (and what it replaced).

---

## Appendix — the retired self-hosted GCP stack

**Retired. Nothing from it runs, and nothing from it is in the repo any more.**

It was a self-hosted Kitaru 0.18 server on a GCE VM (static IP, Caddy + Let's Encrypt over `nip.io`,
GCS artifact store, Artifact Registry, a ZenML `prod-modal` stack) running `decode run` as a durable
Kitaru **flow** in a Modal container — [ADR-0008](../docs/adr/0008-kitaru-durable-runtime.md) +
[ADR-0010](../docs/adr/0010-runtime-replay.md). It died with the engine it was built on: kitaru 0.22
removed durable execution, and [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md) replaced the whole
thing with the managed workspace + Worker model above. [ADR-0020 §6](../docs/adr/0020-remote-headless-on-modal.md)
then deleted every surface it named: `make deploy` / `make run-remote`, `scripts/deploy.sh`,
`scripts/demo-multiple-attempts.sh`, `docker/flow.Dockerfile`, `scripts/kitaru_bootstrap_api_key.py`
and the `remote` dependency group.

Its traps were genuinely instructive — the containerd image store vs `umoci`, `nip.io` TLS, the GCS
`buckets.get` permission, ZenML's build cache, the code-upload TOCTOU — and **git history** keeps them
in full: `git log --oneline -- running_the_code/07_infra.md`, then
`git show <commit>:running_the_code/07_infra.md`. Read them there; run none of them.
