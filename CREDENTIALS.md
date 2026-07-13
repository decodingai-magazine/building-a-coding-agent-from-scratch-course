# Credentials — where decode's secrets live, and how to test it end to end

decode hides two *different* secrets from two *different* observers. Only one of them is a proxy.
Both ship **off** — with neither on, every key comes from your `.env`.

| | **Sandbox Credential Proxy** | **Secret-store config source** |
|---|---|---|
| What it is | a mitmproxy sidecar container | a `kitaru.get_secret()` call — *not* a proxy at all |
| Whose secret | a tool credential (e.g. a GitHub PAT) | your whole config surface, LLM provider API key included |
| Hidden from | the model / the worker container | the Kitaru flow payload + checkpoints |
| Where the secret lives | the proxy container's env | the harness process' `Settings` (never `os.environ`) |
| How it is applied | HTTP header, injected *after* egress | hydrated into `Settings`, which every reader already reads |
| Knob | `SANDBOX_CREDENTIAL_PROXY_ENABLED` (or a non-empty `SANDBOX_GIT_TOKEN`) | `RUNTIME_SECRET_STORE_CONFIG` |
| Needs | a Docker daemon | a Kitaru secret (named by `RUNTIME_SECRET_NAME`) |
| Where | headless (`decode run` / `decode replay`) + `SANDBOX_MODE=docker` only | headless only |
| Code | [`sandbox/proxy.py`](src/decode/sandbox/proxy.py), [`sandbox/proxy_addon.py`](src/decode/sandbox/proxy_addon.py) | [`runtime/flow.py`](src/decode/runtime/flow.py) `_config_from_secret_store` |
| ADR | [0011 §6](docs/adr/0011-sandboxing-and-credential-proxy.md), [0012 §10](docs/adr/0012-isolated-workspace.md) | [0008 §5](docs/adr/0008-kitaru-durable-runtime.md) |

They compose: one headless run can have both on, hiding two different secrets in two different places.

> **A note on the name.** A third mechanism once resolved the *model key alone* from a Kitaru secret at
> model construction, and shipped under a name (`RUNTIME_CREDENTIALS_PROXY_ENABLED`) that read exactly
> like the sandbox Credential Proxy while being unrelated. It is **deleted**
> ([ADR-0015 §4](docs/adr/0015-environment-bucket-secrets.md)): the provider API key now comes from
> `Settings` alone, hydrated by whichever settings source is active. A stale `RUNTIME_*` entry in a
> `.env` is silently ignored. "Credential Proxy" means header injection, and nothing else.

