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
  -d '{"username":"decode-kitaru-worker","description":"Modal-hosted Kitaru Worker (ADR-0020 §5)"}'
KEY=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  https://cloudapi.zenml.io/organizations/$ORG/service_accounts/decode-kitaru-worker/api_keys \
  -d '{"name":"modal-worker","expires_in_minutes":43200}' | python3 -c "import sys,json;print(json.load(sys.stdin)['key'])")

# 3. both Modal Secrets, per environment (--force replaces the WHOLE surface — pass every key again;
#    ADR-0021 §2: NO DECODE_ENV in either Secret — the deployment is named after it instead)
set -a && . ./.env && set +a
uv run modal secret create decode-headless-local LLM_PROVIDER="${LLM_PROVIDER:-gemini}" \
  GEMINI_API_KEY="$GEMINI_API_KEY" KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KEY" KITARU_AGENT_ID=01a02523-1097-77e1-aa74-c64e7593050b SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force
uv run modal secret create decode-kitaru-worker-local LLM_PROVIDER="${LLM_PROVIDER:-gemini}" \
  GEMINI_API_KEY="$GEMINI_API_KEY" KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KEY" --force                                   # deliberately NO KITARU_AGENT_ID

# 4. verify — a recorded Modal run, a live Modal worker, one replay (redeploy: a --force create
#    writes a NEW Secret object the running deployment is not bound to)
uv run decode remote deploy
uv run decode remote run "say hello" --sandbox-mode none
uv run kitaru session list --agent decode --origin recorded --size 3
uv run modal deploy scripts/modal_kitaru_worker.py
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 --agent-version-id <NEW_VERSION_ID>
uv run kitaru worker list                                           # want: decode-modal-worker, live: True
uv run kitaru replay create <RECORDED_SESSION_ID> --agent decode@3 --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' --evaluate-baselines
uv run kitaru job watch <JOB_ID>                                    # want: a terminal state
```

`modal secret` has no `get`: a Secret is the container's whole config surface, so pass **every** key it
needs on every create (`LLM_PROVIDER`, `GEMINI_MODEL`, …).

The Agent Version must be **re-registered** first — ADR-0021 §4 dropped `DECODE_ENV` from the run spec, and
versions are immutable: `uv run python scripts/register_kitaru_agent.py --sandbox-mode none --skip-bin-check
…`, then pin the new id above and in `--agent decode@<N>`.

Then the paper trail:

- 07 §2b and §3: delete the two "Not minted yet — tasks/153" notes.
- tasks/done/142, 143, 145: flip any [HUMAN] box that was waiting on this key.
- tasks/done/151: flip the cron [HUMAN] box once you deploy with `DECODE_NIGHTLY_CRON` set (04 §5)
  — that one starts a recurring paid job, which is why it is yours to pull.

## Acceptance Criteria

- [ ] [HUMAN] a Modal headless run is listed as a recorded Kitaru Session.
- [ ] [HUMAN] `kitaru worker list` shows the Modal worker live; a replay on the re-registered version
      reaches a terminal state.
- [ ] 07 carries no "not minted yet" note.

## Log
### [PA] 2026-09-02 20:00 — Filed
Split out of the article-6 audit; blocked on an operator-only credential write.

### [SWE] 2026-09-09 — Scope narrowed by ADR-0021
The Environment Bucket is deleted, so this key no longer gates headless runs: a Modal container reads
its config from its own Secret and imports no kitaru unless recording is configured. Commands updated
for the `-<env>` suffixed Secrets, and for the Agent Version re-registration ADR-0021 §4 forces.
