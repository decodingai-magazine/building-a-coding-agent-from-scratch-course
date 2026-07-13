# Credentials — where decode's secrets live, and how to test it end to end

decode hides two *different* secrets from two *different* observers. Only one of them is a proxy.
Both ship **off** — with neither on, every key comes from your `.env`.

| | **Sandbox Credential Proxy** | **Model-key secret resolution** |
|---|---|---|
| What it is | a mitmproxy sidecar container | a `kitaru.get_secret()` call — *not* a proxy at all |
| Whose secret | a tool credential (e.g. a GitHub PAT) | your LLM provider API key (Gemini / OpenRouter) |
| Hidden from | the model / the worker container | the Kitaru flow payload + checkpoints |
| Where the secret lives | the proxy container's env | the harness process' memory |
| How it is applied | HTTP header, injected *after* egress | passed to the model client |
| Knob | `SANDBOX_CREDENTIAL_PROXY_ENABLED` (or a non-empty `SANDBOX_GIT_TOKEN`) | `RUNTIME_SECRET_STORE_MODEL_KEY` |
| Needs | a Docker daemon | a Kitaru secret |
| Where | headless (`decode run` / `decode replay`) + `SANDBOX_MODE=docker` only | headless only |
| Code | [`sandbox/proxy.py`](src/decode/sandbox/proxy.py), [`sandbox/proxy_addon.py`](src/decode/sandbox/proxy_addon.py) | [`agent/factory.py`](src/decode/agent/factory.py) `resolve_provider_key_from_secret_store` |
| ADR | [0011 §6](docs/adr/0011-sandboxing-and-credential-proxy.md), [0012 §10](docs/adr/0012-isolated-workspace.md) | [0008 §5](docs/adr/0008-kitaru-durable-runtime.md) |

They compose: one headless run can have both on, hiding two different secrets in two different places.

> **A note on the name.** Model-key secret resolution shipped as `RUNTIME_CREDENTIALS_PROXY_ENABLED`,
> which read exactly like the sandbox Credential Proxy while being an unrelated mechanism.
> [ADR-0008 §5](docs/adr/0008-kitaru-durable-runtime.md) retired that name; the knob is now
> `RUNTIME_SECRET_STORE_MODEL_KEY`. **An old `RUNTIME_CREDENTIALS_PROXY_ENABLED=true` in a `.env` is
> now silently ignored** — rename it. "Credential Proxy" means header injection, and nothing else.

A third knob, `RUNTIME_SECRET_STORE_CONFIG`, is the *superset* of model-key resolution: it hydrates
the whole `Settings` surface (provider, model, keys, tuning) from the **same** Kitaru secret named by
`RUNTIME_SECRET_NAME`, into `Settings` only — never `os.environ`. Take the model key alone, or take
everything. (`MODAL_PROXY_TOKEN_ID` / `_SECRET` are a fourth thing wearing the word "proxy": Modal's
own endpoint auth headers, unrelated to all of the above.)

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

## Part 1 — Model-key secret resolution (the LLM key, from Kitaru)

No Docker. Headless only. Not a proxy — a `get_secret()` lookup at model construction.

### 1a. OFF — baseline

```bash
uv run decode run "say hi in three words"
```

Working: an answer on stdout, exit `0`. The key came from `settings.gemini_api_key` — i.e. your env.

### 1b. ON — the key comes from Kitaru, not the env

```bash
uv run kitaru secrets set decode-llm-creds --private --GEMINI_API_KEY=<your-key>

env -u GEMINI_API_KEY RUNTIME_SECRET_STORE_MODEL_KEY=true \
  uv run decode run "say hi in three words"
```

Working: the same answer, exit `0` — **with `GEMINI_API_KEY` unset in the environment**. That is the
whole claim: the key was resolved from the Kitaru secret at run time, so a deployed flow payload
carries the secret *name*, never the raw key.

Control — prove the `env -u` actually bit:

```bash
env -u GEMINI_API_KEY uv run decode run "hi"
# → Decode: set GEMINI_API_KEY … , exit 1
```

### 1c. Negative — flag on, secret missing

```bash
env -u GEMINI_API_KEY RUNTIME_SECRET_STORE_MODEL_KEY=true \
  RUNTIME_SECRET_NAME=does-not-exist uv run decode run "hi"
```

Working: one friendly stderr line naming the real fix (`kitaru secrets set does-not-exist
--GEMINI_API_KEY=…`), exit non-zero, no traceback — and **no silent fallback** to the settings key.

### 1d. Negative — the TUI ignores the flag (flow-mode only)

```bash
env -u GEMINI_API_KEY RUNTIME_SECRET_STORE_MODEL_KEY=true uv run decode
```

