# Infra — the remote runtime stack (`decode run` on Modal, orchestrated by ZenML/Kitaru)

> **Status: deployed and verified.** A headless `decode run` submits to a self-hosted Kitaru server,
> executes in a Modal container, spawns its own Modal bash sandbox, and records every checkpoint on
> the server. `scripts/deploy.sh` provisions the whole thing; this file explains what it does and why.

decode already runs its *sandboxes* on Modal (`SANDBOX_MODE=modal`). This stack moves the **headless
agent itself** there: `decode run` submits the Kitaru flow to a **Modal orchestrator stack**, the flow
executes in a Modal container, and every checkpoint / artifact / HITL wait is recorded on a
**self-hosted Kitaru/ZenML server** so the run is durable, replayable, and resumable from any machine.

## The shape (and why each piece exists)

| Piece | What | Why this and not more |
|---|---|---|
| **Kitaru/ZenML server** | one `zenmldocker/kitaru` container on one GCE VM, SQLite on the boot disk | The durability core: executions, checkpoint metadata, replay, HITL waits, and the [Environment Bucket](CREDENTIALS.md) all live here. It must be reachable *from Modal*, so it cannot stay on the laptop. One VM + SQLite beats Cloud Run/GKE/MySQL for a single-user course — the MySQL/helm path exists for teams ([docs](https://docs.zenml.io/kitaru/server-deployment/docker.md)). |
| **Modal orchestrator stack** | ZenML's `modal` orchestrator + `modal` sandbox flavors | The flow container runs as a Modal Sandbox; decode's own `SANDBOX_MODE=modal` bash sandboxes are spawned *from* it (nested, and verified working). |
| **GCS bucket** | artifact store (`gs://…`) | Checkpoint payloads, artifacts, and the uploaded code archive. Modal cannot read a local artifact store — remote is mandatory. |
| **Artifact Registry repo** | container registry | The flow image is built locally at submit time and pushed here; Modal pulls it. Also mandatory-remote. |
| **Runtime service account** | `decode-kitaru@…` | Least privilege: GCS objects + image push/pull, plus bucket-scoped admin (see §2). Your human account is only for the one-time bootstrap. |

Deliberately **not** in the picture:

- **ZenML Pro / managed workspace** — ~$999/mo Scale tier vs ~$15/mo VM. Self-hosting is one command;
  swapping to SaaS later is one `kitaru login`.
- **Server-side deployments** (`kitaru deploy` + HTTP invoke) — unconfirmed on Modal stacks, and
  client-submit needs fewer concepts: `decode run` *is* the trigger.
- **MySQL / Cloud SQL** — only needed for multi-replica servers.

## 0. One command

```bash
scripts/deploy.sh up        # provision everything; every step checks before it creates
scripts/deploy.sh status    # what exists right now
scripts/deploy.sh update    # re-apply the mutable parts (secrets, stack, image base)
scripts/deploy.sh down      # delete everything it created
```

You bring three credentials and nothing else:

```bash
gcloud auth list                      # a human account with roles/owner on the project
test -f ~/.modal.toml && echo ok      # `modal token set` — the orchestrator submits with these
test -f .env && echo ok               # decode's config surface; mirrored into the decode-prod bucket
```

Then kick off a headless agent:

```bash
make run-remote TASK="explain what this repo does"
make run-remote TASK="fix the failing test" REPO=https://github.com/you/your-repo.git
```

The rest of this file is what `deploy.sh` does, section by section — read it when something breaks.

Reference values for this course's deployment (override as env vars):

```bash
export PROJECT=coding-agent-course
export REGION=europe-west2
export ZONE=europe-west2-a
export SA=decode-kitaru@${PROJECT}.iam.gserviceaccount.com
export BUCKET=gs://${PROJECT}-kitaru
export REGISTRY=${REGION}-docker.pkg.dev/${PROJECT}/kitaru-images
```

## 1. GCP project — APIs and the runtime service account

```bash
gcloud services enable compute.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

gcloud iam service-accounts create decode-kitaru \
  --display-name="decode Kitaru stack (GCS artifacts + AR images)"
gcloud projects add-iam-policy-binding ${PROJECT} \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"
gcloud projects add-iam-policy-binding ${PROJECT} \
  --member="serviceAccount:${SA}" --role="roles/artifactregistry.writer"

# long-lived key — keep it OUT of the repo; the stack registers it as a ZenML service connector
gcloud iam service-accounts keys create ~/.config/decode/decode-kitaru-key.json \
  --iam-account=${SA}
```

> **If key creation fails with `constraints/iam.disableServiceAccountKeyCreation`:** your org has
> Google's secure-by-default policy on (any org created since mid-2024 does). The key is still the
> right call here — Modal is foreign compute with no GCP identity, so *something* portable must reach
> it, and a scoped SA key beats storing your owner-powered user token server-side. Exempt **only this
> project** (org-wide enforcement stays on) — needs `roles/orgpolicy.policyAdmin` on the org:
>
> ```bash
> gcloud organizations add-iam-policy-binding <ORG_ID> \
>   --member=user:<you> --role=roles/orgpolicy.policyAdmin
> gcloud resource-manager org-policies disable-enforce \
>   iam.disableServiceAccountKeyCreation --project=${PROJECT}
> ```
>
> Propagation takes a minute or two — an immediate retry still fails with the same error. Reverse
> anytime with `enable-enforce`.

## 2. Storage + registry

```bash
gcloud storage buckets create ${BUCKET} --location=${REGION}
gcloud artifacts repositories create kitaru-images \
  --repository-format=docker --location=${REGION}
gcloud auth configure-docker ${REGION}-docker.pkg.dev   # once per machine

# REQUIRED, and not obvious — see below
gcloud storage buckets add-iam-policy-binding ${BUCKET} \
  --member="serviceAccount:${SA}" --role=roles/storage.admin
```

> **`roles/storage.objectAdmin` is not enough, and the failure is misleading.** ZenML's GCS service
> connector calls `get_bucket()` **every time it mints credentials** for the artifact store — not just
> during `verify`. `objectAdmin` grants object read/write/list/delete but *not* `storage.buckets.get`,
> so submitting a flow dies with:
>
> ```
> RuntimeError: The connector ... could not be accessed due to an authorization error:
> failed to fetch GCS bucket …: 403 … does not have storage.buckets.get access
> ```
>
> The binding above is **bucket-scoped**, not project-wide. `roles/storage.legacyBucketReader` is the
> tighter alternative (it adds exactly `storage.buckets.get` + list); `storage.admin` on the one
> bucket is what this deployment uses.

## 3. The server — one VM, one container

```bash
gcloud compute addresses create kitaru-server-ip --region=${REGION}
export KITARU_IP=$(gcloud compute addresses describe kitaru-server-ip \
  --region=${REGION} --format='value(address)')

# admin password: generated once, never in the repo or shell history
umask 077 && openssl rand -base64 24 > ~/.config/decode/kitaru-admin-password

gcloud compute instances create-with-container kitaru-server \
  --machine-type=e2-small --zone=${ZONE} \
  --address=${KITARU_IP} --tags=kitaru-server \
  --container-image=zenmldocker/kitaru:0.18.0 \
  --container-env=ZENML_SERVER_AUTO_ACTIVATE=1,ZENML_DEFAULT_USER_NAME=admin,ZENML_DEFAULT_USER_PASSWORD="$(cat ~/.config/decode/kitaru-admin-password)" \
  --container-mount-host-path=host-path=/var/kitaru,mount-path=/zenml/.zenconfig/local_stores/default_zen_store

# THE FIRST BOOT ALWAYS CRASH-LOOPS WITHOUT THIS — see below
gcloud compute ssh kitaru-server --zone=${ZONE} --command='
  sudo chown -R 1000:1000 /var/kitaru &&
  sudo docker restart $(sudo docker ps -aqf name=klt-kitaru-server)'

# 80 is required: Let's Encrypt validates the ACME challenge over it. Caddy redirects it to HTTPS
# afterwards. :8080 is never exposed — it is Caddy's upstream on localhost.
gcloud compute firewall-rules create allow-kitaru-tls \
  --allow=tcp:80,tcp:443 --target-tags=kitaru-server
```

> **`/var/kitaru` must be chowned to UID 1000.** The container runs as UID 1000; the mounted host
> directory is created root-owned. The very first boot therefore dies with
> `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to open database file` and
> crash-loops forever. This is not a maybe — it happens every time, and the health check just hangs.

> **The firewall opens 80/443 to `0.0.0.0/0`, and that cannot be narrowed.** The server must be
> reachable *from Modal*, whose egress IPs are not pinnable on standard plans, so the login page stays
> exposed to the internet — scanners find it within hours. TLS (below) stops eavesdropping, not
> reachability. The admin password is 24 random bytes; treat everything in the secret store as
> rotatable.

### TLS — Caddy in front, or the dashboard does not work at all

This is not optional polish. **The Kitaru server sends an HSTS header while serving plain HTTP:**

```
strict-transport-security: max-age=63072000; includeSubdomains
```

A browser obeys it, upgrades its next request to HTTPS, hits a port that speaks no TLS, and gets
uvicorn's plaintext `Invalid HTTP request received.` back — which the dashboard's JS then tries to
parse as JSON and dies with **`Unexpected token 'I', "Invalid HT"... is not valid JSON`**. The login
itself succeeds (`POST /api/v1/login → 200`); it is the *next* request that breaks. ZenML ships that
header because it assumes it is behind TLS. So put it behind TLS.

Let's Encrypt will not issue a certificate for a bare IP, and there is no DNS zone here — so use
`nip.io`, which resolves `<ip>.nip.io` to `<ip>`:

```bash
gcloud compute ssh kitaru-server --zone=${ZONE} --command="
  sudo mkdir -p /var/caddy/data /var/caddy/config
  sudo tee /var/caddy/Caddyfile >/dev/null <<'CADDY'
${KITARU_IP}.nip.io {
	reverse_proxy localhost:8080
}
CADDY
  sudo docker run -d --name caddy --restart=always --network host \
    -v /var/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
    -v /var/caddy/data:/data -v /var/caddy/config:/config \
    caddy:2-alpine"
```

Caddy obtains the cert on first boot (retrying every 60s until 80/443 are actually open — the ACME
error reads `Timeout during connect (likely firewall problem)`), renews it forever, and redirects
`:80` → `:443`. `/data` is a volume so certs survive a reboot. The server URL is then
`https://<ip>.nip.io`, and `:8080` never needs to leave the VM.

> **Deny `:8080` outright — "no allow rule" is not enough.** The container listens on `0.0.0.0:8080`
> inside the VM (konlet uses host networking; the server has no bind-to-loopback knob), and GCP's
> default network ships **`default-allow-internal`**, which permits `tcp:0-65535` from `10.128.0.0/9`.
> So *any* VM in the VPC — a scratch box, a GKE node, a compromised workload — could read the admin
> password and every secret off the plaintext port. Not opening a port is not the same as closing it:
>
> ```bash
> gcloud compute firewall-rules create deny-kitaru-plaintext \
>   --direction=INGRESS --priority=100 --action=DENY --rules=tcp:8080 \
>   --target-tags=kitaru-server --source-ranges=0.0.0.0/0
> ```
>
> Priority 100 beats `default-allow-internal` (65534) and any stray allow at the default 1000. Loopback
> is not subject to VPC firewall rules, so Caddy → `localhost:8080` keeps working. `deploy.sh` creates
> this on `up`, and `status` shouts if it is missing.

Success criterion — first boot pulls the image and runs DB migrations, so give it 2-3 minutes:

```bash
until curl -sf https://${KITARU_IP}.nip.io/health >/dev/null; do sleep 5; done; echo "server up"
```

Notes that bite:

- **Pin the image tag to the client** (`kitaru --version` → `0.18.0`). Upgrades = bump both together.
- SQLite on the boot disk is the whole database. `gcloud compute disks snapshot` is your backup story.
- The **dashboard** is the same URL: `https://<ip>.nip.io`, user `admin`, password from
  `~/.config/decode/kitaru-admin-password` (`pbcopy < …` — do not echo it).

## 4. Login + the Modal stack

**`kitaru stack create --type modal` does not exist.** The types are `local`, `kubernetes`, `vertex`,
`sagemaker`, `azureml`. Modal ships as a ZenML *integration* (orchestrator + sandbox flavors), so the
stack is assembled with the `zenml` CLI — Kitaru reads the same stacks off the same server.

**Headless login needs a REST bootstrap.** `kitaru login` takes `--api-key` or an interactive browser
flow, and `kitaru auth api-keys create` needs a login first. `scripts/kitaru_bootstrap_api_key.py`
breaks the cycle: an OAuth2 password grant (`POST /api/v1/login`) mints a JWT, which creates the
`decode-runner` service account and its API key.

```bash
uv run kitaru login https://${KITARU_IP}.nip.io --api-key "$(cat ~/.config/decode/kitaru-api-key)"

uv run --group remote zenml service-connector register gcp-decode --type gcp \
  --auth-method service-account --project_id=${PROJECT} \
  --service_account_json=@$HOME/.config/decode/decode-kitaru-key.json

uv run --group remote zenml artifact-store register gcs-kitaru --flavor=gcp --path=${BUCKET}/kitaru
uv run --group remote zenml artifact-store connect gcs-kitaru --connector gcp-decode --resource-id ${BUCKET}

uv run --group remote zenml container-registry register ar-kitaru --flavor=gcp --uri=${REGISTRY}
uv run --group remote zenml container-registry connect ar-kitaru --connector gcp-decode --resource-id ${REGISTRY}

# both timeouts are billing caps: a Modal sandbox bills until it exits or its TTL fires
uv run --group remote zenml orchestrator register modal-orch --flavor=modal --timeout=7200 --synchronous=true
uv run --group remote zenml sandbox register modal-sandbox --flavor=modal --timeout=1800

uv run --group remote zenml stack register prod-modal -o modal-orch -a gcs-kitaru -c ar-kitaru -sb modal-sandbox
uv run kitaru stack use prod-modal
uv run kitaru init          # pins the source root — see below
```

Modal API credentials ride ambient `~/.modal.toml` at submit time; GCP credentials ride the service
connector. Separate channels, neither stored in the other.

> **`kitaru init` is mandatory, not cosmetic.** Without the `.kitaru` marker, ZenML infers the source
> root from the entrypoint script — for `uv run decode` that is `.venv/bin`, and the code archive
> uploads **empty**: `RuntimeError: The code archive to be uploaded does not contain any files.`

### The `remote` dependency group

Submitting to this stack needs ZenML's **entire** `gcp` integration installed locally — `gcsfs`,
`kfp`, `google-cloud-aiplatform`, `kubernetes`, the lot. ZenML's `check_installation()` is
all-or-nothing: miss one package and the integration never activates, the connector type never
registers, and you get `Service connector type gcp is not available locally` even though the connector
exists on the server. That is why `pyproject.toml` carries a `remote` dependency group and every
remote command runs `uv run --group remote`. The base install stays light for everyone else.

## 5. The flow image

`docker/flow.Dockerfile` installs decode and its dependencies; Kitaru/ZenML layers the flow code and
entrypoint on top. Both flows in `src/decode/runtime/flow.py` carry `@flow(image=_runtime_image())`,
which propagates `DECODE_ENV` and `SANDBOX_MODE` from the submitting process into the container.

Three traps, all of them load-bearing:

- **Docker Desktop's containerd image store must be OFF.** It is the default since 4.34. It pushes an
  OCI manifest whose layers keep *Docker* media types, and Modal unpacks images with `umoci`, which
  validates strictly against the OCI spec and dies on the hybrid:
  `Terminating task due to error: command umoci raw unpack … had exit status: 1`. Turn it off in
  Settings → General, or set `UseContainerdSnapshotter=false` in
  `~/Library/Group Containers/group.com.docker/settings-store.json` and `docker desktop restart`.
  `deploy.sh` refuses to run while it is on.
- **Pull the amd64 base first.** With containerd off, the classic builder cannot cross-build unless
  the base image's amd64 manifest is already local (`docker pull --platform linux/amd64
  python:3.12-slim`). Otherwise Apple Silicon builds an arm64 image that Modal — x86-64 only — cannot
  run, and the build fails inside the container with `psutil could not be installed from sources
  because gcc is not installed`.
- **`ENV UV_SYSTEM_PYTHON=1`.** ZenML layers its own `uv pip install -r
  .zenml_stack_integration_requirements` on top of your image *without* `--system`, and uv refuses to
  install when it finds no virtualenv (exit 2).

**ZenML caches builds server-side.** If you change the Docker setup and resubmit, ZenML may reuse the
old image by digest and your fix will look like it did nothing. Force a rebuild:

```bash
uv run --group remote zenml pipeline builds list
uv run --group remote zenml pipeline builds delete <id>
```

## 6. Secrets

The remote env's config surface is the same [Environment Bucket](CREDENTIALS.md) mechanism, now stored
on *this* server:

```bash
make sync-secrets ENV=prod      # .env → the kitaru secret decode-prod
```

The one documented exception (ADR-0015): `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are not `Settings`
fields, so `sync_secrets.py` skips them and the bucket never carries them — but the *flow container*
needs them in process env to spawn its own bash sandboxes. They reach it through a separate Kitaru
secret named by `ImageSettings.secret_environment_from`:

```bash
uv run kitaru secrets set decode-modal --private \
  --MODAL_TOKEN_ID=… --MODAL_TOKEN_SECRET=…
```

Only `SANDBOX_MODE=modal` needs it; `_runtime_image()` demands the secret only in that mode.

## 7. Running

```bash
make run-remote TASK="…" [REPO=<url>] [SANDBOX=modal|none]
```

which is:

```bash
DOCKER_BUILDKIT=1 KITARU_STACK=prod-modal DECODE_ENV=prod SANDBOX_MODE=modal \
  uv run --group remote decode run "$TASK" --repo "$REPO"
```

`KITARU_STACK` is the zero-code seam: decode's flows call `.run()` with no stack argument, so the
ambient stack decides where they execute (`flow.run(stack=…)` > `@flow(stack=…)` > `kitaru.configure` >
`KITARU_STACK` > `[tool.kitaru].stack` > active stack). First submit builds and pushes the image;
later submits reuse it. `SANDBOX_MODE` picks where the agent's `bash` lands:

| `SANDBOX_MODE` | Where `bash` runs | `--repo` Workspace | Hand-back |
|---|---|---|---|
| `modal` (default for `run-remote`) | a **nested** Modal sandbox, `/workspace` | yes | needs `SANDBOX_GIT_TOKEN` in the bucket — **not yet verified from a remote run** |
| `none` | inside the flow container itself, `/app/code` | no (ADR-0012 §3) | no |

Verified end to end on the remote stack: the flow container, Environment-Bucket hydration, the model
call, the nested Modal `bash` sandbox, and the `--repo` clone (read-only, against a public repo).
**Not** verified: Hand-back actually *pushing* a `decode/<session-id>` branch — that needs a repo you
can write to. It fails soft ("headless sandbox hand-back failed; continuing"), so a run never loses
its result over it.

Operate a live run from anywhere the server is reachable:

```bash
uv run kitaru executions list
uv run kitaru executions logs <exec_id> --follow
uv run kitaru executions input <exec_id> --wait <name> --value "…"   # resolve a HITL wait
uv run kitaru executions replay <exec_id> --from <checkpoint>
```

Debugging a failed remote run: the server log only carries what the flow *logged*. The container's
traceback lives in the Modal sandbox, whose id the error names
(`Modal orchestration sandbox sb-… failed with exit code 1`):

```python
import modal
sb = modal.Sandbox.from_id("sb-…")
print(sb.stderr.read())
```

One failure mode worth naming, because it looks like a wiring bug and is not: Gemini occasionally
returns empty / thinking-only responses, and pydantic-ai retries them until
`UnexpectedModelBehavior: Exceeded maximum output retries (3)`. No tool ever ran, no checkpoint
appears. Re-run it.

## 8. Costs

| Item | ~/month |
|---|---|
| e2-small VM + 10GB disk + static IP | ~$15 |
| GCS + Artifact Registry (course-scale) | ~$1 |
| Modal flow + sandbox containers | usage-based; both timeouts above cap the burn |

## 9. Teardown

```bash
scripts/deploy.sh down
```

which deletes the VM, IP, firewall rule, bucket (and every artifact in it), registry, and the service
account with its key. If you took the org-policy exception in §1, undo it:

```bash
gcloud resource-manager org-policies enable-enforce \
  iam.disableServiceAccountKeyCreation --project=${PROJECT}
gcloud organizations remove-iam-policy-binding <ORG_ID> \
  --member=user:<you> --role=roles/orgpolicy.policyAdmin
```
