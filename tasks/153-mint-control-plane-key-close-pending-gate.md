---
status: pending
feature: remote-headless-article-6
---

# Mint the `ZENPROKEY_…` control plane key and close 07_infra's pending gate

Tags: `infra`, `[HUMAN]`
Depends on: None
Blocks: —

07 §2b ("⏳ Pending gate"): without a control plane key in a Modal Secret, a Modal headless run
degrades to *unrecorded* (exit 0 — recording is an observer) and the Modal-hosted Kitaru Worker
refuses to start. Minting is an org-level ZenML Pro write with the operator's own device credential
— not something an agent session does (the permission classifier blocks reading
`~/.config/kitaru/credentials.json`).

**Scope narrowed by [ADR-0021](../docs/adr/0021-decode-env-is-a-naming-suffix.md)** (2026-09-09): this
key no longer gates headless runs *at all*. The Environment Bucket is deleted, so a container reads
its config from its own Modal Secret and never touches a Kitaru workspace unless recording is on.
What is left blocked: recording a remote run, and the Kitaru Worker.

## Scope (operator)

The exact commands (values only ever from your shell; nothing echoed, nothing committed):

```bash
# 1. one-hour automation token from the kitaru device credential, then the org id
TOKEN=$(curl -s -H "Authorization: Bearer $(python3 -c "import json;print(json.load(open('$HOME/.config/kitaru/credentials.json'))['https://cloudapi.zenml.io']['api_token'])")" \
        https://cloudapi.zenml.io/auth/api_token | tr -d '"')
ORG=$(curl -s -H "Authorization: Bearer $TOKEN" https://cloudapi.zenml.io/organizations \
      | python3 -c "import sys,json;d=json.load(sys.stdin);print((d if isinstance(d,list) else d['items'])[0]['id'])")

# 2. service account (409 = already exists, fine) + an expiring, revocable key
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts \
  -d '{"username":"decode-headless","description":"Modal Headless App recording (ADR-0020 §4)"}'
KEY=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts/decode-headless/api_keys \
  -d '{"name":"modal-headless","expires_in_minutes":43200}' | python3 -c "import sys,json;print(json.load(sys.stdin)['key'])")

# 3. the Modal Secret, per environment (--force replaces the WHOLE surface — pass every key again;
#    ADR-0021 §2: NO DECODE_ENV in the Secret — the deployment is named after it instead)
set -a && . ./.env && set +a
uv run modal secret create decode-headless-local LLM_PROVIDER="${LLM_PROVIDER:-gemini}" \
  GEMINI_API_KEY="$GEMINI_API_KEY" KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KEY" KITARU_AGENT_ID=01a02523-1097-77e1-aa74-c64e7593050b SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force

# 4. verify — a recorded Modal run (redeploy: a --force create writes a NEW Secret object the
#    running deployment is not bound to)
uv run decode remote deploy
uv run decode remote run "say hello" --sandbox-mode none
uv run kitaru session list --agent decode --origin recorded --size 3
```

`modal secret` has no `get`: a Secret is the container's whole config surface, so pass **every** key it
needs on every create (`LLM_PROVIDER`, `GEMINI_MODEL`, …).

Then the paper trail:

- tasks/done/142, 143: flip any [HUMAN] box that was waiting on this key.
- tasks/done/151: flip the cron [HUMAN] box once you deploy with `DECODE_NIGHTLY_CRON` set (04 §5)
  — that one starts a recurring paid job, which is why it is yours to pull.

## Acceptance Criteria

- [ ] [HUMAN] a Modal headless run is listed as a recorded Kitaru Session.

## Log
### [PA] 2026-09-02 20:00 — Filed
Split out of the article-6 audit; blocked on an operator-only credential write.

### [SWE] 2026-09-09 — Scope narrowed by ADR-0021
The Environment Bucket is deleted, so this key no longer gates headless runs: a Modal container reads
its config from its own Secret and imports no kitaru unless recording is configured. Commands updated
for the `-<env>` suffixed Secrets, and for the Agent Version re-registration ADR-0021 §4 forces.

### [PA] 2026-09-11 — Note
One command edited, nothing else: the re-registration step named `scripts/register_kitaru_agent.py`,
which ADR-0022 §11 deleted — `scripts/bootstrap_kitaru.py` replaced it (it registers the agent, BOTH
Agent Versions, the `opik` importer and every `evaluators/*.py` on one server, idempotently), so the
step as written could not be run. Its `--sandbox-mode` / `--skip-bin-check` flags went with the old
script: the replacement takes `--server` and registers whatever is missing. Flagged, deliberately NOT changed here because this task belongs to another
feature: the `--evaluate-baselines` flag in the replay command above was renamed to
`--baseline-evaluation-mode` in kitaru 0.25 (see `running_the_code/06_evals_replays.md` §5), and the
version numbers `decode@3` are the course workspace's — a freshly bootstrapped server registers the same
two specs as `decode@1` (docker) / `decode@2` (`none`).

### [SWE] 2026-09-15 — Scope narrowed by ADR-0023
The Modal-hosted Kitaru Worker is deleted (Workers run only on the operator's machine), and
`07_evals_replays_deploy.md` with it. This key now gates one thing: recording a Modal headless run into
the managed workspace. Dropped the worker Secret, the worker deploy/replay verification, its acceptance
criterion and the 07 paper-trail step; the service account is renamed `decode-headless` to match.
