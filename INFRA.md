# Infra — the remote runtime stack (`decode run` on Modal, orchestrated by ZenML/Kitaru)

> **Status: design approved, repo wiring pending.** The GCP/Modal/Kitaru infrastructure below is real and
> reproducible today; the repo-side pieces (`kitaru init`, flow `ImageSettings`, in-flow hand-back,
> `make deploy`) land with ADR-0016. Until then this file is the operator's bootstrap manual.

decode already runs its *sandboxes* on Modal (`SANDBOX_MODE=modal`). This stack moves the **headless agent
itself** there: `decode run` submits the Kitaru flow to a **Modal orchestrator stack**, the flow executes in a
Modal container, and every checkpoint / artifact / HITL wait is recorded on a **self-hosted Kitaru/ZenML
server** so the run is durable, replayable, and resumable from any machine.

## The shape (and why each piece exists)

| Piece | What | Why this and not more |
|---|---|---|
| **Kitaru/ZenML server** | one `zenmldocker/kitaru` container on one GCE VM, SQLite on the boot disk | The durability core: executions, checkpoint metadata, replay, HITL waits, and the [Environment Bucket](CREDENTIALS.md) all live here. It must be reachable *from Modal*, so it cannot stay on the laptop. One VM + SQLite beats Cloud Run/GKE/MySQL for a single-user course — the MySQL/helm path exists for teams ([docs](https://docs.zenml.io/kitaru/server-deployment/docker.md)). |
| **Modal orchestrator stack** | `kitaru stack create --type modal` | Kitaru's native Modal stack type — wraps the [ZenML Modal orchestrator](https://docs.zenml.io/stacks/stack-components/orchestrators/modal). The flow container runs in a Modal sandbox; decode's own `SANDBOX_MODE=modal` bash sandboxes are spawned *from* it. |
| **GCS bucket** | artifact store (`gs://…`) | Checkpoint payloads and artifacts. Modal cannot read a local artifact store — remote is mandatory. |
| **Artifact Registry repo** | container registry | The flow image is built locally at submit time and pushed here; Modal pulls it. Also mandatory-remote. |
| **Runtime service account** | `decode-kitaru@…` with 2 roles | Least privilege: the stack needs GCS read/write + image push/pull, nothing else. Your human account (owner) is only for the one-time bootstrap. |

Deliberately **not** in the picture:

- **ZenML Pro / managed workspace** — ~$999/mo Scale tier vs ~$15/mo VM. Self-hosting is one command; swapping
  to SaaS later is one `kitaru login`.
- **Server-side deployments** (`kitaru deploy` + HTTP invoke) — unconfirmed on Modal stacks (docs name
  k8s/Vertex/SageMaker/AzureML), and client-submit needs fewer concepts: `decode run` *is* the trigger.
- **MySQL / Cloud SQL** — only needed for multi-replica servers.

## 0. Prerequisites (all verified from the laptop)

```bash
gcloud auth list                      # your human account, roles/owner on the project
docker info >/dev/null && echo ok     # builds the flow image locally; DOCKER_BUILDKIT=1 required at submit
test -f ~/.modal.toml && echo ok      # Modal account tokens (kitaru picks up standard Modal auth)
uv run kitaru --version               # ships with the repo deps — pin the server image to this version
```

Reference values for this course's deployment — adapt to your own project:

```bash
export PROJECT=coding-agent-course
export REGION=europe-west2
export ZONE=europe-west2-a
export SA=decode-kitaru@${PROJECT}.iam.gserviceaccount.com
export BUCKET=gs://${PROJECT}-kitaru
export REGISTRY=${REGION}-docker.pkg.dev/${PROJECT}/kitaru-images
```

### Resuming an existing deployment — verify before you create

Every resource below is create-once, not idempotent — a re-run fails with "already exists". Audit what is
already there and **skip any section whose check passes**:

```bash
gcloud services list --enabled --format='value(config.name)' | grep -E '^(compute|artifactregistry|storage)\.'   # §1 APIs
gcloud iam service-accounts describe ${SA} && ls ~/.config/decode/decode-kitaru-key.json                        # §1 SA + key
gcloud storage buckets describe ${BUCKET} --format='value(name)'                                                # §2 bucket
gcloud artifacts repositories describe kitaru-images --location=${REGION}                                       # §2 registry
gcloud compute instances describe kitaru-server --zone=${ZONE} --format='value(status)'                         # §3 VM (RUNNING)
gcloud compute firewall-rules describe allow-kitaru                                                             # §3 firewall
uv run kitaru stack list                                                                                        # §4 stack
```

## 1. GCP project — APIs and the runtime service account

Your human account does the bootstrap; the **runtime SA** is what the stack holds. Two roles, no more.

```bash
gcloud config set project ${PROJECT}
gcloud services enable compute.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

gcloud iam service-accounts create decode-kitaru \
  --display-name="decode Kitaru stack (GCS artifacts + AR images)"
gcloud projects add-iam-policy-binding ${PROJECT} \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"
gcloud projects add-iam-policy-binding ${PROJECT} \
  --member="serviceAccount:${SA}" --role="roles/artifactregistry.writer"

# long-lived key — keep it OUT of the repo; the stack registers it server-side as a service connector
gcloud iam service-accounts keys create ~/.config/decode/decode-kitaru-key.json \
  --iam-account=${SA}
```

> **If key creation fails with `constraints/iam.disableServiceAccountKeyCreation`:** your org has Google's
> secure-by-default policy on (any org created since mid-2024 does). The key is still the right call here —
> Modal is foreign compute with no GCP identity, so *something* portable must reach it, and a 2-role SA key
> beats storing your owner-powered user token server-side. Exempt **only this project** (org-wide enforcement
> stays on) — needs `roles/orgpolicy.policyAdmin` on the org, which an org admin can self-grant:
>
> ```bash
> gcloud organizations add-iam-policy-binding <ORG_ID> \
>   --member=user:<you> --role=roles/orgpolicy.policyAdmin
> gcloud resource-manager org-policies disable-enforce \
>   iam.disableServiceAccountKeyCreation --project=${PROJECT}
> ```
>
> Propagation takes a minute or two — an immediate retry still fails with the same error. Reverse anytime
> with `enable-enforce`.

## 2. Storage + registry

```bash
gcloud storage buckets create ${BUCKET} --location=${REGION}
gcloud artifacts repositories create kitaru-images \
  --repository-format=docker --location=${REGION}
gcloud auth configure-docker ${REGION}-docker.pkg.dev   # once per machine
```

## 3. The server — one VM, one container

```bash
gcloud compute addresses create kitaru-server-ip --region=${REGION}
export KITARU_IP=$(gcloud compute addresses describe kitaru-server-ip \
  --region=${REGION} --format='value(address)')

# admin password: generated once, lives only in this file — never in the repo or shell history
umask 077 && openssl rand -base64 24 > ~/.config/decode/kitaru-admin-password

gcloud compute instances create-with-container kitaru-server \
  --machine-type=e2-small --zone=${ZONE} \
  --address=${KITARU_IP} --tags=kitaru-server \
  --container-image=zenmldocker/kitaru:0.18.0 \
  --container-env=ZENML_SERVER_AUTO_ACTIVATE=1,ZENML_DEFAULT_USER_NAME=admin,ZENML_DEFAULT_USER_PASSWORD="$(cat ~/.config/decode/kitaru-admin-password)" \
  --container-mount-host-path=host-path=/var/kitaru,mount-path=/zenml/.zenconfig/local_stores/default_zen_store

# the flow container in Modal must reach :8080; restrict the source range if you can pin Modal egress,
# otherwise this is admin-password-over-HTTP on an open port — fine for a course VM, not for real secrets
gcloud compute firewall-rules create allow-kitaru \
  --allow=tcp:8080 --target-tags=kitaru-server
```

The success criterion — first boot pulls the image and runs DB migrations, so give it 2-3 minutes:

```bash
until curl -sf http://${KITARU_IP}:8080/health >/dev/null; do sleep 5; done; echo "server up"
```

Notes that bite:

- **Pin the image tag to the client** (`kitaru --version` → `0.18.0`). Upgrades = bump both together.
- The container runs as UID 1000 — `/var/kitaru` on the VM must be writable by it
  (`sudo chown 1000 /var/kitaru` over SSH if the first boot loops).
- SQLite on the boot disk is the whole database. `gcloud compute disks snapshot` is your backup story.

## 4. Connect + create the Modal stack

```bash
# interactive — username `admin`, password from ~/.config/decode/kitaru-admin-password
uv run kitaru login http://${KITARU_IP}:8080

uv run kitaru stack create prod-modal \
  --type modal \
  --artifact-store ${BUCKET}/kitaru \
  --container-registry ${REGISTRY} \
  --sandbox modal \
  --credentials gcp-service-account:$HOME/.config/decode/decode-kitaru-key.json \
  --extra orchestrator.timeout=7200 \
  --extra sandbox.timeout=1800

uv run kitaru stack use prod-modal
```

Modal API credentials ride ambient `~/.modal.toml` — Modal auth and GCP credentials are separate channels.
Both timeouts are billing caps: a Modal sandbox keeps charging while alive.

For headless/CI submission (no interactive login):

```bash
uv run kitaru auth service-accounts create decode-runner
uv run kitaru auth api-keys create decode-runner default -o json   # → kitaru login <url> --api-key kat_…
```

---

> **STOP — the infra runbook ends here.** §4 completing means the GCP/Modal/Kitaru infrastructure is fully
> provisioned. §5 and §6 depend on repo work that ships with ADR-0016 (`make sync-secrets` needs the
> env-bucket feature merged; `make deploy` / `make run-remote` do not exist yet). An executor following this
> file step-by-step must not attempt them before that lands.

## 5. Secrets — the Environment Bucket, unchanged

Nothing new. The remote env's config surface is the same [Environment Bucket](CREDENTIALS.md) mechanism,
now stored on *this* server instead of the local one:

```bash
make sync-secrets ENV=prod      # .env → kitaru secret decode-prod, on the server you just logged into
```

The one documented exception carries over (ADR-0015): `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` are not
`Settings` fields — the *flow container* needs them in process env to spawn bash sandboxes
(`SANDBOX_MODE=modal`). They reach it via the image's `secret_environment_from`, not the bucket (ADR-0016).

## 6. Running (the target UX)

```bash
make deploy                      # idempotent: login check → sync-secrets → stack ensure → stack use
make run-remote TASK="…" REPO=…  # KITARU_STACK=prod-modal DECODE_ENV=prod uv run decode run "$TASK" --repo $REPO
```

`KITARU_STACK` is the zero-code seam: decode's flows call `.run()` with no stack argument, so the ambient
stack decides where they execute (`flow.run(stack=…)` > `@flow(stack=…)` > `kitaru.configure` >
`KITARU_STACK` > `[tool.kitaru].stack` > active stack). First submit builds and pushes the image
(`DOCKER_BUILDKIT=1`, `linux/amd64` by default — Apple Silicon safe); later submits reuse the cached build.

Operate a live run from anywhere the server is reachable:

```bash
uv run kitaru executions list
uv run kitaru executions logs <exec_id> --follow
uv run kitaru executions input <exec_id> --wait <name> --value "…"   # resolve a HITL wait
uv run kitaru executions replay <exec_id> --from <checkpoint>
```

## 7. Costs

| Item | ~/month |
|---|---|
| e2-small VM + 10GB disk + static IP | ~$15 |
| GCS + Artifact Registry (course-scale) | ~$1 |
| Modal flow + sandbox containers | usage-based; both timeouts above cap the burn |

## 8. Teardown

```bash
gcloud compute instances delete kitaru-server --zone=${ZONE}
gcloud compute addresses delete kitaru-server-ip --region=${REGION}
gcloud compute firewall-rules delete allow-kitaru
gcloud storage rm -r ${BUCKET}
gcloud artifacts repositories delete kitaru-images --location=${REGION}
gcloud iam service-accounts delete ${SA}                # kills the key with it

# undo the security exceptions from §1, if you made them
gcloud resource-manager org-policies enable-enforce \
  iam.disableServiceAccountKeyCreation --project=${PROJECT}
gcloud organizations remove-iam-policy-binding <ORG_ID> \
  --member=user:<you> --role=roles/orgpolicy.policyAdmin

rm -f ~/.config/decode/decode-kitaru-key.json ~/.config/decode/kitaru-admin-password
```
