# Infra — the remote runtime stack (`decode run` on Modal, orchestrated by ZenML/Kitaru)

> **Status: deployed and verified.** `make run-remote TASK="…"` submits a headless agent to a
> self-hosted Kitaru server, it executes in a Modal container, spawns its own Modal bash sandbox, and
> every checkpoint lands on the server — durable, replayable, resumable from any machine.

decode already runs its *sandboxes* on Modal (`SANDBOX_MODE=modal`). This stack moves the **headless
agent itself** there. [`scripts/deploy.sh`](scripts/deploy.sh) provisions all of it; **§1 is the only
part you type by hand.**

## The shape (and why each piece exists)

| Piece | What | Why this and not more |
|---|---|---|
| **Kitaru/ZenML server** | one `zenmldocker/kitaru` container on one GCE VM, SQLite on the boot disk, Caddy in front for TLS | The durability core: executions, checkpoint metadata, replay, HITL waits, and the [Environment Bucket](CREDENTIALS.md). It must be reachable *from Modal*, so it cannot stay on the laptop. One VM + SQLite beats Cloud Run/GKE/MySQL for a single-user course. |
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
   `/var/kitaru` ownership fix, the 80/443 firewall rule (**asks you first**), a DENY rule on `:8080`,
   and deletion of any pre-TLS `allow-kitaru` rule.
6. **TLS** — installs Caddy on the VM, which gets a real Let's Encrypt cert for `<ip>.nip.io` and
   reverse-proxies to the server. Waits for `https://…/health`.
7. **Login** — mints the `decode-runner` service account + API key over the REST API, then
   `kitaru login`.
8. **Stack** — registers the GCP connector, GCS artifact store, AR registry, Modal orchestrator and
   sandbox, assembles the `prod-modal` stack, activates it, and runs `kitaru init`.
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
server     https://35.234.151.4.nip.io  (healthy)
bucket     gs://coding-agent-course-kitaru
registry   europe-west2-docker.pkg.dev/coding-agent-course/kitaru-images
vm         RUNNING
firewall   allow-kitaru-tls (tcp:80,443 → 0.0.0.0/0)
plaintext  denied (tcp:8080 DENY @ priority 100)
tls        Let's Encrypt, valid
stack      prod-modal
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

It touches **only GCP**. The Modal apps (`decode-<env>`, `decode-sandbox-<env>`) survive — they cost
nothing idle and the next `up` reuses them; `modal app stop <name>` if you want them gone. If you took
the §1.6 org-policy exception, undo it with `enable-enforce`.

`status` works on an empty stack too — that is the point of running it after a teardown.

---

## 3. Run a headless agent

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

`KITARU_STACK` is the zero-code seam: decode's flows call `.run()` with no stack argument, so the
ambient stack decides where they execute. First submit builds and pushes the image (3-5 min); later
submits reuse it (~90s). `SANDBOX_MODE` picks where the agent's `bash` lands:

| `SANDBOX_MODE` | Where `bash` runs | `--repo` Workspace | Hand-back |
|---|---|---|---|
| `modal` (default) | a **nested** Modal sandbox, `/workspace` | yes | needs `SANDBOX_GIT_TOKEN` — **not yet verified from a remote run** |
| `none` | inside the flow container itself, `/app/code` | no (ADR-0012 §3) | no |

Verified end to end: the flow container, Environment-Bucket hydration, the model call, the nested Modal
bash sandbox, and the `--repo` clone (read-only, public repo). **Not** verified: Hand-back actually
*pushing* a `decode/<session-id>` branch — that needs a repo you can write to. It fails soft, so a run
never loses its result over it.

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
database file` and crash-loops forever, while the health check just hangs.

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

**`kitaru init` is mandatory.** Without the `.kitaru` marker, ZenML infers the source root from the
entrypoint script — for `uv run decode` that is `.venv/bin` — and the code archive uploads **empty**:
`RuntimeError: The code archive to be uploaded does not contain any files.`

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