The surviving secret-store lookup hydrates the whole `Settings` surface (provider, model, keys,
tuning) from the Kitaru secret named by `RUNTIME_SECRET_NAME`, into `Settings` only — never
`os.environ`, and the real process env still wins. (`MODAL_PROXY_TOKEN_ID` / `_SECRET` are a third
thing wearing the word "proxy": Modal's own endpoint auth headers, unrelated to all of the above.)

The rest of this file is a manual e2e tutorial. Every case is an **A/B**: the same command with the
flag flipped, and a different observable. Run the automated backstop (Part 5) before reaching for a
real PAT — it already proves the same claims with no network and no secret.

## 0. Prerequisites

```bash
docker info >/dev/null && echo "docker ok"    # only the Sandbox Credential Proxy needs this
export GEMINI_API_KEY=<your-key>
uv run kitaru secrets list                    # must print (a possibly empty) list, not hang
```

Both features read their secret through the `kitaru secrets` store, so that last command has to work
before anything below does. Two ways to get there — pick either:

```bash
uv run kitaru login     # starts a local server on 127.0.0.1:8383, with a web dashboard
uv run kitaru logout    # or: no server at all — clears the auth state, uses the local database
```

`kitaru secrets list` retrying `HTTPConnection(host='127.0.0.1', port=8383) … Connection refused`
means stored auth state from an earlier `kitaru login` is pointing at a server that is no longer
running. Either bring it back up (`kitaru login`) or drop the state (`kitaru logout`).

## Part 2 — Sandbox Credential Proxy (header injection)

Docker + headless only. The worker never holds the real token.

> **How much of this is Kitaru? None of it** (since [ADR-0015 §6](docs/adr/0015-environment-bucket-secrets.md)).
> The proxy — the mitmproxy container, the per-run docker network, the CA trust, the header injection —
> is plain docker. And `build_credential_map()` is now a **pure function of the hydrated `Settings`**:
> a `{{ settings_field }}` template names a **Settings field**, so the credential arrives the same way
> every other setting does (your `.env` at `DECODE_ENV=local`, the Environment Bucket at a remote one).
> Kitaru's only `get_secret` seam in the whole codebase is that Environment-Bucket settings source —
> the Credential Proxy has none.

> **The request must come from `bash`, or the proxy is not in the picture at all.** Only `bash` runs
> inside the worker container. **`web_fetch` runs host-side** — a plain `httpx` call in the decode
> process ([`tools/web.py`](src/decode/tools/web.py)) with no `http_proxy`, no CA, no injection. Ask
> the model to "GET this URL" and it will reach for `web_fetch`, sail past the proxy, and hand you a
> **401 that has nothing to do with your credential map**. Every prompt below therefore *names the
> `bash` tool explicitly*. If your run log shows `running tool: web_fetch`, you are testing nothing —
> re-run with a prompt that forces `bash`.

### 2a. OFF — baseline, and the call that fails

```bash
SANDBOX_MODE=docker uv run decode run \
  "use the bash tool to run exactly this, and show me the output:
   python3 -c \"import urllib.request; print(urllib.request.urlopen('https://api.github.com/user').read().decode())\""
```

Working: the model reports **401 / `Requires authentication`** — and the log shows `running tool:
bash`, not `web_fetch`. No proxy container exists — verify during the run, from a second terminal:

```bash
docker ps --filter name=decode-proxy                                        # empty
docker ps --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   # the worker, alone
```

This 401 is the control: the request left the worker un-injected. In 2b the same request becomes a
`200`. (A 401 from `web_fetch` would prove nothing — it never entered the worker.)

### 2b. ON — the general rule path (any host)

A proxy rule names a **`Settings` field**, so this path is two small edits and no separate secret.
First add the field to [`src/decode/config/settings.py`](src/decode/config/settings.py) — plus its
`KEY=` line in [`.env.example`](.env.example), which a unit test enforces
([ADR-0015 §9](docs/adr/0015-environment-bucket-secrets.md)):

```python
github_api_token: SecretStr = SecretStr("")     # Settings          → .env.example: GITHUB_API_TOKEN=
```

Then add a rule to `DEFAULT_PROXY_RULES` in [`src/decode/sandbox/proxy.py`](src/decode/sandbox/proxy.py)
— it ships empty (opt-in) — naming that field in a `{{ … }}` header template:

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

Working: your GitHub login is printed. Same request as 2a, now authenticated — and the worker never
held the PAT (it lives in the decode process' `Settings` and in the proxy container's env, nowhere
else). The startup line names the rules that loaded, which is how you tell the two paths apart:
`hosts=['api.github.com']` is **this** (your `DEFAULT_PROXY_RULES`);
`hosts=['api.github.com', 'github.com']` is `github_token_rules()`, i.e. a stray `SANDBOX_GIT_TOKEN`
took over.

A template that names no real field, or one whose value is empty, **fails loudly** at flow start
naming the field — never a silently unauthenticated request.

Watch it live. The proxy container runs with `--rm`, so its logs vanish at teardown; start this in a
second terminal **before** the run:

```bash
until docker ps -q -f name=decode-proxy | grep -q .; do sleep 0.2; done
docker logs -f "$(docker ps -q -f name=decode-proxy)" | tee /tmp/proxy.log
# → [decode-proxy] credentials loaded for hosts: ['api.github.com']
# → [decode-proxy] injected headers for api.github.com: ['Authorization']
```

Prove the worker is token-free, while the run is still in flight:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep -i token     # GH_TOKEN=decode-proxy-injects-the-real-token  ← a DECOY
docker exec $WORKER env | grep -i proxy     # http_proxy=http://decode-proxy-…:8080
docker exec $WORKER gh --version            # gh is installed, alongside git
```

The worker holds a **decoy** `GH_TOKEN`, not the real one — and that decoy is load-bearing. `gh`
refuses to issue *any* request when it finds no token in its env: it fails locally with `gh auth
login` and never emits the request the proxy would have authenticated. So the worker is handed a
placeholder, `gh` sends `Authorization: token <decoy>`, and the proxy **overwrites** that header with
your real PAT after the request has left the worker. The claim to verify is therefore *"what the
worker holds is not the secret"*, not *"the worker holds nothing"*:

```bash
docker exec $WORKER env | grep -F "<your-PAT>"   # EMPTY — the real credential is never here
```

### 2c. ON — the one-knob GitHub path (no rule edit, no Kitaru secret)

Revert `DEFAULT_PROXY_RULES` to `[]` first. **Then clear the Workspace** — this step is not optional:

```bash
rm -rf .decode/sandbox   # ← REQUIRED: 2a/2b left a populated Workspace behind

SANDBOX_GIT_TOKEN=<PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

**Why the `rm` matters.** `--repo` clones **only into an empty Workspace** — a populated one is reused,
never re-cloned (re-cloning would discard in-progress work). Parts 2a and 2b ran with no `--repo`, so
they left an empty-scratch tree that the model `git init`'d and committed into. Run 2c against that
leftover and `--repo` is **silently ignored**: there is no `origin`, the push fails, and the proxy gets
blamed for a Workspace problem. Confirm with `git -C .decode/sandbox remote -v` — a clone has an
`origin`, a leftover scratch tree has none.

Working: the branch is pushed and the PR opens. A non-empty `SANDBOX_GIT_TOKEN` **auto-engages** the
proxy without the flag, and `github_token_rules()` builds two rules from that one token — `Bearer` on
`api.github.com` (the REST API) and `Basic base64("x-access-token:<PAT>")` on `github.com` (GitHub's
git-over-HTTPS transport rejects `Bearer`). Both `git` **and `gh`** are installed in the proxy-wired
worker — `git` gives the Basic rule a client, `gh` opens the PR off the Bearer rule (driven by the
decoy `GH_TOKEN` from 2b) — and the real PAT is in neither.

### 2d. Negatives — every way it must stay off

| Command | Working looks like |
|---|---|
| `SANDBOX_CREDENTIAL_PROXY_ENABLED=true uv run decode run "hi"` (mode `none`) | no-op; `docker ps -f name=decode-proxy` empty |
| `SANDBOX_CREDENTIAL_PROXY_ENABLED=true SANDBOX_MODE=docker uv run decode` (TUI) | no-op; the REPL never builds it and never imports kitaru |
| `SANDBOX_GIT_TOKEN= SANDBOX_MODE=docker uv run decode run "hi"` (explicit empty) | proxy stays **down** — gated on the value, not on presence; an empty token must inject nothing, not an empty `Bearer` |
| flag on, `DEFAULT_PROXY_RULES = []` | the proxy container starts, logs `no credential map … passthrough, no injection`, and injects nothing |
| Docker daemon stopped, `SANDBOX_MODE=docker` | one friendly stderr line, exit non-zero, no flow built |

## Part 3 — both features on at once

Different secrets, different hiding places — and one source. Put **both** keys in your `.env` (the
2b `GITHUB_API_TOKEN` and `GEMINI_API_KEY`), mirror them into an Environment Bucket, and run against
it: the whole `Settings` surface — the LLM key *and* the credential the proxy rule names — hydrates
from the bucket, then `build_credential_map()` reads that hydrated `Settings` (no second lookup).

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

Working: the login is printed, and the log shows `running tool: bash`. Neither key was in the process
env — both were hydrated into `Settings` from the `decode-staging` bucket; the PAT then reached the
proxy container's env only, never the worker's. A missing bucket key is one friendly stderr line from
the pre-flight (`make sync-secrets ENV=staging`), never a traceback from inside the flow.

Two ways this run lies to you if you take the shortcuts:

- **`running tool: web_fetch` in the log** → the model went around the sandbox entirely (host-side
  `httpx`, no proxy) and the 401 you get back says nothing about your credential map. Force `bash`.
- **`hosts=['api.github.com', 'github.com']` in the `proxy start` line** → a `SANDBOX_GIT_TOKEN` in
  your shell *or your `.env`* auto-engaged `github_token_rules()` on top of your rule. It "works", but
  it is 2c's path, not this one. `env -u SANDBOX_GIT_TOKEN` (above) rules that out; this path shows
  `hosts=['api.github.com']`.

## Part 4 — cleanup, and the teardown proof

After **any** run there must be no Docker litter:

```bash
docker ps -a --filter name=decode-proxy               # empty
docker network ls --filter name=decode-sandbox-net    # empty
uv run kitaru secrets delete decode-staging           # only if you ran Part 3
```

A leftover network means the worker was not reaped before `proxy.stop()` — `docker network rm` fails
while a container is still attached, which is why that ordering is load-bearing.

## Part 5 — the automated backstop

Everything above is covered without a PAT and without network:

```bash
# unit — rules, template resolution, map merge, secret-off-argv, log-names-only. No docker.
uv run pytest tests/unit/decode/sandbox/test_proxy.py \
              tests/unit/decode/runtime/test_sandbox_proxy.py -v

# integration — a REAL mitmproxy container + a real worker + a stub upstream. Needs docker.
uv run pytest tests/integration/test_credential_proxy.py -v
```

[`tests/integration/test_credential_proxy.py`](tests/integration/test_credential_proxy.py) asserts
exactly the manual claims from 2b: the injected header **arrived** at the upstream, the secret is
**absent** from the worker's own env, the mitmproxy CA is trusted on the worker's **first** command,
and teardown leaves no container or network behind.
