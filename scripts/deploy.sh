#!/usr/bin/env bash
#
# The remote runtime stack (getting_started/infra.md): a Kitaru/ZenML server on GCE, a Modal orchestrator stack,
# GCS artifacts, Artifact Registry images. One command per lifecycle verb:
#
#   scripts/deploy.sh up       provision everything (safe to re-run: every step checks first)
#   scripts/deploy.sh update   re-apply the mutable parts (secrets, stack, image base)
#   scripts/deploy.sh down     delete everything it created
#   scripts/deploy.sh status   what exists right now
#
# Credentials it expects you to bring:
#   * gcloud   — an authenticated human account with roles/owner on $PROJECT
#   * modal    — ~/.modal.toml (`modal token set`), the tokens the orchestrator submits with
#   * .env     — decode's own config surface; mirrored into the decode-$DECODE_ENV bucket
#
# It writes two files under ~/.config/decode and nothing else outside GCP/Modal/Kitaru:
#   decode-kitaru-key.json     the runtime SA key the stack authenticates GCS + AR with
#   kitaru-admin-password      the server's admin password (generated once)
#   kitaru-api-key             the decode-runner service-account key the CLI logs in with
set -euo pipefail

PROJECT="${PROJECT:-coding-agent-course}"
REGION="${REGION:-europe-west2}"
ZONE="${ZONE:-europe-west2-a}"
DECODE_ENV="${DECODE_ENV:-prod}"

SA_NAME="decode-kitaru"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
BUCKET="gs://${PROJECT}-kitaru"
AR_REPO="kitaru-images"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}"
VM="kitaru-server"
IP_NAME="kitaru-server-ip"
FIREWALL="allow-kitaru-tls"       # 80 (ACME) + 443 (TLS). :8080 is never exposed.
LEGACY_FIREWALL="allow-kitaru"    # the pre-TLS rule that opened :8080 — deleted on `up`
DENY_FIREWALL="deny-kitaru-plaintext"   # priority 100: beats default-allow-internal on :8080
STACK="prod-modal"

# Pin the server image to the client: `kitaru --version` and the container must move together.
KITARU_VERSION="${KITARU_VERSION:-0.18.0}"
SERVER_IMAGE="zenmldocker/kitaru:${KITARU_VERSION}"

# The flow image's base. Pulled for linux/amd64 explicitly — see preflight_docker.
FLOW_BASE_IMAGE="python:3.12-slim"