Working: `Decode: set GEMINI_API_KEY …`, exit 1. The REPL guard deliberately ignores the flag: the
lookup is `flow_mode`-gated, so the TUI never reaches Kitaru. A green REPL here would be the bug.

## Part 2 — Sandbox Credential Proxy (header injection)

Docker + headless only. The worker never holds the real token.

> **How much of this is Kitaru?** Less than the name suggests. The proxy itself — the mitmproxy
> container, the per-run docker network, the CA trust, the header injection — is plain docker, with no
> Kitaru anywhere. Kitaru enters at exactly **one** seam: `build_credential_map()` resolves
> `{{ secret-name.key }}` header templates via `kitaru.get_secret()`. So **2b (below) is the only
> Credential-Proxy scenario that touches Kitaru at all**; **2c (`SANDBOX_GIT_TOKEN`) bypasses it
> entirely** — `github_token_rules()` builds literal header values with no secret-store fetch. If you
> are here to see Kitaru work, Part 1 and 2b are the demos; 2c is a docker/mitmproxy demo.

### 2a. OFF — baseline, and the call that fails

```bash
SANDBOX_MODE=docker uv run decode run \
  "use python urllib to GET https://api.github.com/user and print the response"
```

Working: the model reports **401 / `Requires authentication`**. No proxy container exists — verify
during the run, from a second terminal:

```bash
docker ps --filter name=decode-proxy                                        # empty
docker ps --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   # the worker, alone
```

This 401 is the control. In 2b the same request becomes a `200`.

### 2b. ON — the general rule path (any host)

Add a rule to `DEFAULT_PROXY_RULES` in [`src/decode/sandbox/proxy.py`](src/decode/sandbox/proxy.py)
— it ships empty (opt-in):

```python
DEFAULT_PROXY_RULES: list[SandboxProxyRule] = [
    SandboxProxyRule(
        name="github-api",
        hosts=["api.github.com"],
        headers={"Authorization": "Bearer {{ github-token.value }}"},
    ),
]
```

```bash
uv run kitaru secrets set github-token --private --value=<PAT>

SANDBOX_CREDENTIAL_PROXY_ENABLED=true SANDBOX_MODE=docker uv run decode run \
  "use python urllib to GET https://api.github.com/user and print the login field"
```

Working: your GitHub login is printed. Same request as 2a, now authenticated — and the worker never
held the PAT.

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

## Part 3 — both proxies on at once

Different secrets, different hiding places.

```bash
uv run kitaru secrets set decode-llm-creds --private --GEMINI_API_KEY=<key>
uv run kitaru secrets set github-token --private --value=<PAT>
# with the api.github.com rule from 2b in DEFAULT_PROXY_RULES

env -u GEMINI_API_KEY \
  RUNTIME_SECRET_STORE_MODEL_KEY=true \
  SANDBOX_CREDENTIAL_PROXY_ENABLED=true \
  SANDBOX_MODE=docker \
  uv run decode run "GET https://api.github.com/user with urllib and print the login"
```

Working: the login is printed. The LLM key was never in the env; the PAT was never in the worker.
Pre-flight order is load-bearing — secret-store hydration runs before the proxy pre-flight, so the
two never emit conflicting error lines.

## Part 4 — cleanup, and the teardown proof

After **any** run there must be no Docker litter:

```bash
docker ps -a --filter name=decode-proxy               # empty
docker network ls --filter name=decode-sandbox-net    # empty
uv run kitaru secrets delete decode-llm-creds
uv run kitaru secrets delete github-token
```

A leftover network means the worker was not reaped before `proxy.stop()` — `docker network rm` fails
while a container is still attached, which is why that ordering is load-bearing.

## Part 5 — the automated backstop

Everything above is covered without a PAT and without network:

```bash
# unit — rules, template resolution, map merge, secret-off-argv, log-names-only. No docker.
uv run pytest tests/unit/decode/sandbox/test_proxy.py \
              tests/unit/decode/runtime/test_sandbox_proxy.py \
              tests/unit/decode/runtime/test_credentials_proxy.py \
              tests/unit/decode/agent/test_factory_credentials_proxy.py -v

# integration — a REAL mitmproxy container + a real worker + a stub upstream. Needs docker.
uv run pytest tests/integration/test_credential_proxy.py -v
```

[`tests/integration/test_credential_proxy.py`](tests/integration/test_credential_proxy.py) asserts
exactly the manual claims from 2b: the injected header **arrived** at the upstream, the secret is
**absent** from the worker's own env, the mitmproxy CA is trusted on the worker's **first** command,
and teardown leaves no container or network behind.
