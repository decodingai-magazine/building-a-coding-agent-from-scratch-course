# Credentials — one config surface

`Settings` ([`config/settings.py`](../src/decode/config/settings.py)) is the **single source of truth** for
every credential decode holds. Nothing else reads one. So there is only ever one interesting question —
**how does a value get *into* `Settings`?** — and `DECODE_ENV` is the whole answer:

| `DECODE_ENV` | The source chain (highest first) |
|---|---|
| `local` (default) | process env → **`.env`** → defaults. Kitaru is never imported. |
| `dev` / `staging` / `prod` | process env → **the Environment Bucket** (`decode-<env>`) → defaults. **`.env` is dropped from the chain entirely.** |

One surface, two injection mechanisms, selected by one variable ([ADR-0015](../docs/adr/0015-environment-bucket-secrets.md)).
Values land in `Settings` **only** — never `os.environ` — so a model-chosen `bash` never inherits one.

A second, smaller question, kept separate: *which of those values does the **sandbox** get?* Exactly one,
opt-in — `SANDBOX_GIT_TOKEN` ([Part 2](#part-2--the-sandbox-git-token-sandbox_git_token)). Everything else
stays in the harness process.

[Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs)'s footprint is one line: the Environment Bucket **is** a Kitaru secret — the only `get_secret` seam
in the codebase ([ADR-0015 §6](../docs/adr/0015-environment-bucket-secrets.md)). (`MODAL_PROXY_TOKEN_ID` /
`_SECRET` are unrelated: Modal's own endpoint-auth headers — see [`02_modal_endpoints.md`](02_modal_endpoints.md).)

The rest is a manual e2e tutorial. Every case is an **A/B**: the same command with one thing flipped, and a
different observable. Part 4 is the automated backstop — same claims, no network, no PAT, no Kitaru.

> **Clean break — the Credential Proxy is gone** (why: [ADR-0016](../docs/adr/0016-drop-credential-proxy.md); stale-key behavior: Part 2c). What replaces it is [Part 2](#part-2--the-sandbox-git-token-sandbox_git_token) — one token, direct-injected, both backends, and an honest warning that the model can read it.

## Part 0 — prerequisites

```bash
cp .env.example .env                          # set GEMINI_API_KEY
uv run kitaru secrets list                    # Parts 1b+ only: must print a (possibly empty) list, not hang
docker info >/dev/null && echo "docker ok"    # Part 2 only
```

Part 1a needs **none** of that — at `DECODE_ENV=local` (the default) decode never touches Kitaru or Docker.

If `kitaru secrets list` retries `HTTPConnection(host='127.0.0.1', port=8383) … Connection refused`, stored
auth state from an earlier `kitaru login` is pointing at a server that is no longer running. Pick either:

```bash
uv run kitaru logout    # no server at all — the server-less local database (simplest)
uv run kitaru login     # or bring the local server + web dashboard back up on 127.0.0.1:8383
```

On macOS the local Kitaru server can also die mid-run with an ObjC fork-safety abort — the
`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` fix is in [03_runtime.md](03_runtime.md#macos-the-local-kitaru-server-crashes-mid-run).

## Part 1 — the config surface (`DECODE_ENV`)

> **Coming from an older `.env` ([ADR-0015 §4](../docs/adr/0015-environment-bucket-secrets.md))?**
> `RUNTIME_SECRET_NAME`, `RUNTIME_SECRET_STORE_CONFIG` and `RUNTIME_SECRET_STORE_MODEL_KEY` are **deleted**,
> and pydantic's `extra="ignore"` means a stale line in your `.env` is **silently ignored** — decode starts
> and never tells you the knob did nothing. Migrate: `make sync-secrets ENV=<env>` once, then
> `DECODE_ENV=<env>` on every run. Old per-credential secrets are dead weight:
> `uv run kitaru secrets delete <name>`.

`DECODE_ENV` decides **where `Settings` gets its values, and nothing else** — not session dirs, not log paths,
not `MEMORY.md`. It is the bootstrap variable, so it is read out-of-band (your `.env` file, overlaid by the
process env) *before* the chain is built.

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

Every line of that output is a design decision:

- **Key names only, never values** — in the diff, the confirmation, even a kitaru error (its stderr is
  redacted before printing).
- **REPLACES** — `kitaru secrets set` overwrites the *whole* key set, so the bucket is an exact **mirror**
  of your file; a key you delete from `.env` is gone on the next sync.
- **Skipped** keys are not `Settings` fields (`MODAL_TOKEN_ID`, …) — read from `os.environ`, the bucket
  could never feed them.
- **One-way** — `.env` → Kitaru, never back: dumping a prod bucket into a developer's working tree is the
  failure this design exists to prevent. `--yes` skips the prompt (CI).

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
| **Missing bucket** (or Kitaru local server down): `DECODE_ENV=prod uv run decode run "hi"` | ONE friendly stderr line, exit 1, **no traceback** — and it names the fix, not the missing key: *Decode: DECODE_ENV=prod but the environment bucket 'decode-prod' could not be loaded (it is missing, or the Kitaru local server is down) — run `make sync-secrets ENV=prod` (see running_the_code/06_credentials.md).* |
| Same, in the **TUI**: `DECODE_ENV=prod uv run decode` | The **same** line, exit 1 — the REPL is guarded before it starts. Both surfaces or it isn't a config surface. |
| **No backfill**: delete `GEMINI_API_KEY` from the bucket (`make sync-secrets ENV=staging` after removing it from `.env`), put it back in `.env`, then `env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode run "hi"` | `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` — it fails **loudly** even though the key is sitting right there in `.env`. That file is not in the chain at a remote env. **This is the point of having environments at all**: a provisioning gap must not be masked by a developer's laptop. |
| **Process env wins**: `GEMINI_API_KEY=<a-real-key> DECODE_ENV=staging uv run decode run "hi"` | It answers, using *your* key — precedence is always `process env > (.env \| bucket) > defaults`. Handy for a one-off override; also the escape hatch when a bucket key is stale. |

## Part 2 — the sandbox git token (`SANDBOX_GIT_TOKEN`)

One knob, one mechanism, **both backends** ([ADR-0016 §2](../docs/adr/0016-drop-credential-proxy.md)). Set it and
the token enters the **Worker**'s env as `GITHUB_TOKEN`:

| | how the value gets in | what it buys |
|---|---|---|
| `docker` | a **value-less** `-e GITHUB_TOKEN` on `docker run`; the value rides the docker **client's env**, so it never sits in a host-visible argv (no `ps`, no rendered error can read it) | the model can `git push` / `gh pr create` from inside `/workspace` |
| `modal` | a `modal.Secret` on the sandbox | the same |
| `none` | n/a — `none` *is* the host, with your own ambient credentials | n/a |

Both backends then chain the **same** git credential-helper (`x-access-token:$GITHUB_TOKEN`), so git's
HTTPS transport authenticates; `gh` reads `GITHUB_TOKEN` natively. Unset or empty → **no env var, no
credential helper, and a `docker run` argv byte-identical to the no-token case**. The sandbox holds no
credential at all.

> **⚠️ A sandboxed process CAN read `$GITHUB_TOKEN`.** That is the deliberate cost of deleting the Credential
> Proxy, stated plainly ([ADR-0016](../docs/adr/0016-drop-credential-proxy.md), *Consequences*). A prompt-injected
> agent can `echo $GITHUB_TOKEN`. The mitigation is **policy, not code**: hand it a **fine-grained,
> repo-scoped, revocable** PAT — never a broadly-scoped or organisation-wide one — and revoke it when you are
> done. Want the stronger property? **Leave `SANDBOX_GIT_TOKEN` unset** and take host-side hand-back (2a).

**Where does the token itself come from?** The one config surface: `.env` at `local`, the bucket at a remote
env — `sandbox_git_token()` reads the already-hydrated `Settings`, no second lookup. Mirror it like any other
key and `DECODE_ENV=staging SANDBOX_MODE=docker decode run --repo …` works with the token absent from your
shell entirely.

> **Where a tool call actually runs.** Only `bash` (and the file/search tools) run inside the Worker.
> **`web_fetch` runs host-side** — a plain `httpx` call in the decode process ([`tools/web.py`](../src/decode/tools/web.py)) —
> so it sees the *host's* network and none of the sandbox's environment. Ask the model to "GET this URL" and it
> will reach for `web_fetch`, never touching the Worker or its `GITHUB_TOKEN`. If you mean to exercise the
> sandbox, say **"use the bash tool"** in the prompt and check the run log for `running tool: bash`.

### 2a. OFF — the default: no credential in the sandbox, and the branch still comes back

```bash
rm -rf .decode/sandbox        # ← REQUIRED before any --repo run; see the box below

env -u SANDBOX_GIT_TOKEN SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line and commit it"
```

Working: the model commits inside `/workspace`, and on completion **hand-back** pushes a `decode/<session-id>`
branch to your repo — with your **ambient host git credentials**, because every hand-back git command is a
*host* subprocess against `.decode/sandbox` ([`sandbox/handback.py`](../src/decode/sandbox/handback.py),
[ADR-0012 §8](../docs/adr/0012-isolated-workspace.md)). Prove the Worker is credential-free, during the run, from a
second terminal:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep -i token     # EMPTY — no GITHUB_TOKEN, nothing
docker exec $WORKER git config --global --get credential.helper   # EMPTY — no helper either
```

**This is the safe default, and it already ships your work back.** You only need Part 2b for the strictly
larger ask: letting the **model itself** push and open the PR.

> ### `rm -rf .decode/sandbox` before *any* `--repo` run — not optional
>
> `--repo` clones **only into an empty Workspace** ([04_sandboxing.md](04_sandboxing.md)); a populated
> `.decode/sandbox` is reused and `--repo` **silently ignored** — no `origin`, and the push dies with a
> baffling `'origin' does not appear to be a git repository`. Confirm before you blame the token:
> `git -C .decode/sandbox remote -v` — a clone has an `origin`, a leftover scratch tree has none.

### 2b. ON — the model pushes the branch and opens the PR itself

```bash
rm -rf .decode/sandbox        # ← 2a left a populated Workspace behind

SANDBOX_GIT_TOKEN=<fine-grained-PAT> SANDBOX_MODE=docker \
  uv run decode run --repo https://github.com/<you>/<repo> \
  "create NOTES.md with one line, commit it, push the branch, then open a PR against main"
```

Working: the branch is pushed and the PR opens — from *inside* the sandbox, off the injected token. Both
`git` **and** `gh` are installed in the Worker (docker installs them per session; modal bakes them into a
cached image layer), so `git push` authenticates through the credential helper and `gh pr create` off
`GITHUB_TOKEN`. Verify the injection during the run:

```bash
WORKER=$(docker ps -q --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
docker exec $WORKER env | grep GITHUB_TOKEN                       # → GITHUB_TOKEN=<your PAT>  ← YES, it prints
docker exec $WORKER git config --global --get credential.helper   # → the x-access-token helper
ps aux | grep -F "<your-PAT>"                                     # EMPTY — never in a host argv
```

**That first line printing your PAT is the expected result**, and it is the whole honest cost of this design:
the Worker holds the token, so anything running in the Worker can read it. The third line is the one property
the docker path still buys you — the value rides the docker client's env, so it is not in `docker run`'s argv
and cannot be scraped from the host process table.

`SANDBOX_MODE=modal` is the same story with a `modal.Secret`, and the same warning applies (the token is in
the remote sandbox's env).

### 2c. Negatives — every way it must stay off

| Command | Working looks like |
|---|---|
| `SANDBOX_GIT_TOKEN= SANDBOX_MODE=docker uv run decode run "hi"` (explicit empty) | **nothing injected** — gated on the *value*, not on presence: no `GITHUB_TOKEN`, no credential helper, and an argv identical to the unset case |
| `SANDBOX_GIT_TOKEN=<PAT> uv run decode run "hi"` (mode `none`) | no-op — `none` *is* the host; there is no Worker env to inject into |
| A stale `SANDBOX_CREDENTIAL_PROXY_ENABLED=true` / `SANDBOX_PROXY_IMAGE=…` in your `.env` | **silently ignored** — both keys are deleted ([ADR-0016 §1](../docs/adr/0016-drop-credential-proxy.md)); no proxy container exists to start. `docker ps` shows the Worker, alone. |
| Docker daemon stopped, `SANDBOX_MODE=docker` | one friendly stderr line, exit non-zero, no flow built |

## Part 3 — cleanup

```bash
rm -rf .decode/sandbox                                # the Workspace Part 2 left behind
uv run kitaru secrets delete decode-staging           # the bucket, if you ran Part 1b
docker ps -a --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim   # empty — the Worker is reaped
```

And revoke the PAT you handed the sandbox in 2b. It was scoped and revocable — that was the point.

## Part 4 — the automated backstop

Everything above is covered without a PAT and without network:

```bash
# Environment Bucket — the chain per DECODE_ENV, the no-backfill property, the captured failure.
uv run pytest tests/unit/decode/config/test_env_bucket.py \
              tests/unit/decode/config/test_settings.py \
              tests/unit/decode/config/test_env_example_drift.py -v

# The sync script — full-surface replace, key-names-only output, one-way, the local refusal.
uv run pytest tests/unit/scripts/test_sync_secrets.py -v

# Token injection — the value-less -e passthrough, the shared credential helper, the empty-token
# byte-identical argv. Both backends, no docker, no modal.
uv run pytest tests/unit/decode/sandbox/test_docker_backend.py \
              tests/unit/decode/sandbox/test_modal_backend.py \
              tests/unit/decode/sandbox/test_workspace.py -v

# The capstone's one-mechanism claims: both backends share ONE helper + ONE gate; unset → no machinery.
uv run pytest tests/integration/test_sandbox_capstone.py -k token -v
```

[`test_sandbox_capstone.py`](../tests/integration/test_sandbox_capstone.py) asserts Part 2's manual claims:
one helper constant, one token gate, the secret in **no** argv; unset → no credential machinery; and, when a
docker daemon or Modal credentials exist, live injection with a dummy token (no GitHub call).
[`test_env_example_drift.py`](../tests/unit/decode/config/test_env_example_drift.py) is why
[`.env.example`](../.env.example) cannot lie: its `KEY=` lines and the `Settings` fields must match in **both**
directions — which also guarantees the retired proxy keys are really gone.
