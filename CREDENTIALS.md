# Credentials — one config surface, two injection mechanisms

`Settings` ([`config/settings.py`](src/decode/config/settings.py)) is the **single source of truth** for
every credential decode holds. Nothing else reads one. So there is only ever one interesting question —
**how does a value get *into* `Settings`, and who is it hidden from?**

Two mechanisms answer it, and they are not alternatives: one fills the config surface, the other hides a
credential from the sandbox. They compose.

| | **Environment Bucket** (config hydration) | **Sandbox Credential Proxy** (header injection) |
|---|---|---|
| What it is | a settings **source** — one `kitaru.get_secret()` call | a **mitmproxy sidecar** container |
| What it moves | your whole config surface (provider key, model, every tuning knob) | one tool credential (e.g. a GitHub PAT) on one host |
| Hidden from | the Kitaru flow payload + checkpoints; `os.environ` (so a model-chosen `bash` can't read it) | the model and the Worker container |
| Where the value lands | the harness process' `Settings` — **never** `os.environ` | the proxy container's env; the header is added *after* egress |
| Knob | `DECODE_ENV` (`local` \| `dev` \| `staging` \| `prod`) | `SANDBOX_CREDENTIAL_PROXY_ENABLED` (or a non-empty `SANDBOX_GIT_TOKEN`) |
| Needs | a Kitaru secret named `decode-<env>` — write it with `make sync-secrets ENV=<env>` | a Docker daemon |
| Where it works | **both surfaces** — TUI *and* headless (`decode run` / `decode replay`) | headless **+** `SANDBOX_MODE=docker` only |
| Code | [`config/settings.py`](src/decode/config/settings.py) `EnvironmentBucketSettingsSource`, [`scripts/sync_secrets.py`](scripts/sync_secrets.py) | [`sandbox/proxy.py`](src/decode/sandbox/proxy.py), [`sandbox/proxy_addon.py`](src/decode/sandbox/proxy_addon.py) |
| ADR | [0015](docs/adr/0015-environment-bucket-secrets.md) | [0011 §6](docs/adr/0011-sandboxing-and-credential-proxy.md), [0012 §10](docs/adr/0012-isolated-workspace.md), amended by [0015 §6](docs/adr/0015-environment-bucket-secrets.md) |

**How much of this is Kitaru?** Exactly one line of it. The Environment Bucket **is** a Kitaru secret, and
that settings source is **the only `get_secret` seam in the whole codebase** ([ADR-0015 §6](docs/adr/0015-environment-bucket-secrets.md)).
The Credential Proxy has none: it is plain docker + mitmproxy, and its rules resolve from the already-hydrated
`Settings` — whichever mechanism filled it. (`MODAL_PROXY_TOKEN_ID` / `_SECRET` are a third thing wearing the
word "proxy": Modal's own endpoint-auth headers, unrelated to both.)

The rest of this file is a manual e2e tutorial. Every case is an **A/B**: the same command with one thing
flipped, and a different observable. Part 5 is an automated backstop that proves the same claims with no
network, no PAT, and no Kitaru.

## 0. Prerequisites

```bash
cp .env.example .env                          # set GEMINI_API_KEY
docker info >/dev/null && echo "docker ok"    # Part 2 (the Credential Proxy) only
uv run kitaru secrets list                    # Parts 1b+ and 3 only: must print a (possibly empty) list, not hang
```

Part 1a needs **none** of that — at `DECODE_ENV=local` (the default) decode never touches Kitaru or Docker.

If `kitaru secrets list` retries `HTTPConnection(host='127.0.0.1', port=8383) … Connection refused`, stored
auth state from an earlier `kitaru login` is pointing at a server that is no longer running. Pick either:

```bash
uv run kitaru logout    # no server at all — the server-less local database (simplest)
uv run kitaru login     # or bring the local server + web dashboard back up on 127.0.0.1:8383
```

On macOS the local Kitaru server can also die mid-run with an ObjC fork-safety abort — the
`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` fix is in the [README](README.md#headless-runtime-decode-run).

## Part 1 — the config surface (`DECODE_ENV`)

> **Clean break — read this if you are coming from an older `.env` ([ADR-0015 §4](docs/adr/0015-environment-bucket-secrets.md)).**
> `RUNTIME_SECRET_NAME`, `RUNTIME_SECRET_STORE_CONFIG` and `RUNTIME_SECRET_STORE_MODEL_KEY` are **deleted** —
> no shim, no deprecation warning, no fail-fast guard. Pydantic's `extra="ignore"` means a stale line in your
> `.env` is now **silently ignored**: decode will start, read your `.env` like any other local run, and never
> tell you the knob did nothing. *This paragraph is the only thing standing between you and that silent no-op.*
> The migration is two commands — `make sync-secrets ENV=<env>` once, then `DECODE_ENV=<env>` on every run.
> Per-credential secrets from the old world (a `decode-llm-creds`, a lone `github-token`) are now dead weight:
> `uv run kitaru secrets delete <name>`.

`DECODE_ENV` decides **where `Settings` gets its values, and nothing else** — not session dirs, not log paths,
not `MEMORY.md`. It is the bootstrap variable, so it is read out-of-band (your `.env` file, overlaid by the
process env) *before* the chain is built.

| `DECODE_ENV` | The source chain (highest first) |
|---|---|
| `local` (default) | process env → **`.env`** → defaults. Kitaru is never imported. |
| `dev` / `staging` / `prod` | process env → **the Environment Bucket** (`decode-<env>`) → defaults. **`.env` is dropped from the chain entirely.** |

### 1a. OFF — `local`, and the invariant that comes with it

The claim: at the default env, decode does not import kitaru at all. It is a one-liner to check, and the same
one-liner is the B side of the A/B:

```bash
uv run python -c "
import sys, decode.cli
print('kitaru imported:', any(m.split('.')[0] == 'kitaru' for m in sys.modules))
from decode.config.settings import settings
print('DECODE_ENV =', settings.decode_env, '| opik project =', settings.opik_project_name)"
# → kitaru imported: False
# → DECODE_ENV = local | opik project = decode-local

DECODE_ENV=staging uv run python -c "
import sys, decode.cli
print('kitaru imported:', any(m.split('.')[0] == 'kitaru' for m in sys.modules))
from decode.config.settings import settings
print('DECODE_ENV =', settings.decode_env, '| opik project =', settings.opik_project_name)"
# → kitaru imported: True
# → DECODE_ENV = staging | opik project = decode-staging
```

Working: `False` at `local`, `True` at a remote env. That second import is the cost of an environment — a
ZenML stack and a network touch before the first prompt — and it is exactly why `local` is the default.
Note the free side-effect: the Opik project follows the environment (`decode-local` / `decode-staging`), so
traces self-sort. Set `OPIK_PROJECT_NAME` explicitly and your value always wins.

`local` reads `.env` and there is nothing to mirror, so the sync script refuses outright:

```bash
make sync-secrets ENV=local
# → Error: `local` reads your .env directly — there is nothing to sync. Pick dev, staging or prod.
```

### 1b. ON — mirror `.env` into the Environment Bucket

The bucket name is **derived** (`decode-<env>`); there is no override knob, so "`DECODE_ENV=staging` pointed at
the prod bucket" is unrepresentable. One command writes it:

```bash
make sync-secrets ENV=staging       # → uv run python scripts/sync_secrets.py --env staging
```

```
Mirroring .env → decode-staging (key names only; values are never printed).
decode-staging does not exist yet — it will be created.
Skipped (not a Settings field): MODAL_TOKEN_ID
  + GEMINI_API_KEY
  + OPENROUTER_API_KEY
This REPLACES the entire contents of decode-staging with these 2 key(s) — `kitaru secrets set` overwrites the whole key set.
Proceed? [y/N]:
```

Read that output — every line of it is a design decision:

- **Key names only, never values** — in the diff (`+` added, `-` removed, `~` changed, `=` unchanged), in the
  confirmation, and even in a kitaru error (its stderr echoes the argv, so it is redacted before printing).
- **REPLACES.** `kitaru secrets set` overwrites the *whole* key set — a partial update destroys the other keys.
  So the push is one full-surface call, which is what makes the bucket an exact **mirror** of your file rather
  than a pile of merged history. A key you delete from `.env` is gone from the bucket on the next sync.
- **Skipped** keys are the ones that are not `Settings` fields (`MODAL_TOKEN_ID`, `DECODE_LOG_FILE`, …). They
  are read from `os.environ`, so the bucket could never feed them anyway — mirroring one would just add an
  unreadable secret to the store.
- **One-way.** `.env` → Kitaru, never back. There is deliberately no pull: dumping a prod bucket into a
  developer's working tree is the failure this whole design exists to prevent. Add `--yes` (CI) to skip the
  prompt.

Confirm it landed (names only — `--show-values` exists, and you do not need it):

```bash
uv run kitaru secrets list                    # → decode-staging: … (private)
uv run kitaru secrets show decode-staging     # metadata + key names
```

### 1c. ON — run against the bucket, with the key absent from your environment

```bash
env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode run "say hi in exactly three words"
env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode                    # the TUI, identically
```

Working: it answers. No provider key was in the process env, `.env` was not in the chain, and nothing was
written to `os.environ` — the whole surface was hydrated into `Settings` from `decode-staging` at singleton
construction, so the TUI and the headless flow behave identically (hydration is process-scoped, not a
headless-only toggle).

### 1d. Negatives — the four ways this must fail (and win)

| Command | Working looks like |
|---|---|
| **Missing bucket** (or Kitaru local server down): `DECODE_ENV=prod uv run decode run "hi"` | ONE friendly stderr line, exit 1, **no traceback** — and it names the fix, not the missing key: *Decode: DECODE_ENV=prod but the environment bucket 'decode-prod' could not be loaded (it is missing, or the Kitaru local server is down) — run `make sync-secrets ENV=prod` (see CREDENTIALS.md).* |
| Same, in the **TUI**: `DECODE_ENV=prod uv run decode` | The **same** line, exit 1 — the REPL is guarded before it starts. Both surfaces or it isn't a config surface. |
| **No backfill**: delete `GEMINI_API_KEY` from the bucket (`make sync-secrets ENV=staging` after removing it from `.env`), put it back in `.env`, then `env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode run "hi"` | `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` — it fails **loudly** even though the key is sitting right there in `.env`. That file is not in the chain at a remote env. **This is the point of having environments at all**: a provisioning gap must not be masked by a developer's laptop. |
| **Process env wins**: `GEMINI_API_KEY=<a-real-key> DECODE_ENV=staging uv run decode run "hi"` | It answers, using *your* key — precedence is always `process env > (.env \| bucket) > defaults`. Handy for a one-off override; also the escape hatch when a bucket key is stale. |

## Part 2 — the Sandbox Credential Proxy (header injection)

Docker + headless only. The Worker never holds the real token.

> **The request must come from `bash`, or the proxy is not in the picture at all.** Only `bash` runs inside the
> Worker container. **`web_fetch` runs host-side** — a plain `httpx` call in the decode process
> ([`tools/web.py`](src/decode/tools/web.py)) with no `http_proxy`, no CA, no injection. Ask the model to "GET
> this URL" and it will reach for `web_fetch`, sail past the proxy, and hand you a **401 that has nothing to do
> with your credential map** — and worse, it makes 2a's "proxy OFF" control a *fake* control: both sides then
> fail for the same wrong reason and you have tested nothing. Every prompt below therefore *names the `bash`
> tool explicitly*. If your run log shows `running tool: web_fetch`, throw the result away and re-run.

### 2a. OFF — baseline, and the call that fails

```bash
SANDBOX_MODE=docker uv run decode run \
  "use the bash tool to run exactly this, and show me the output:
   python3 -c \"import urllib.request; print(urllib.request.urlopen('https://api.github.com/user').read().decode())\""
```

Working: the model reports **401 / `Requires authentication`** — and the log shows `running tool: bash`, not
`web_fetch`. No proxy container exists — verify during the run, from a second terminal:

```bash
docker ps --filter name=decode-proxy                                        # empty
docker ps --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   # the worker, alone
```

This 401 is the control: the request left the Worker un-injected. In 2b the same request becomes a `200`.

### 2b. ON — the general rule path (any host)

A Proxy Rule names a **`Settings` field**, so this is two small edits and **no secret to create** — the
credential rides the one config surface you set up in Part 1 (your `.env` at `local`, the bucket at a remote
env). First add the field to [`src/decode/config/settings.py`](src/decode/config/settings.py) — plus its `KEY=`
line in [`.env.example`](.env.example), which a unit test enforces
([ADR-0015 §9](docs/adr/0015-environment-bucket-secrets.md)):

```python
github_api_token: SecretStr = SecretStr("")     # Settings          → .env.example: GITHUB_API_TOKEN=
```

Then add a rule to `DEFAULT_PROXY_RULES` in [`src/decode/sandbox/proxy.py`](src/decode/sandbox/proxy.py) — it
ships empty (opt-in) — naming that field in a `{{ … }}` header template:

```python
DEFAULT_PROXY_RULES: list[SandboxProxyRule] = [
    SandboxProxyRule(
        name="github-api",
        hosts=["api.github.com"],
        headers={"Authorization": "Bearer {{ github_api_token }}"},
    ),
]
```

```bash
# Unset SANDBOX_GIT_TOKEN for this one — a non-empty value (in your shell OR your .env) auto-engages
# github_token_rules() too, and you'd be testing 2c's path on top of this one.
env -u SANDBOX_GIT_TOKEN \
  GITHUB_API_TOKEN=<PAT> \
  SANDBOX_CREDENTIAL_PROXY_ENABLED=true SANDBOX_MODE=docker uv run decode run \
  "use the bash tool to run exactly this, and show me the output:
   python3 -c \"import json,urllib.request; print(json.load(urllib.request.urlopen('https://api.github.com/user'))['login'])\""
```

Working: your GitHub login is printed. Same request as 2a, now authenticated — and the Worker never held the
PAT (it lives in the decode process' `Settings` and in the proxy container's env, nowhere else). The startup
line names the rules that loaded, which is how you tell the two paths apart: `hosts=['api.github.com']` is
**this** (your `DEFAULT_PROXY_RULES`); `hosts=['api.github.com', 'github.com']` is `github_token_rules()`, i.e.
a stray `SANDBOX_GIT_TOKEN` took over.

`build_credential_map()` is a **pure function of the hydrated `Settings`** — no lookup, no network. A template
that names no real field, or one whose value is empty, **fails loudly** at flow start naming the field; never a
silently unauthenticated request.

Watch it live. The proxy container runs with `--rm`, so its logs vanish at teardown; start this in a second
terminal **before** the run:

```bash
until docker ps -q -f name=decode-proxy | grep -q .; do sleep 0.2; done
docker logs -f "$(docker ps -q -f name=decode-proxy)" | tee /tmp/proxy.log
# → [decode-proxy] credentials loaded for hosts: ['api.github.com']
# → [decode-proxy] injected headers for api.github.com: ['Authorization']
```

Prove the Worker is token-free, while the run is still in flight:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep -i token     # GH_TOKEN=decode-proxy-injects-the-real-token  ← a DECOY
docker exec $WORKER env | grep -i proxy     # http_proxy=http://decode-proxy-…:8080
docker exec $WORKER gh --version            # gh is installed, alongside git
```

The Worker holds a **decoy** `GH_TOKEN`, not the real one — and that decoy is load-bearing. `gh` refuses to
issue *any* request when it finds no token in its env: it fails locally with `gh auth login` and never emits the
request the proxy would have authenticated. So the Worker is handed a placeholder, `gh` sends
`Authorization: token <decoy>`, and the proxy **overwrites** that header with your real PAT after the request has
left the Worker. The claim to verify is therefore *"what the Worker holds is not the secret"*, not *"the Worker
holds nothing"*:

```bash
docker exec $WORKER env | grep -F "<your-PAT>"   # EMPTY — the real credential is never here
```

### 2c. ON — the one-knob GitHub path (no rule edit)

Revert `DEFAULT_PROXY_RULES` to `[]` first. **Then clear the Workspace** — this step is not optional:

```bash
rm -rf .decode/sandbox   # ← REQUIRED: 2a/2b left a populated Workspace behind

SANDBOX_GIT_TOKEN=<PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

**Why the `rm` matters.** `--repo` clones **only into an empty Workspace** — a populated one is reused, never
re-cloned (re-cloning would discard in-progress work). Parts 2a and 2b ran with no `--repo`, so they left an
empty-scratch tree that the model `git init`'d and committed into. Run 2c against that leftover and `--repo` is
**silently ignored**: there is no `origin`, the push dies with `'origin' does not appear to be a git repository`,
and the proxy gets blamed for a Workspace problem. Confirm with `git -C .decode/sandbox remote -v` — a clone has
an `origin`, a leftover scratch tree has none.

Working: the branch is pushed and the PR opens. A non-empty `SANDBOX_GIT_TOKEN` **auto-engages** the proxy
without the flag, and `github_token_rules()` builds two rules from that one token — `Bearer` on `api.github.com`
(the REST API) and `Basic base64("x-access-token:<PAT>")` on `github.com` (GitHub's git-over-HTTPS transport
rejects `Bearer`). Both `git` **and `gh`** are installed in the proxy-wired Worker — `git` gives the Basic rule a
client, `gh` opens the PR off the Bearer rule (driven by the decoy `GH_TOKEN` from 2b) — and the real PAT is in
neither.

### 2d. Negatives — every way it must stay off

| Command | Working looks like |
|---|---|
| `SANDBOX_CREDENTIAL_PROXY_ENABLED=true uv run decode run "hi"` (mode `none`) | no-op; `docker ps -f name=decode-proxy` empty |
| `SANDBOX_CREDENTIAL_PROXY_ENABLED=true SANDBOX_MODE=docker uv run decode` (TUI) | no-op; the REPL never builds it |
| `SANDBOX_GIT_TOKEN= SANDBOX_MODE=docker uv run decode run "hi"` (explicit empty) | proxy stays **down** — gated on the value, not on presence; an empty token must inject nothing, not an empty `Bearer` |
| flag on, `DEFAULT_PROXY_RULES = []` | the proxy container starts, logs `no credential map … passthrough, no injection`, and injects nothing |
| Docker daemon stopped, `SANDBOX_MODE=docker` | one friendly stderr line, exit non-zero, no flow built |

## Part 3 — both mechanisms at once

Different secrets, different hiding places, **one source**. Put both keys in your `.env` (the 2b
`GITHUB_API_TOKEN` and `GEMINI_API_KEY`), mirror them into the bucket, and run against it: the whole `Settings`
surface — the LLM key *and* the credential the Proxy Rule names — hydrates from `decode-staging`, and then
`build_credential_map()` reads that hydrated `Settings`. No second lookup anywhere.

```bash
make sync-secrets ENV=staging        # .env → the decode-staging bucket (one-way; the file is the truth)

env -u GEMINI_API_KEY -u GITHUB_API_TOKEN -u SANDBOX_GIT_TOKEN \
  DECODE_ENV=staging \
  SANDBOX_CREDENTIAL_PROXY_ENABLED=true \
  SANDBOX_MODE=docker \
  uv run decode run \
  "use the bash tool to run exactly this, and show me the output:
   python3 -c \"import json,urllib.request; print(json.load(urllib.request.urlopen('https://api.github.com/user'))['login'])\""
```

Working: the login is printed, and the log shows `running tool: bash`. Neither key was in the process env —
both were hydrated into `Settings` from the bucket; the PAT then reached the proxy container's env only, never
the Worker's, and never `os.environ`. A key missing from the bucket is one friendly stderr line from the
pre-flight (`make sync-secrets ENV=staging`), never a traceback from inside the flow.

Two ways this run lies to you if you take a shortcut:

- **`running tool: web_fetch` in the log** → the model went around the sandbox entirely (host-side `httpx`, no
  proxy) and the 401 you get back says nothing about your credential map. Force `bash`.
- **`hosts=['api.github.com', 'github.com']` in the `proxy start` line** → a `SANDBOX_GIT_TOKEN` in your shell
  *or your `.env`* auto-engaged `github_token_rules()` on top of your rule. It "works", but it is 2c's path, not
  this one. `env -u SANDBOX_GIT_TOKEN` (above) rules that out; this path shows `hosts=['api.github.com']`.

## Part 4 — cleanup, and the teardown proof

After **any** proxy run there must be no Docker litter:

```bash
docker ps -a --filter name=decode-proxy               # empty
docker network ls --filter name=decode-sandbox-net    # empty
rm -rf .decode/sandbox                                # the Workspace 2a–2c left behind
uv run kitaru secrets delete decode-staging           # the bucket, if you ran Part 1b or 3
```

A leftover network means the Worker was not reaped before `proxy.stop()` — `docker network rm` fails while a
container is still attached, which is why that ordering is load-bearing.

## Part 5 — the automated backstop

Everything above is covered without a PAT and without network:

```bash
# Environment Bucket — the chain per DECODE_ENV, the no-backfill property, the captured failure.
uv run pytest tests/unit/decode/config/test_env_bucket.py \
              tests/unit/decode/config/test_settings.py \
              tests/unit/decode/config/test_env_example_drift.py -v

# The sync script — full-surface replace, key-names-only output, one-way, the local refusal.
uv run pytest tests/unit/scripts/test_sync_secrets.py -v

# Credential Proxy — rules, {{ settings_field }} resolution, map merge, secret-off-argv. No docker.
uv run pytest tests/unit/decode/sandbox/test_proxy.py \
              tests/unit/decode/runtime/test_sandbox_proxy.py -v

# Integration — a REAL mitmproxy container + a real Worker + a stub upstream. Needs docker.
uv run pytest tests/integration/test_credential_proxy.py -v
```

[`tests/integration/test_credential_proxy.py`](tests/integration/test_credential_proxy.py) asserts exactly the
manual claims from 2b: the injected header **arrived** at the upstream, the secret is **absent** from the
Worker's own env, the mitmproxy CA is trusted on the Worker's **first** command, and teardown leaves no
container or network behind. [`test_env_example_drift.py`](tests/unit/decode/config/test_env_example_drift.py)
is why [`.env.example`](.env.example) cannot lie: its `KEY=` lines and the `Settings` fields must match in
**both** directions, with no allowlist.
