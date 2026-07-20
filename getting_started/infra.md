# Infra — the remote runtime stack (`decode run` on Modal, orchestrated by ZenML/Kitaru)

> **Status: deployed and verified.** `make run-remote TASK="…"` submits a headless agent to a
> self-hosted Kitaru server, it executes in a Modal container, spawns its own Modal bash sandbox, and
> every checkpoint lands on the server — durable, replayable, resumable from any machine.

> **💰 This is the only part of the course that costs real money — and it's entirely optional.**
> Every other track (the lessons, the REPL, headless runs, sandboxing, evals) runs free and local.
> Deploying this stack bills a GCP project ~$16/month while it's up (the VM + a static IP; exact
> breakdown in [§5 Costs](#5-costs)) plus usage-based Modal compute. Skip it unless you specifically
> want headless runs executing entirely in the cloud — and when you're done, `scripts/deploy.sh down`
> deletes everything it created.

decode already runs its *sandboxes* on Modal (`SANDBOX_MODE=modal`). This stack moves the **headless
agent itself** there. [`scripts/deploy.sh`](../scripts/deploy.sh) provisions all of it; **§1 is the only
part you type by hand.**

## Quickstart

**Do [§1](#1-what-you-must-do-by-hand) first** — the toolchain, `gcloud auth login`, `modal token set`,
the Docker containerd toggle, and `.env`. `deploy.sh` refuses to start without them. Then:

**1. Deploy** (~10-15 min; it stops once to ask you to approve the 80/443 firewall rule):

```bash
scripts/deploy.sh up
```

**2. Read the URL** — it is derived from the server's IP, so it changes every rebuild:

```bash
scripts/deploy.sh status         # the `server` row, e.g. https://34.153.164.72.nip.io
```

That one URL is both the API the CLI talks to and the dashboard you open in a browser.

**3. Get the dashboard password** — user is `admin`, password was generated during `up`:

```bash
pbcopy < ~/.config/decode/kitaru-admin-password    # copies it; do NOT echo or `cat` it
```

**4. Run something:**

```bash
make run-remote TASK="run bash 'uname -a && pwd' and tell me what you see"
```

Want a **Linux x86-64** kernel and `/workspace` — that proves the `bash` ran in the Modal sandbox and
not on your laptop. The first submit builds and pushes the flow image (3-5 min); later ones reuse it
(~90s). Watch it land with `uv run kitaru executions list`.

**Done for the day?** The stack bills ~$16/month while it is up:

```bash
scripts/deploy.sh down <your-project>
```

Everything below is the detail behind these four steps.

## The shape (and why each piece exists)

| Piece | What | Why this and not more |
|---|---|---|
| **Kitaru/ZenML server** | one `zenmldocker/kitaru` container on one GCE VM, SQLite on the boot disk, Caddy in front for TLS | The durability core: executions, checkpoint metadata, replay, HITL waits, and the [Environment Bucket](credentials.md). It must be reachable *from Modal*, so it cannot stay on the laptop. One VM + SQLite beats Cloud Run/GKE/MySQL for a single-user course. |
| **Modal orchestrator stack** | ZenML's `modal` orchestrator + `modal` sandbox flavors | The flow container runs as a Modal Sandbox; decode's own bash sandboxes are spawned *from* it (nested). |
| **GCS bucket** | artifact store (`gs://…`) | Checkpoint payloads, artifacts, uploaded code. Modal cannot read a local artifact store — remote is mandatory. |
| **Artifact Registry repo** | container registry | The flow image is built locally at submit time and pushed here; Modal pulls it. Also mandatory-remote. |
| **Runtime service account** | `decode-kitaru@…` | Least privilege: GCS objects + image push/pull, plus one bucket-scoped admin binding (§4). Your human account is only for the bootstrap. |

Deliberately **not** here: ZenML Pro (~$999/mo vs ~$16/mo), server-side `kitaru deploy` (client-submit
needs fewer concepts — `decode run` *is* the trigger), MySQL/Cloud SQL (multi-replica only).

---

## 1. What you MUST do by hand

`deploy.sh` cannot do these — they need a browser, a GUI toggle, an org admin, or your judgement.
Do them once, in order.

### 1.1 Install the toolchain

```bash
make install                    # uv sync + git hooks
brew install --cask docker      # or Docker Desktop from docker.com
brew install google-cloud-sdk   # gcloud
```

`uv`, `docker`, and `gcloud` must all be on your PATH. `deploy.sh` refuses to start otherwise.

### 1.2 Authenticate — interactive, browser-based, cannot be scripted

```bash
gcloud auth login               # a human account with roles/owner on the project
gcloud config set project <your-project>
modal token set                 # writes ~/.modal.toml — the tokens the orchestrator submits with
```

Both open a browser. `gcloud` credentials also **expire**, and a `deploy.sh` run will then die with
`Reauthentication failed. cannot prompt during non-interactive execution.` — just `gcloud auth login`
again.

### 1.3 Turn OFF Docker Desktop's containerd image store

**Not optional.** It is the default since Docker Desktop 4.34, and it pushes an OCI manifest whose
layers keep *Docker* media types. Modal unpacks images with `umoci`, which validates strictly against
the OCI spec and dies on the hybrid:

```
Terminating task due to error: command umoci raw unpack … had exit status: 1
```

**Settings → General → uncheck "Use containerd for pulling and storing images" → Apply & Restart.**

`deploy.sh` preflight refuses to run while it is on, so you cannot forget. (Scriptable alternative:
set `UseContainerdSnapshotter=false` in
`~/Library/Group Containers/group.com.docker/settings-store.json`, then `docker desktop restart`.)

### 1.4 Fill in `.env`

```bash
cp .env.example .env            # then fill in the keys
```

At minimum `GEMINI_API_KEY`, plus `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` (copy them out of
`~/.modal.toml`), and `SANDBOX_GIT_TOKEN` if you want the agent to push its work back as a branch.
`deploy.sh` mirrors this file into the server's `decode-prod` Environment Bucket.

### 1.5 Approve the firewall rule when the script asks

`deploy.sh up` **stops and asks** before opening ports, because the answer is a security decision, not
a default:

```
The next rule opens tcp:80 and tcp:443 to 0.0.0.0/0 …
Create the firewall rule? [y/N]
```

Port 80 is required (Let's Encrypt validates the ACME challenge over it) and 443 is the server. It
**cannot** be narrowed to your laptop: the Modal flow container must reach the server too, and Modal's
egress IPs are not pinnable on standard plans. Consequence: **the login page is reachable from the
internet and scanners find it within hours.** TLS stops eavesdropping, not reachability. Treat every
key in that server's secret store as rotatable.

### 1.6 Only if SA-key creation fails: the org-policy exception

If `deploy.sh up` dies on `constraints/iam.disableServiceAccountKeyCreation`, your org has Google's
secure-by-default policy on (any org created since mid-2024 does). The key is still the right call —
Modal is foreign compute with no GCP identity, so *something* portable must reach it, and a scoped SA
key beats storing your owner-powered user token server-side. Exempt **only this project**; needs
`roles/orgpolicy.policyAdmin` on the org:

```bash
gcloud organizations add-iam-policy-binding <ORG_ID> \
  --member=user:<you> --role=roles/orgpolicy.policyAdmin
gcloud resource-manager org-policies disable-enforce \
  iam.disableServiceAccountKeyCreation --project=<your-project>
```

Propagation takes a minute or two — an immediate retry fails with the same error. Reverse anytime with
`enable-enforce`.

---

## 2. Deploy

```bash
scripts/deploy.sh up        # provision everything
scripts/deploy.sh status    # what exists right now
scripts/deploy.sh update    # re-apply the mutable parts
scripts/deploy.sh down      # delete everything it created
```

Override any of `PROJECT`, `REGION`, `ZONE`, `DECODE_ENV` as env vars; the defaults are this course's
(`coding-agent-course`, `europe-west2`, `europe-west2-a`, `prod`).

### `up` — provision, safely re-runnable

Every step checks before it creates, so a re-run after a failure resumes instead of erroring with
"already exists". In order:

1. **Preflight** — gcloud/uv/docker present, an active gcloud account, `~/.modal.toml` exists, the
   containerd store is OFF, and pulls `python:3.12-slim` for **linux/amd64** (Modal is x86-64 only;
   without this an Apple Silicon build produces an arm64 image Modal cannot run).
2. **APIs** — compute, artifactregistry, storage.
3. **Service account** `decode-kitaru` — `roles/storage.objectAdmin` + `roles/artifactregistry.writer`,
   and a JSON key at `~/.config/decode/decode-kitaru-key.json`.
4. **Bucket + registry** — plus a **bucket-scoped `roles/storage.admin`** binding (§4 explains why the
   project-level roles are not enough).
5. **Server** — static IP, generated admin password, the GCE VM running the Kitaru container, the
   `/var/kitaru` ownership repair (**every** `up`, not just the first — that is how a crash-looping
   server heals), the 80/443 firewall rule (**asks you first**), a DENY rule on `:8080`, and deletion
   of any pre-TLS `allow-kitaru` rule.
6. **TLS** — installs Caddy on the VM, which gets a real Let's Encrypt cert for `<ip>.nip.io` and
   reverse-proxies to the server. Waits for `https://…/health`.
7. **Login** — mints the `decode-runner` service account + API key over the REST API, then
   `kitaru login`.
8. **Stack** — registers the GCP connector, GCS artifact store, AR registry, Modal orchestrator and
   sandbox, assembles the `prod-modal` stack, runs `kitaru init`, then activates it (that order
   matters — §4).
9. **Secrets** — writes the `decode-modal` secret (Modal tokens) and mirrors `.env` into the
   `decode-prod` Environment Bucket.

Files it writes, and nothing else outside GCP/Modal/Kitaru:

| Path | What |
|---|---|
| `~/.config/decode/decode-kitaru-key.json` | the runtime SA key the stack authenticates GCS + AR with |
| `~/.config/decode/kitaru-admin-password` | the server's admin password (generated once) |
| `~/.config/decode/kitaru-api-key` | the `decode-runner` key the CLI logs in with |

### `update` — after you change code, deps, or `.env`

Re-applies TLS (a dead Caddy self-heals), the login, the stack, and the secrets. Does **not** touch the
VM or the bucket. The next `run-remote` rebuilds the flow image if the code or deps changed.

### `status` — the one-screen truth

```
project    coding-agent-course
server     https://34.13.23.119.nip.io  (healthy)
bucket     gs://coding-agent-course-kitaru
registry   europe-west2-docker.pkg.dev/coding-agent-course/kitaru-images
vm         RUNNING
firewall   allow-kitaru-tls (tcp:80,443 → 0.0.0.0/0)
legacy     none (:8080 not exposed)
plaintext  denied (tcp:8080 DENY @ priority 100)
tls        Let's Encrypt, valid
stack      prod-modal
```

The URL moves with the IP: `nip.io` encodes the address in the hostname, so every rebuilt server gets a
new name (and a new cert). `status` is where you read the current one.

### Verify the deploy

Four checks, cheapest first. Each proves a different layer, so the first one that fails tells you
where to look.

**1. The stack exists** (~10s, free):

```bash
scripts/deploy.sh status
```

Want: `server … (healthy)`, `tls  Let's Encrypt, valid`, `plaintext  denied`, `stack  prod-modal`, and
nothing `<none>`. A `(unreachable)` server with a `RUNNING` vm is almost always the `/var/kitaru`
ownership trap (§4) — re-run `up`, which now repairs it.

**2. The agent runs remotely** — flow container, Environment-Bucket hydration, the model call, and the
nested Modal sandbox. No `--repo`, so nothing is shipped:

```bash
make run-remote TASK="run bash 'uname -a && pwd' and tell me what you see"
```

Want: a **Linux x86-64** kernel and `/workspace`. That is the proof the `bash` landed in the Modal
sandbox and not on your laptop — a macOS kernel here means `SANDBOX_MODE` never reached the container.

**3. The work comes back** — the Hand-back (ADR-0012 §8). The task must *forbid* pushing, so that the
harness is what ships the branch, not the model:

```bash
make run-remote REPO=https://github.com/you/your-repo.git \
  TASK="add a line to README.md saying hello. commit it. do NOT push and do NOT open a PR."
git ls-remote https://github.com/you/your-repo.git 'refs/heads/decode/*'
```

Want: a `decode/<first-8-of-the-exec_id>` branch carrying the model's commit. Missing means the flow
container could not push: confirm `SANDBOX_GIT_TOKEN` reached the bucket with

```bash
make sync-secrets ENV=prod      # re-mirrors .env; prints key NAMES only, never values
```

and that the token can write to that repo. Hand-back fails soft, so the run still returns its answer
either way — read the `[handback]` line in the run's output for which it was.

**4. The record is durable** — the server saw it:

```bash
uv run kitaru executions list
uv run kitaru executions logs <exec_id>
```

Or open the dashboard (`scripts/deploy.sh status` prints the URL; user `admin`, password in
`~/.config/decode/kitaru-admin-password`) — the run is on the Flows page.

One more worth a glance, because it is the difference between a tidy account and 16 orphaned apps:

```bash
uv run modal app list      # exactly decode-prod + decode-sandbox-prod, one per environment
```

### `down` — teardown

Deletes the VM — **with it every execution record and both Kitaru secrets** (`decode-<env>`,
`decode-modal`), which live in the SQLite on its boot disk — plus the IP, the firewall rules, the bucket
**and every artifact in it**, the registry, and the service account with its key; removes the three
local files above. A rebuilt server gets its secrets back from your `.env` (`make sync-secrets`), but the
run history is gone.

It asks you to type the project name to confirm. Without a terminal (a `!` command in an agent session,
CI) there is nothing to type on, so pass it as the argument instead — same confirmation, typed by you:

```bash
scripts/deploy.sh down coding-agent-course
```

It also stops this environment's two Modal apps — `decode-<env>` (the flow container's app) and
`decode-sandbox-<env>` (its nested bash sandboxes). Stopping is permanent; the next `up` recreates them
on the first submit. Apps from *other* environments are left alone. If you took the §1.6 org-policy
exception, undo it with `enable-enforce`.

### Verify the teardown

`status` works on an empty stack too — that is the point of running it after a teardown:

```bash
scripts/deploy.sh status
```

Want: every row `<none>`, plus `plaintext  n/a (no server)` and `stack  <none>`. Then confirm nothing
survives that could still bill you:

```bash
gcloud compute instances list --project=coding-agent-course           # no kitaru-server
gcloud compute addresses list --project=coding-agent-course           # no kitaru-server-ip (a reserved
                                                                      #   IP with nothing attached BILLS)
gcloud compute firewall-rules list --project=coding-agent-course      # no allow-kitaru-tls / deny-…
gcloud storage ls --project=coding-agent-course                       # no gs://…-kitaru
gcloud artifacts repositories list --project=coding-agent-course      # no kitaru-images
gcloud iam service-accounts list --project=coding-agent-course        # no decode-kitaru
uv run modal app list                                                 # decode-prod + decode-sandbox-prod
                                                                      #   both `stopped`
ls ~/.config/decode                                                   # empty
```

The old URL should now fail to connect at all (not merely 502 — that would mean Caddy is still up).
Your local `kitaru` client still points at the dead server; the next `up` logs it in again.

---

## 3. Run a headless agent

### How the CLI reaches the server

No URL is passed on the command line — `decode run` reads it from the client config `kitaru login`
wrote back in `up` step 7. Two files, neither under `~/.config/kitaru` (that path does not exist —
looking there is a dead end):

| Path | What |
|---|---|
| `~/Library/Application Support/kitaru/config.yaml` | `store.type: rest` + `store.url: https://<ip>.nip.io` — the switch from a local SQLite store to your server, plus `active_stack_id` / `active_project_id` |
| `~/Library/Application Support/kitaru/credentials.yaml` | the API token, sent as a bearer on every REST call. **Secret — never `cat` it into a terminal an agent can read** |
| `.kitaru/config.yaml` (repo-local) | `active_stack_id` / `active_project_id` for this checkout; also the source-root marker (§4) |

The chain that put them there: `kitaru_bootstrap_api_key.py` password-grants a JWT with the admin
password → creates the `decode-runner` service account → writes its key to
`~/.config/decode/kitaru-api-key` → `kitaru login <url> --api-key …` exchanges it for the stored token.

So a submit is: client REST-calls the server for the `prod-modal` stack definition → builds the flow
image locally and pushes it to Artifact Registry → uploads the code archive to GCS → Modal pulls and
runs it → checkpoints flow back over that same REST connection. **The Modal container reaches the
server at the same public `nip.io` URL your laptop does** — which is why §1.5's firewall rule cannot be
narrowed to your IP.

Connection broken? `uv run kitaru stack list` is the cheapest probe — it either answers off the server
or tells you the auth is dead. Re-run `scripts/deploy.sh update`, which re-mints and re-logs in.

### Submit

```bash
make run-remote TASK="explain what this repo does"
make run-remote TASK="fix the failing test" REPO=https://github.com/you/your-repo.git
make run-remote TASK="…" SANDBOX=none          # bash runs in the flow container itself
```

which is just:

```bash
DOCKER_BUILDKIT=1 KITARU_STACK=prod-modal DECODE_ENV=prod SANDBOX_MODE=modal \
  uv run --group remote decode run "$TASK" --repo "$REPO"
```

`KITARU_STACK` is the zero-code seam: decode's flows call `.run()` with no stack argument, so an
ambient value decides where they execute. Resolution order:

```
explicit  .run(stack=…)   >   KITARU_STACK env   >   the configured active stack
```

**`KITARU_STACK` and "the active stack" are different knobs, not two names for one.** Kitaru is
explicit about it (`kitaru/_config/_active_context.py`): *"`KITARU_STACK` is an execution default and
does not set ZenML's active stack."* So `make run-remote` lands on `prod-modal` because the Makefile
exports it — regardless of what `kitaru stack current` reports. The active stack is what bare
`uv run kitaru …` commands and the `stack` row of `deploy.sh status` read. Expect to see them disagree;
only the `status` row is worth fixing (`uv run kitaru stack use prod-modal`).

A submit builds and pushes the flow image (3-5 min) only when the code, the deps or the Docker settings
changed; otherwise it reuses the recorded build (`Reusing existing build …`, ~90s). `SANDBOX_MODE` picks
where the agent's `bash` lands:

| `SANDBOX_MODE` | Where `bash` runs | `--repo` Workspace | Hand-back |
|---|---|---|---|
| `modal` (default) | a **nested** Modal sandbox, `/workspace` | yes | yes — pushes `decode/<exec-id>` with `SANDBOX_GIT_TOKEN` |
| `none` | inside the flow container itself, `/app/code` | no (ADR-0012 §3) | no |

**The Hand-back runs inside the flow container, not on your laptop** (ADR-0012 §8, amended). That is
where the Workspace is cloned, worked in, and swept back from the sandbox — the submitting machine's
`.decode/sandbox` is a stranger to the run. It is also the only reason the push needs
`SANDBOX_GIT_TOKEN`: a flow container has no ambient git credential at all. Without the token the run
still returns its answer, and says plainly that it could not push. Verify it with check 3 above.

**Modal apps: one per environment, never one per run.** The flow container lands in `decode-<env>` and
its bash sandboxes in `decode-sandbox-<env>` (`runtime/modal_app.py`, `sandbox/modal_backend.py`).
ZenML would otherwise name the app `zenml-<run_id>` and leave a fresh app behind on every single run.

### Operate a live run

```bash
uv run kitaru executions list
uv run kitaru executions logs <exec_id> --follow
uv run kitaru executions input <exec_id> --wait <name> --value "…"   # resolve a HITL wait
uv run kitaru executions replay <exec_id> --from <checkpoint>
```

**Dashboard:** the same URL `deploy.sh status` prints. User `admin`, password from
`~/.config/decode/kitaru-admin-password` (`pbcopy < …` — do not echo it). Its **Flows** page shows one
row per flow with only its latest run; **Executions** is the per-run list.

---

## 4. Reference — the traps, and why the script does what it does

Read this when something breaks. Every item cost a debugging cycle.

**`roles/storage.objectAdmin` is not enough.** ZenML's GCS connector calls `get_bucket()` **every time
it mints credentials** — not just on `verify`. `objectAdmin` grants object read/write/list/delete but
not `storage.buckets.get`, so a submit dies with `403 … does not have storage.buckets.get access`.
Hence the bucket-scoped `storage.admin` binding (`legacyBucketReader` is the tighter alternative).

**`/var/kitaru` must be chowned to UID 1000.** The container runs as UID 1000; the mounted host dir is
created root-owned. The first boot therefore dies with `sqlite3.OperationalError: unable to open
database file` and crash-loops forever, while the health check just hangs. `up` **polls** for the
directory — it does not exist until konlet starts the container, so any fixed sleep races it — and it
re-checks on *every* `up`, not only when it creates the VM: a crash-looping server is exactly the state
you re-run `up` to repair. An already-correct dir is left alone (no restart of a healthy server).

**Concurrent submits race on the code upload.** ZenML archives the source root, names the object after
its content hash, and uploads it with a **check-then-copy** — a TOCTOU. Submit N runs at once when that
archive is not already in the bucket and they all decide to upload it; the losers die with
`FileExistsError: Destination file 'gs://…/code_uploads/<hash>.tar.gz' already exists`. One warm-up run
first uploads the archive, and every later submit then skips the upload — so warm, THEN fan out, and do
not edit tracked files in between (any edit changes the hash and re-arms the race).
`scripts/demo-multiple-attempts.sh` does both, plus an 8s stagger as insurance.

**Never pass `platform=` to `ImageSettings` — pin it in the Dockerfile's `FROM`.** Otherwise *every*
submit rebuilds and re-pushes the image, forever. ZenML decides whether it can reuse a build by hashing
the Docker settings, but its builder **mutates those settings mid-build** (`build_config.build_options`:
`pull`/`rm` flip `None → False`). `find_existing_build` hashes them *before* the mutation, and the
checksum stored on the build is computed *after* it, so the lookup can never match what the build
recorded — a permanent miss, not a cache miss. Default users never see it because `build_config` is
`None` and there is nothing to mutate; passing `platform` is what creates the object. Modal is x86-64
and a Mac is not, so the platform must be pinned *somewhere* — `FROM --platform=linux/amd64` does it
with no `build_config`, and the image ZenML layers on top inherits the architecture. Confirmed fixed:
the second consecutive submit now logs `Reusing existing build … for stack prod-modal`.

**TLS is mandatory, not polish.** The Kitaru server sends `strict-transport-security: max-age=63072000`
*while serving plain HTTP*. A browser obeys it, upgrades the next request to HTTPS, hits a port that
speaks none, and the dashboard dies parsing uvicorn's plaintext `Invalid HTTP request received.` as
JSON: **`Unexpected token 'I', "Invalid HT"... is not valid JSON`**. Let's Encrypt will not issue for a
bare IP and there is no DNS zone here, so the server is named via `nip.io` (`<ip>.nip.io` → `<ip>`).

**Deny `:8080`; "no allow rule" is not the same as closed.** The container listens on `0.0.0.0:8080`
(konlet uses host networking; no bind-to-loopback knob), and GCP's default network ships
`default-allow-internal` — `tcp:0-65535` from `10.128.0.0/9`. Any VM in the VPC could read the admin
password and every secret in the clear. The DENY rule at priority 100 beats it.

**`kitaru stack create --type modal` does not exist.** The types are local/kubernetes/vertex/sagemaker/
azureml. Modal ships as a ZenML *integration*, so the stack is assembled with the `zenml` CLI — Kitaru
reads the same stacks off the same server.

**`kitaru init` is mandatory — and must run BEFORE `kitaru stack use`.** Without the `.kitaru` marker,
ZenML infers the source root from the entrypoint script — for `uv run decode` that is `.venv/bin` — and
the code archive uploads **empty**: `RuntimeError: The code archive to be uploaded does not contain any
files.` But `init` also *writes* `.kitaru/config.yaml` with its own `active_stack_id: <default>`, so
running it after `stack use` silently reverts the selection. That bug only bites a **fresh** deploy —
later `up`s skip `init` because `.kitaru` already exists, so it hides on every re-run — and it is
cosmetic for runs (`make run-remote` exports `KITARU_STACK`, §3), but it leaves `deploy.sh status`
reporting `stack default` and the verify check below failing for no real reason. `ensure_stack` now
orders them `init` → `use`.

**The `remote` dependency group is all-or-nothing.** Submitting needs ZenML's *entire* `gcp`
integration locally (`gcsfs`, `kfp`, `google-cloud-aiplatform`, `kubernetes`, …). Miss one package and
`check_installation()` fails, the integration never activates, and you get `Service connector type gcp
is not available locally` even though the connector exists on the server. Hence `uv run --group remote`
on every remote command; the base install stays light for everyone else.

**`ENV UV_SYSTEM_PYTHON=1` in the flow image.** ZenML layers its own `uv pip install -r
.zenml_stack_integration_requirements` on top *without* `--system`, and uv refuses to install when it
finds no virtualenv (exit 2).

**ZenML caches builds server-side.** Change the Docker setup, resubmit, and ZenML may reuse the old
image by digest — your fix looks like a no-op. Force a rebuild:

```bash
uv run --group remote zenml pipeline builds list
uv run --group remote zenml pipeline builds delete <id>
```

**Headless login needs a REST bootstrap.** `kitaru login` takes `--api-key` or an interactive browser
flow, and creating an API key needs a login first. `scripts/kitaru_bootstrap_api_key.py` breaks the
cycle with an OAuth2 password grant.

**Debugging a failed remote run.** The server log only carries what the flow *logged*. The container's
traceback lives in the Modal sandbox the error names (`Modal orchestration sandbox sb-… failed`):

```python
import modal
print(modal.Sandbox.from_id("sb-…").stderr.read())
```

**Not a wiring bug:** Gemini occasionally returns empty / thinking-only responses, and pydantic-ai
retries until `UnexpectedModelBehavior: Exceeded maximum output retries (3)`. No tool ran, no
checkpoint appears. Re-run it.

**Secrets.** `make sync-secrets ENV=prod` mirrors `.env` into the `decode-prod` bucket. The one
exception (ADR-0015): `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are not `Settings` fields, so the bucket
never carries them — but the flow container needs them in process env to spawn bash sandboxes. They
ride a separate `decode-modal` secret via `ImageSettings.secret_environment_from`.

**Pin the server image to the client.** `kitaru --version` → `0.18.0` must match the container tag.
Upgrades bump both together. SQLite on the boot disk is the whole database; `gcloud compute disks
snapshot` is your backup story.

## 5. Costs

| Item | ~/month |
|---|---|
| e2-small VM + 10GB disk + static IP | ~$15 |
| GCS + Artifact Registry (course-scale) | ~$1 |
| Modal flow + sandbox containers | usage-based; the orchestrator (2h) and sandbox (30m) timeouts cap the burn |
