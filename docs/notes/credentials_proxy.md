---
title: "The Credential Proxy — how it worked, and why it is gone"
created: 2026-07-13
purpose: >
  A postmortem walkthrough of decode's deleted Credential Proxy: the architecture, the
  containers and the network, what crossed which boundary, how a request travelled from the
  token-free Worker out to the internet and back, and where every credential actually lived.
  Written after the code was removed, from the code as it last existed.
grounded_in:
  - src/decode/sandbox/proxy.py               # deleted in 51d7457
  - src/decode/sandbox/proxy_addon.py         # deleted in 51d7457
  - src/decode/sandbox/docker_backend.py      # proxy wiring deleted in 51d7457
  - src/decode/runtime/flow.py                # _sandbox_proxy() deleted in 51d7457
  - docs/adr/0011-sandboxing-and-credential-proxy.md   # §6 — the original design
  - docs/adr/0012-isolated-workspace.md                # §10 — SANDBOX_GIT_TOKEN, two mechanisms
  - docs/adr/0016-drop-credential-proxy.md             # the deletion
status: historical — the code described here no longer exists on main
---

# The Credential Proxy

**Deleted** in commit `51d7457` (PR #32), per [ADR-0016](../adr/0016-drop-credential-proxy.md).
Read it at `git show 51d7457^:src/decode/sandbox/proxy.py`. This note explains what it was and
how it worked, because the *idea* is worth keeping even though the code is not.

## The one-sentence version

A sandboxed **Worker** container could make an *authenticated* call to GitHub while holding **no
credential at all**: it sent the request with no token, and a **mitmproxy** container sitting on the
same docker network attached the `Authorization` header **after** the request had already left the
Worker.

The property it bought: *the process that runs model-chosen shell commands cannot read the token,
because the token is not in its environment, its filesystem, or its memory.*

## Scope — where it applied

| Axis | Value |
|---|---|
| Surface | **Headless only** (`decode run` / `decode replay`). The REPL never built it — that is what kept bare `decode` from importing kitaru. |
| Sandbox mode | **`docker` only.** `modal` never had it (no docker network to hang a proxy container off) — it direct-injected the token instead ([ADR-0012 §10](../adr/0012-isolated-workspace.md)). `none` is the host, so nothing to protect. |
| Default | **Off.** `DEFAULT_PROXY_RULES` shipped **empty**; an empty credential map is a passthrough proxy that injects nothing. |
| Engaged when | `SANDBOX_MODE=docker` **and** (`SANDBOX_CREDENTIAL_PROXY_ENABLED=true` **or** a non-empty `SANDBOX_GIT_TOKEN`). |

That table is also the epitaph: the invariant it protected was true in exactly **one of three
modes** — and not the remote one, where isolation matters most.

## Architecture

```mermaid
flowchart TB
    subgraph host["HOST — the decode process (headless flow)"]
        settings["Settings (hydrated)<br/>SANDBOX_GIT_TOKEN · SANDBOX_PROXY_IMAGE"]
        rules["Proxy Rules<br/>SandboxProxyRule(name, hosts[], headers{})<br/>DEFAULT_PROXY_RULES = [] · github_token_rules(token)"]
        cmap["build_credential_map(rules)<br/>resolve templates HOST-SIDE, at flow start<br/>→ {host: {header: value}}"]
        sp["_sandbox_proxy() context manager<br/>start → install executor → yield → teardown"]
        settings --> rules --> cmap --> sp
    end

    subgraph net["per-run docker network — decode-sandbox-net-&lt;uuid12&gt;"]
        worker["WORKER container (decode-sandbox-&lt;id&gt;)<br/>image: uv:python3.12-bookworm-slim<br/>/workspace = bind-mount of .decode/sandbox<br/>env: http_proxy/https_proxy → proxy · GH_TOKEN=&lt;decoy&gt;<br/>trust store: + mitmproxy CA<br/><b>HOLDS NO REAL CREDENTIAL</b>"]
        proxy["PROXY container (decode-proxy-&lt;uuid12&gt;)<br/>image: mitmproxy/mitmproxy<br/>mitmdump --listen-port 8080 -s /opt/proxy_addon.py<br/>env: DECODE_CREDENTIAL_MAP={host:{header:value}}<br/><b>HOLDS THE CREDENTIAL</b>"]
    end

    internet([internet — github.com · api.github.com · everything else])

    sp -- "docker network create" --> net
    sp -- "docker run --env-file (0600, unlinked)" --> proxy
    sp -- "docker run --network + proxy env + CA mount" --> worker
    worker -- "request, NO auth header" --> proxy
    proxy -- "request + injected Authorization" --> internet
    internet -- "response (unmodified)" --> proxy -- "response" --> worker

    classDef hostc fill:#1565c0,stroke:#0d47a1,color:#fff;
    classDef sec fill:#e65100,stroke:#bf360c,color:#fff;
    classDef ext fill:#6a1b9a,stroke:#38006b,color:#fff;
    class settings,rules,cmap,sp hostc;
    class worker,proxy sec;
    class internet ext;
```

Three processes, three trust levels: the **host** knows the secret and resolves it; the **proxy**
holds the resolved secret in its own env; the **Worker** — the only one running model-chosen code —
holds nothing.

## Inputs and outputs

**Inputs** (all host-side):

| Input | Where from | Role |
|---|---|---|
| `SANDBOX_MODE=docker` | `Settings` | Gate. Anything else = no proxy. |
| `SANDBOX_CREDENTIAL_PROXY_ENABLED` | `Settings` (bool, default `false`) | Explicit opt-in for the rules path. |
| `SANDBOX_GIT_TOKEN` | `Settings` (`SecretStr`) | The one-knob GitHub shortcut — a non-empty value **auto-engaged** the proxy. |
| `SANDBOX_PROXY_IMAGE` | `Settings` (default `mitmproxy/mitmproxy`) | The addon container image. |
| `DEFAULT_PROXY_RULES` | Code constant, shipped `[]` | Rules for any *other* host. |

**Intermediate artifact** — the **credential map**, the only object that carries a resolved secret:

```python
# build_credential_map(rules) → this, host-side, at flow start
{
  "api.github.com": {"Authorization": "Bearer ghp_xxx"},           # REST API (gh, curl)
  "github.com":     {"Authorization": "Basic eC1hY2Nlc3MtdG9rZW46Z2hwX3h4eA=="},  # git push over HTTPS
}
```

Two rules for one token, because GitHub wants **different auth** for the two transports: `Bearer`
for the REST API, `Basic base64("x-access-token:<PAT>")` for git-over-HTTPS (which rejects Bearer).
`api.github.com` had to come **first** — the addon returned the *first* match and `github.com`
parent-matches `api.github.com`.

**Outputs** (what `_sandbox_proxy()` produced for the rest of the flow):

| Output | Consumer |
|---|---|
| `proxy.network` — `decode-sandbox-net-<uuid12>` | `DockerBackend(network=…)` — the Worker joins it. |
| `proxy.worker_proxy_env` — `http_proxy`/`https_proxy`/`HTTP_PROXY`/`HTTPS_PROXY` = `http://decode-proxy-<uuid12>:8080`, `no_proxy=localhost,127.0.0.1`, **`GH_TOKEN=<decoy>`** | `DockerBackend(proxy_env=…)` — `-e` flags on the Worker's `docker run`. Carries **no secret**. |
| `proxy.ca_cert_host_path` — the mitmproxy CA in a host temp dir | `DockerBackend(ca_cert_host_path=…)` — bind-mounted read-only into the Worker, then trusted. |
| A `SandboxExecutor` wired to that `DockerBackend` | `install_executor()` — becomes `bash`'s executor for the flow span. |

## How a credential reached the proxy — and *only* the proxy

The resolved map never touched the Worker, and never touched the host's `argv` either:

1. `build_credential_map()` resolves rules host-side into `{host: {header: value}}`.
2. `DockerCredentialProxy._run_proxy_container()` writes that map as JSON into a **`0600` temp file**
   (`DECODE_CREDENTIAL_MAP=<json>`), passes it as `docker run --env-file <path>`, and **`unlink`s the
   file in a `finally`** the moment `docker run` returns. On-disk lifetime: milliseconds. It never
   appears in `docker run`'s command line, so a host-side `ps` never sees it.
3. Inside the container, `proxy_addon.py` reads it once at import: `json.loads(os.environ.get("DECODE_CREDENTIAL_MAP", "{}"))`.
4. Logs everywhere print **header names and host names, never values**.

The Worker's `docker run` got `--network`, the proxy URL env vars, and a CA mount. No secret.

## The request path — Worker → internet

```mermaid
sequenceDiagram
    participant W as Worker container<br/>(git / gh / curl / urllib)
    participant P as mitmproxy + CredentialAddon
    participant G as github.com

    Note over W: env: https_proxy=http://decode-proxy-xxx:8080<br/>trust store contains the mitmproxy CA
    W->>P: CONNECT github.com:443
    P-->>W: 200 Connection established
    W->>P: TLS handshake — P serves a leaf cert<br/>signed by the mitmproxy CA (Worker trusts it → OK)
    W->>P: POST /decode/decode.git/git-receive-pack<br/>(NO Authorization header)
    Note over P: addon.request(flow)<br/>_match_host("github.com") → hit<br/>flow.request.headers["Authorization"] = "Basic …"<br/>(SET, not append — overwrites the gh decoy)
    P->>G: TLS to the real github.com (upstream, normal certs)<br/>POST … + Authorization: Basic …
    G-->>P: 200 (push accepted)
    P-->>W: 200 (response passed through UNMODIFIED)
```

Points that matter:

- **TLS is terminated twice.** mitmproxy is a man-in-the-middle by design: it presents the Worker a
  leaf certificate minted by its own CA (which is why the CA had to be in the Worker's trust store),
  and opens a *separate*, ordinary TLS connection upstream. It reads and rewrites plaintext in
  between. That is the whole trick — you cannot inject a header into a connection you cannot decrypt.
- **The addon only touches the request.** The `request` hook sets headers; there is no `response`
  hook. Internet → Worker is a pass-through.
- **`headers[name] = value` replaces.** That is what let the decoy token work (below).
- **A host miss is a pass-through, not a failure.** `cli.github.com` (the `gh` apt install) matched no
  rule, so it egressed un-injected. `_match_host` matched exact **or parent domain**, case-insensitively.

### The decoy token

`gh` refuses to issue *any* request when it finds no token in the environment — it fails locally with
"gh auth login" and never reaches the proxy that would have authenticated it. So the Worker was handed
a placeholder:

```python
_GH_PLACEHOLDER_TOKEN = "decode-proxy-injects-the-real-token"   # GH_TOKEN in the worker env
```

`gh` then proceeds happily, sends `Authorization: token decode-proxy-injects-the-real-token`, and the
addon **overwrites** that header with the real credential after the request has left the Worker. The
string authenticates nothing; the invariant survives. It is also the single clearest tell that the
design was fighting its environment.

## Container CA trust — the race that needed a synchronous fix

The Worker had to trust the proxy's CA **before its first command ran**, or the very first `git`/`curl`
would fail on certificate validation. So `DockerBackend.create()`:

1. Bind-mounts the CA read-only at `/usr/local/share/ca-certificates/mitmproxy-ca-cert.crt` — the exact
   path `update-ca-certificates` folds into the system trust store.
2. Runs `docker exec <worker> update-ca-certificates` **synchronously**, awaiting it (bounded at 60s),
   before `create()` returns. On failure the just-created container is reaped and the error raised.

Also load-bearing: the proxy's cert dir was `chmod 0o777` host-side so non-root `mitmdump` could write
its generated CA into the bind-mounted confdir, and `start()` blocked until **the CA file existed *and*
the listen port answered** (20s budget, probed with a `python3` socket connect inside the container).

## Lifecycle and teardown order

```
_sandbox_proxy(repo, local):                       # runtime/flow.py, wraps the whole flow body
    gate: sandbox_mode == "docker" and (flag or SANDBOX_GIT_TOKEN)   # else pure no-op, imports nothing
    rules  = github_token_rules(token) + DEFAULT_PROXY_RULES
    proxy  = DockerCredentialProxy(build_credential_map(rules))
    proxy.start()                                  # network create → docker run → wait until ready
    install_executor(SandboxExecutor(DockerBackend(network=…, proxy_env=…, ca_cert_host_path=…)))
    prepare_workspace_or_empty(...) + warm the worker   # CA trusted before the first bash
    yield                                          # ← the agent runs here
    finally:
        reap the WORKER first                      # docker network rm FAILS while it is attached
        proxy.stop()                               # stop container → retry network rm (10×) → rmtree certs
```

Teardown order was not a style choice: `docker network rm` fails while any container is still
attached, and the daemon's endpoint cleanup lags container removal — hence the bounded retry loop.

## What it did **not** protect against

Egress was **cooperative**. The Worker was *pointed* at the proxy by convention (`http_proxy` env vars
that well-behaved client libraries honour); it was never *forced* through it. A model that wanted to
bypass it typed:

```bash
curl --noproxy '*' https://example.com     # walks straight around the proxy
```

So the proxy defended against a model **reading its own environment**, not against a model that means
harm. The upgrade path was an internal-only network with default-deny egress — never built.

It also never touched the property that actually matters — **secrets never reach the model's
*context***. That comes from `Settings` never being serialised into a prompt, from `SecretStr`, and
from logs carrying names not values. All of that survives the deletion untouched.

## Why it was deleted

Full argument in [ADR-0016](../adr/0016-drop-credential-proxy.md). The short list:

1. **One of three modes.** `modal` — the remote backend, where isolation matters most — direct-injected
   the token anyway. The "Worker holds no token" invariant was already **false** there, with a
   documented exception saying so.
2. **Two mechanisms, one knob.** `SANDBOX_GIT_TOKEN` behaved differently depending on a backend the
   user did not pick for security reasons.
3. **The machinery was large and fragile in load-bearing places** — a per-run network, a CA race with
   its own regression test, a teardown order that must not be reordered, an apt install that had to
   egress through the proxy, and a decoy token planted to appease `gh`.
4. **It was never an exfiltration barrier** (see above).
5. **The thing it guarded is a scoped, revocable PAT** the user deliberately handed the agent so it
   could push a branch and open a PR. Not a production database credential.

## What replaced it

One mechanism, both backends ([ADR-0016 §2](../adr/0016-drop-credential-proxy.md)): when
`SANDBOX_GIT_TOKEN` is set it is **direct-injected into the Worker env as `GITHUB_TOKEN`** — docker via
a value-less `-e GITHUB_TOKEN` (so the value never sits in host-visible argv), modal via a
`modal.Secret` — plus a git credential-helper (`x-access-token:$GITHUB_TOKEN`) so `git push` over HTTPS
works and `gh` authenticates off it. Unset → the sandbox holds no credential at all, and host-side
**Hand-back** still ships the Session Branch.

**A sandboxed process CAN now read `$GITHUB_TOKEN`.** That is the honest trade: hand it a *scoped,
revocable* PAT. The security story is smaller — and true as written, in every mode.
