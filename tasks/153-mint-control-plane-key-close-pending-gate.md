---
status: pending
feature: remote-headless-article-6
---

# Mint the `ZENPROKEY_…` control plane key and close 07_infra's pending gate

Tags: `infra`, `[HUMAN]`
Depends on: None
Blocks: —

07_infra §1 ("⏳ Pending gate"): without a control plane key in the two Modal Secrets, every Modal
headless run degrades to unrecorded and the Modal-hosted Kitaru Worker refuses to start. Minting is
an org-level ZenML Pro write with the operator's own device credential — not something an agent
session does (the permission classifier blocks reading `~/.config/kitaru/credentials.json`).

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

# 3. both Modal Secrets (--force replaces the WHOLE surface — pass every key again)
set -a && . ./.env && set +a
uv run modal secret create decode-headless GEMINI_API_KEY="$GEMINI_API_KEY" KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KEY" KITARU_AGENT_ID=01a02523-1097-77e1-aa74-c64e7593050b SANDBOX_GIT_TOKEN="$SANDBOX_GIT_TOKEN" --force
uv run modal secret create decode-kitaru-worker GEMINI_API_KEY="$GEMINI_API_KEY" KITARU_API_URL="$KITARU_API_URL" \
  KITARU_API_KEY="$KEY" --force                                   # deliberately NO KITARU_AGENT_ID

# 4. verify — a recorded Modal run, a live Modal worker, one decode@3 replay
uv run modal run scripts/modal_headless.py::main --task "say hello" --sandbox-mode none
uv run kitaru session list --agent decode --origin recorded --size 3
uv run modal deploy scripts/modal_kitaru_worker.py
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 --agent-version-id 01a029bf-0ae3-7de1-b594-4bc71a7ba91a
uv run kitaru worker list                                           # want: decode-modal-worker, live: True
uv run kitaru replay create <RECORDED_SESSION_ID> --agent decode@3 --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' --evaluate-baselines
uv run kitaru job watch <JOB_ID>                                    # want: a terminal state
```

`modal secret` has no `get`: if the existing `decode-headless` secret carried keys beyond the 07_infra §1
table (`LLM_PROVIDER`, `GEMINI_MODEL`, …), pass them again in step 3.

Then the paper trail:

- 07_infra §1 and §3: delete the two "Not minted yet — tasks/153" notes.
- tasks/done/142, 143, 145: flip any [HUMAN] box that was waiting on this key.
- tasks/done/151: flip the cron [HUMAN] box once you deploy with `DECODE_NIGHTLY_CRON` set (07_infra §2c)
  — that one starts a recurring paid job, which is why it is yours to pull.

## Acceptance Criteria

- [ ] [HUMAN] a Modal headless run is listed as a recorded Kitaru Session.
- [ ] [HUMAN] `kitaru worker list` shows the Modal worker live; a `decode@3` replay reaches a terminal state.
- [ ] 07_infra carries no "not minted yet" note.

## Log
### [PA] 2026-09-02 20:00 — Filed
Split out of the article-6 audit; blocked on an operator-only credential write.