CONFIG_DIR="${HOME}/.config/decode"
SA_KEY="${CONFIG_DIR}/decode-kitaru-key.json"
ADMIN_PASSWORD_FILE="${CONFIG_DIR}/kitaru-admin-password"
API_KEY_FILE="${CONFIG_DIR}/kitaru-api-key"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[31mxx\033[0m  %s\n' "$*" >&2; exit 1; }
have() { gcloud "$@" >/dev/null 2>&1; }

server_ip() {
  gcloud compute addresses describe "${IP_NAME}" --region="${REGION}" --project="${PROJECT}" \
    --format='value(address)' 2>/dev/null || true
}

# Let's Encrypt will not issue for a bare IP, and the server must be reachable from Modal — so it
# needs a name. nip.io resolves <ip>.nip.io to <ip> with no DNS to own, which is enough for ACME.
#
# No server yet (or just torn down) → prints nothing and still SUCCEEDS. A trailing
# ``[ -n "$ip" ] && printf …`` would return 1 there, and under ``set -e`` the caller's
# ``url="$(kitaru_url)"`` would kill the script — silently, in exactly the empty state ``status`` is
# for.
server_domain() {
  local ip; ip="$(server_ip)"
  [ -n "${ip}" ] || return 0
  printf '%s.nip.io' "${ip}"
}

kitaru_url() {
  local domain; domain="$(server_domain)"
  [ -n "${domain}" ] || return 0
  printf 'https://%s' "${domain}"
}

# ---------------------------------------------------------------------------- preflight

preflight_docker() {
  docker info >/dev/null 2>&1 || die "docker is not running — the flow image is built locally."

  # Docker Desktop's containerd image store (the default since 4.34) pushes an OCI manifest whose
  # layers keep Docker media types. Modal unpacks images with umoci, which validates strictly
  # against the OCI spec and dies on that hybrid: `umoci raw unpack ... exit status 1`.
  if docker info 2>/dev/null | grep -q 'io.containerd.snapshotter'; then
    die "Docker Desktop's containerd image store is ON; Modal cannot unpack the images it pushes.
    Turn it off: Settings → General → uncheck 'Use containerd for pulling and storing images',
    then Apply & Restart. (Or set UseContainerdSnapshotter=false in
    ~/Library/Group Containers/group.com.docker/settings-store.json and run: docker desktop restart)"
  fi

  # With containerd off, the classic builder cannot cross-build unless the base image's amd64
  # manifest is already local: it fails with 'platform (linux/arm64/v8) does not match'. Modal is
  # x86-64 only, so on Apple Silicon this pull is what makes the flow image runnable at all.
  log "pulling ${FLOW_BASE_IMAGE} for linux/amd64 (Modal runs x86-64)"
  docker pull --platform linux/amd64 "${FLOW_BASE_IMAGE}" >/dev/null
}

preflight() {
  command -v gcloud >/dev/null || die "gcloud not found"
  command -v uv >/dev/null || die "uv not found"
  gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
    || die "no active gcloud account — run: gcloud auth login"
  [ -f "${HOME}/.modal.toml" ] || die "no ~/.modal.toml — run: modal token set"
  mkdir -p "${CONFIG_DIR}"
}

# ---------------------------------------------------------------------------- §1-2 GCP

ensure_apis() {
  log "enabling APIs"
  gcloud services enable compute.googleapis.com artifactregistry.googleapis.com \
    storage.googleapis.com --project="${PROJECT}" --quiet
}

ensure_service_account() {
  if have iam service-accounts describe "${SA}" --project="${PROJECT}"; then
    log "service account ${SA_NAME} exists"
  else
    log "creating service account ${SA_NAME}"
    gcloud iam service-accounts create "${SA_NAME}" --project="${PROJECT}" \
      --display-name="decode Kitaru stack (GCS artifacts + AR images)"
  fi

  # Project-wide: objects in any bucket, images in any repo. Deliberately not storage.admin.
  for role in roles/storage.objectAdmin roles/artifactregistry.writer; do
    gcloud projects add-iam-policy-binding "${PROJECT}" \
      --member="serviceAccount:${SA}" --role="${role}" --condition=None --quiet >/dev/null
  done

  if [ -f "${SA_KEY}" ]; then
    log "SA key already at ${SA_KEY}"
  else
    log "creating SA key"
    # Orgs created since mid-2024 block this by default. The key is still the right call: Modal is
    # foreign compute with no GCP identity, so something portable must reach it. Exempt only this
    # project (needs roles/orgpolicy.policyAdmin on the org):
    #   gcloud resource-manager org-policies disable-enforce \
    #     iam.disableServiceAccountKeyCreation --project=${PROJECT}
    ( umask 077 && gcloud iam service-accounts keys create "${SA_KEY}" --iam-account="${SA}" ) \
      || die "SA key creation failed — see the org-policy note in getting_started/infra.md §1"
  fi
}

ensure_bucket() {
  if have storage buckets describe "${BUCKET}"; then
    log "bucket ${BUCKET} exists"
  else
    log "creating bucket ${BUCKET}"
    gcloud storage buckets create "${BUCKET}" --project="${PROJECT}" --location="${REGION}"
  fi

  # ZenML's GCS connector calls get_bucket() every time it mints credentials for the artifact
  # store — not just on `verify`. roles/storage.objectAdmin has no storage.buckets.get, so object
  # I/O alone is not enough and the stack 403s on submit. Scoped to this ONE bucket, not the project.
  log "granting bucket-scoped storage.admin to ${SA_NAME}"
  gcloud storage buckets add-iam-policy-binding "${BUCKET}" \
    --member="serviceAccount:${SA}" --role=roles/storage.admin >/dev/null
}

ensure_registry() {
  if have artifacts repositories describe "${AR_REPO}" --location="${REGION}" --project="${PROJECT}"; then
    log "registry ${AR_REPO} exists"
  else
    log "creating registry ${AR_REPO}"
    gcloud artifacts repositories create "${AR_REPO}" --project="${PROJECT}" \
      --repository-format=docker --location="${REGION}"
  fi
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null
}

# ---------------------------------------------------------------------------- §3 server

# The server container runs as UID 1000, but the mounted host dir /var/kitaru is created root-owned —
# so the very first boot dies with `sqlite3.OperationalError: unable to open database file` and
# crash-loops forever. The single highest-value repair in this whole script.
#
# Two things it must get right, both learned the hard way:
#
#   POLL, never a fixed sleep. /var/kitaru does not exist until konlet starts the container, and sshd
#   is not up before that either — a `sleep 45` then `chown` raced both and printed `chown: cannot
#   access '/var/kitaru'`. That miss was a mere warning, so `up` marched on and failed ten minutes
#   later at the TLS health check, nowhere near the cause.
#
#   Run on EVERY `up`, not only when the VM is created. A crash-looping server is exactly the state
#   you re-run `up` to repair, and the create-only version skipped the repair precisely then — the
#   fix had to be applied by hand.
#
# Idempotent: already-1000 exits 0 and does NOT restart a healthy server. Exit 3 means the container
# has not created the mount yet — keep polling.
ensure_store_ownership() {
  # The remote script speaks in exit codes, because that is all `gcloud compute ssh` carries back:
  #   0  already UID 1000 — nothing to do, and NO restart of a healthy server
  #   10 chowned + restarted
  #   3  the container has not created the mount yet — poll again
  local attempt status
  for attempt in $(seq 1 20); do
    status=0
    gcloud compute ssh "${VM}" --zone="${ZONE}" --project="${PROJECT}" --quiet --command="\
      test -d /var/kitaru || exit 3
      [ \"\$(stat -c %u /var/kitaru 2>/dev/null || echo unknown)\" = 1000 ] && exit 0
      sudo chown -R 1000:1000 /var/kitaru && \
      sudo docker restart \$(sudo docker ps -aqf name=klt-${VM}) >/dev/null && exit 10" \
      2>/dev/null || status=$?
    case "${status}" in
      0)  log "/var/kitaru already owned by UID 1000"; return 0 ;;
      10) log "chowned /var/kitaru to UID 1000, restarted the server (attempt ${attempt})"; return 0 ;;
    esac
    sleep 15
  done
  die "could not chown /var/kitaru after 5 min — the server will crash-loop on
    'sqlite3.OperationalError: unable to open database file'. Fix it by hand, then re-run \`up\`:
      gcloud compute ssh ${VM} --zone=${ZONE} --command='sudo chown -R 1000:1000 /var/kitaru'"
}

ensure_server() {
  if ! have compute addresses describe "${IP_NAME}" --region="${REGION}" --project="${PROJECT}"; then
    log "reserving static IP"
    gcloud compute addresses create "${IP_NAME}" --project="${PROJECT}" --region="${REGION}"
  fi
  local ip; ip="$(gcloud compute addresses describe "${IP_NAME}" --region="${REGION}" \
    --project="${PROJECT}" --format='value(address)')"

  if [ ! -f "${ADMIN_PASSWORD_FILE}" ]; then
    log "generating the server admin password"
    ( umask 077 && openssl rand -base64 24 > "${ADMIN_PASSWORD_FILE}" )
  fi

  if have compute instances describe "${VM}" --zone="${ZONE}" --project="${PROJECT}"; then
    log "VM ${VM} exists"
  else
    log "creating VM ${VM} (${SERVER_IMAGE})"
    gcloud compute instances create-with-container "${VM}" --project="${PROJECT}" \
      --machine-type=e2-small --zone="${ZONE}" \
      --address="${ip}" --tags=kitaru-server \
      --container-image="${SERVER_IMAGE}" \
      --container-env=ZENML_SERVER_AUTO_ACTIVATE=1,ZENML_DEFAULT_USER_NAME=admin,ZENML_DEFAULT_USER_PASSWORD="$(cat "${ADMIN_PASSWORD_FILE}")" \
      --container-mount-host-path=host-path=/var/kitaru,mount-path=/zenml/.zenconfig/local_stores/default_zen_store
  fi
  ensure_store_ownership

  if have compute firewall-rules describe "${FIREWALL}" --project="${PROJECT}"; then
    log "firewall ${FIREWALL} exists"
  else
    warn "The next rule opens tcp:80 and tcp:443 to 0.0.0.0/0. Port 80 is required — Let's Encrypt"
    warn "validates the ACME challenge over it — and Caddy redirects it to HTTPS thereafter. The"
    warn "server must stay reachable from Modal, whose egress IPs cannot be pinned on standard"
    warn "plans, so it cannot be narrowed to your laptop. TLS stops eavesdropping, NOT reachability:"
    warn "the login page remains exposed to the internet, so treat its secrets as rotatable."
    # Approved by a human — interactively when there is a terminal, otherwise by passing
    # ``allow-firewall`` (``up allow-firewall``). A piped/EOF ``read`` would answer this itself, and
    # this is the one step that exposes a port to the internet.
    local reply="${ALLOW_FIREWALL:-}"
    if [ -z "${reply}" ]; then
      [ -t 0 ] || die "no terminal to approve on — re-run as: scripts/deploy.sh up allow-firewall"
      read -r -p "Create the firewall rule? [y/N] " reply
    fi
    [ "${reply}" = "y" ] || die "aborted — the stack cannot work until Modal can reach the server"
    gcloud compute firewall-rules create "${FIREWALL}" --project="${PROJECT}" \
      --allow=tcp:80,tcp:443 --target-tags=kitaru-server \
      --description="Caddy: ACME HTTP-01 challenge (80) + TLS (443)"
  fi

  # :8080 is Caddy's upstream on localhost only. If an older deployment exposed it, close it.
  if have compute firewall-rules describe "${LEGACY_FIREWALL}" --project="${PROJECT}"; then
    log "removing the pre-TLS rule ${LEGACY_FIREWALL} (:8080 no longer needs to be public)"
    gcloud compute firewall-rules delete "${LEGACY_FIREWALL}" --project="${PROJECT}" --quiet
  fi

  # The container listens on 0.0.0.0:8080 (konlet uses host networking; the server has no
  # bind-to-loopback knob), and GCP's default network ships `default-allow-internal`, which permits
  # tcp:0-65535 from 10.128.0.0/9 — so ANY VM in the VPC could read the admin password and every
  # secret off the plaintext port. Priority 100 beats that rule (65534) and any stray allow at the
  # default 1000. Loopback is not subject to VPC firewalls, so Caddy → localhost:8080 is untouched.
  if have compute firewall-rules describe "${DENY_FIREWALL}" --project="${PROJECT}"; then
    log "deny rule ${DENY_FIREWALL} exists"
  else
    log "denying tcp:8080 outright (Caddy is the only front door)"
    gcloud compute firewall-rules create "${DENY_FIREWALL}" --project="${PROJECT}" \
      --direction=INGRESS --priority=100 --action=DENY --rules=tcp:8080 \
      --target-tags=kitaru-server --source-ranges=0.0.0.0/0 \
      --description="Kitaru speaks plaintext on :8080; Caddy is the only front door"
  fi
}

# The Kitaru server ships an HSTS header (`max-age=63072000`) while serving plain HTTP: a browser
# obeys it, upgrades the next request to HTTPS, hits a port that speaks none, and the dashboard dies
# on `Unexpected token 'I' … is not valid JSON` — that string is uvicorn's "Invalid HTTP request
# received." Caddy makes the header true, and encrypts the admin password and every secret in transit.
ensure_tls() {
  local domain; domain="$(server_domain)"

  log "installing Caddy in front of the server (TLS for ${domain})"
  gcloud compute ssh "${VM}" --zone="${ZONE}" --project="${PROJECT}" --quiet --command="
    sudo mkdir -p /var/caddy/data /var/caddy/config
    sudo tee /var/caddy/Caddyfile >/dev/null <<'CADDY'
${domain} {
	reverse_proxy localhost:8080
}
CADDY
    sudo docker rm -f caddy >/dev/null 2>&1 || true
    sudo docker run -d --name caddy --restart=always --network host \
      -v /var/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
      -v /var/caddy/data:/data \
      -v /var/caddy/config:/config \
      caddy:2-alpine >/dev/null
  " || die "could not install Caddy over SSH"

  local url; url="$(kitaru_url)"
  log "waiting for ${url}/health (first boot runs DB migrations; ACME retries every 60s)"
  for _ in $(seq 1 60); do
    if curl -sf -m 8 "${url}/health" >/dev/null 2>&1; then log "server up over TLS"; return 0; fi
    sleep 10
  done
  die "no healthy TLS endpoint — check: gcloud compute ssh ${VM} --zone=${ZONE} --command='sudo docker logs caddy'"
}

# ---------------------------------------------------------------------------- §4 login + stack

# `kitaru login` only takes --api-key or an interactive browser flow, and an API key needs a login
# to create — a chicken-and-egg for a script. The server's REST API breaks it: an OAuth2 password
# grant with the admin password mints a JWT, which creates the decode-runner service account and
# its key. Values never touch stdout.
bootstrap_api_key() {
  local url="$1"
  log "bootstrapping the decode-runner service account + API key"
  ADMIN_PASSWORD_FILE="${ADMIN_PASSWORD_FILE}" API_KEY_FILE="${API_KEY_FILE}" \
    KITARU_URL="${url}" python3 "${REPO_ROOT}/scripts/kitaru_bootstrap_api_key.py"
}

ensure_login() {
  local url; url="$(kitaru_url)"
  [ -n "${url}" ] || die "no server IP — run: scripts/deploy.sh up"

  [ -f "${API_KEY_FILE}" ] || bootstrap_api_key "${url}"

  log "logging in to ${url}"
  if ! ( cd "${REPO_ROOT}" && uv run kitaru login "${url}" --api-key "$(cat "${API_KEY_FILE}")" >/dev/null 2>&1 ); then
    # A key from a previous server is worthless — the DB it lived in is gone. Mint a fresh one.
    warn "the stored API key was rejected; minting a new one"
    rm -f "${API_KEY_FILE}"
    bootstrap_api_key "${url}"
    ( cd "${REPO_ROOT}" && uv run kitaru login "${url}" --api-key "$(cat "${API_KEY_FILE}")" >/dev/null ) \
      || die "kitaru login failed against ${url}"
  fi
}

# `kitaru stack create --type` offers local/kubernetes/vertex/sagemaker/azureml — there is NO modal
# stack type. Modal ships as a ZenML *integration* (orchestrator + sandbox flavors), so the stack is
# assembled with the zenml CLI, which Kitaru reads from the same server.
ensure_stack() {
  cd "${REPO_ROOT}"
  local zen=(uv run --group remote zenml)

  if "${zen[@]}" stack describe "${STACK}" >/dev/null 2>&1; then
    log "stack ${STACK} exists"
  else
    log "registering stack components"
    "${zen[@]}" service-connector register gcp-decode --type gcp --auth-method service-account \
      --project_id="${PROJECT}" --service_account_json=@"${SA_KEY}" >/dev/null 2>&1 || true

    "${zen[@]}" artifact-store register gcs-kitaru --flavor=gcp \
      --path="${BUCKET}/kitaru" >/dev/null 2>&1 || true
    "${zen[@]}" artifact-store connect gcs-kitaru --connector gcp-decode \
      --resource-id "${BUCKET}" >/dev/null

    "${zen[@]}" container-registry register ar-kitaru --flavor=gcp \
      --uri="${REGISTRY}" >/dev/null 2>&1 || true
    "${zen[@]}" container-registry connect ar-kitaru --connector gcp-decode \
      --resource-id "${REGISTRY}" >/dev/null

    # Both timeouts are billing caps: a Modal sandbox bills until it exits or its TTL fires.
    "${zen[@]}" orchestrator register modal-orch --flavor=modal \
      --timeout=7200 --synchronous=true >/dev/null 2>&1 || true
    "${zen[@]}" sandbox register modal-sandbox --flavor=modal --timeout=1800 >/dev/null 2>&1 || true

    log "registering stack ${STACK}"
    "${zen[@]}" stack register "${STACK}" \
      -o modal-orch -a gcs-kitaru -c ar-kitaru -sb modal-sandbox >/dev/null
  fi

  # `init` BEFORE `use`, and the order is load-bearing. `kitaru init` writes .kitaru/config.yaml with
  # its own `active_stack_id: <default>`, so running it after `stack use` silently reverts the
  # selection — on a FRESH deploy only (later `up`s skip init, so the bug hides on every re-run).
  #
  # Pins the source root too. Without it ZenML infers it from the entrypoint script — for `uv run
  # decode` that is .venv/bin, and the code archive uploads empty.
  [ -d "${REPO_ROOT}/.kitaru" ] || uv run kitaru init >/dev/null
  uv run kitaru stack use "${STACK}" >/dev/null
  log "stack ${STACK} active"
}

# ---------------------------------------------------------------------------- §5 secrets

ensure_secrets() {
  cd "${REPO_ROOT}"
  [ -f .env ] || die ".env not found — decode's config surface is mirrored from it"

  # MODAL_TOKEN_* are the modal CLI's tokens, not Settings fields, so sync_secrets.py skips them and
  # the Environment Bucket never carries them. The flow container still needs them in process env to
  # spawn its own bash sandboxes, and ImageSettings.secret_environment_from is their only route in.
  log "writing the decode-modal secret (Modal tokens for the flow container)"
  ( set -a; . ./.env; set +a
    uv run kitaru secrets set decode-modal --private \
      "--MODAL_TOKEN_ID=${MODAL_TOKEN_ID:?MODAL_TOKEN_ID missing from .env}" \
      "--MODAL_TOKEN_SECRET=${MODAL_TOKEN_SECRET:?MODAL_TOKEN_SECRET missing from .env}" >/dev/null )

  log "mirroring .env → the decode-${DECODE_ENV} Environment Bucket"
  uv run python scripts/sync_secrets.py --env "${DECODE_ENV}" --yes
}

# ---------------------------------------------------------------------------- verbs

cmd_up() {
  preflight
  preflight_docker
  ensure_apis
  ensure_service_account
  ensure_bucket
  ensure_registry
  ensure_server
  ensure_tls
  ensure_login
  ensure_stack
  ensure_secrets
  cmd_status
  cat <<EOF

Kick off a headless agent:

  make run-remote TASK="explain what this repo does"
  make run-remote TASK="fix the failing test" REPO=https://github.com/you/your-repo.git

Operate a live run from anywhere the server is reachable:

  uv run kitaru executions list
  uv run kitaru executions logs <exec_id> --follow
EOF
}

cmd_update() {
  preflight
  preflight_docker
  ensure_tls        # re-applies the Caddyfile and restarts Caddy; the cert volume survives
  ensure_login
  ensure_stack
  ensure_secrets
  log "updated — the next run rebuilds the flow image if the code or deps changed"
}

cmd_down() {
  preflight
  warn "This deletes the VM (and with it every execution record + the ${DECODE_ENV} secrets), its IP,"
  warn "the firewall rules, the bucket (and every artifact in it), the image registry, and the"
  warn "runtime service account + its key."
  # The confirmation is the project name, typed by a human — interactively when there is a terminal,
  # otherwise as the argument (``deploy.sh down <project>``), because a piped/EOF ``read`` would
  # silently answer the prompt itself and turn the gate into decoration.
  local reply="${1:-}"
  if [ -z "${reply}" ]; then
    [ -t 0 ] || die "no terminal to confirm on — re-run as: scripts/deploy.sh down ${PROJECT}"
    read -r -p "Type the project name to confirm [${PROJECT}]: " reply
  fi
  [ "${reply}" = "${PROJECT}" ] || die "aborted"

  gcloud compute instances delete "${VM}" --zone="${ZONE}" --project="${PROJECT}" --quiet || true
  gcloud compute firewall-rules delete "${FIREWALL}" --project="${PROJECT}" --quiet || true
  gcloud compute firewall-rules delete "${LEGACY_FIREWALL}" --project="${PROJECT}" --quiet || true
  gcloud compute firewall-rules delete "${DENY_FIREWALL}" --project="${PROJECT}" --quiet || true
  gcloud compute addresses delete "${IP_NAME}" --region="${REGION}" --project="${PROJECT}" --quiet || true
  gcloud storage rm -r "${BUCKET}" || true
  gcloud artifacts repositories delete "${AR_REPO}" --location="${REGION}" --project="${PROJECT}" --quiet || true
  gcloud iam service-accounts delete "${SA}" --project="${PROJECT}" --quiet || true  # takes the key with it

  # Modal, the other half of the stack: the orchestrator App a flow container runs in, and the App its
  # nested bash sandboxes attach to — one of each per environment (runtime/modal_app.py,
  # sandbox/modal_backend.py). Idle Apps bill nothing, but a teardown that leaves them running is not
  # a teardown. Stopping is permanent; the next ``up`` creates them again on first submit.
  local app
  for app in "decode-${DECODE_ENV}" "decode-sandbox-${DECODE_ENV}"; do
    if (cd "${REPO_ROOT}" && uv run modal app stop "${app}" -y) >/dev/null 2>&1; then
      log "stopped modal app ${app}"
    fi
  done

  rm -f "${SA_KEY}" "${ADMIN_PASSWORD_FILE}" "${API_KEY_FILE}"
  log 'torn down. The local kitaru client still points at a dead server — log in again when you rebuild.'
}

cmd_status() {
  local url vm
  url="$(kitaru_url)"
  vm="$(gcloud compute instances describe "${VM}" --zone="${ZONE}" --project="${PROJECT}" \
    --format='value(status)' 2>/dev/null || echo '<none>')"
  printf '\n'
  printf 'project    %s\n' "${PROJECT}"
  printf 'server     %s' "${url:-<none>}"
  if [ -n "${url}" ] && curl -sf -m 5 "${url}/health" >/dev/null 2>&1; then
    printf '  (healthy)\n'
  else
    printf '  (unreachable)\n'
  fi
  printf 'bucket     %s\n' "$(have storage buckets describe "${BUCKET}" && echo "${BUCKET}" || echo '<none>')"
  printf 'registry   %s\n' "$(have artifacts repositories describe "${AR_REPO}" --location="${REGION}" --project="${PROJECT}" && echo "${REGISTRY}" || echo '<none>')"
  printf 'vm         %s\n' "${vm}"
  printf 'firewall   %s\n' "$(have compute firewall-rules describe "${FIREWALL}" --project="${PROJECT}" && echo "${FIREWALL} (tcp:80,443 → 0.0.0.0/0)" || echo '<none>')"
  printf 'legacy     %s\n' "$(have compute firewall-rules describe "${LEGACY_FIREWALL}" --project="${PROJECT}" && echo "${LEGACY_FIREWALL} (tcp:8080 → 0.0.0.0/0) — PLAINTEXT, delete it" || echo 'none (:8080 not exposed)')"
  # The missing DENY rule only matters while a server is listening on :8080 — without one, warning
  # about it reads as an open hole on a machine that does not exist.
  if [ "${vm}" = '<none>' ]; then
    printf 'plaintext  %s\n' 'n/a (no server)'
  else
    printf 'plaintext  %s\n' "$(have compute firewall-rules describe "${DENY_FIREWALL}" --project="${PROJECT}" && echo 'denied (tcp:8080 DENY @ priority 100)' || echo 'NOT DENIED — any VM in the VPC can read :8080 in the clear')"
  fi
  printf 'tls        %s\n' "$(curl -sf -m 8 "${url}/health" >/dev/null 2>&1 && echo "Let's Encrypt, valid" || echo '<no cert>')"
  printf 'stack      %s\n' "$(cd "${REPO_ROOT}" && uv run kitaru stack current 2>/dev/null \
    | awk -F': ' '/Active stack:/ {print $2; exit}' || echo '<none>')"
  printf '\n'
}

case "${1:-}" in
  up)     if [ "${2:-}" = "allow-firewall" ]; then ALLOW_FIREWALL=y; fi   # `&&` here would trip `set -e`
          cmd_up ;;
  update) cmd_update ;;
  down)   cmd_down "${2:-}" ;;
  status) cmd_status ;;
  *)      die "usage: scripts/deploy.sh {up [allow-firewall]|update|down [<project>]|status}" ;;
esac
