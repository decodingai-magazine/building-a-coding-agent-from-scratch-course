# Getting Started

Install **decode**, point it at a model, and run your first session — in about 5 minutes.

This guide is enough to set up the interactive mode of decode required for the first two lessons.

## 0. Quickstart

```bash
# 0. install uv first:  curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install
cp .env.example .env      # then set GEMINI_API_KEY (free: https://aistudio.google.com/apikey)
uv run decode
```

## 1. Prerequisites

| Tool                                 | Needed for                                                   | Install                                            |
| ------------------------------------ | ------------------------------------------------------------ | -------------------------------------------------- |
| **[uv](https://docs.astral.sh/uv/)** | everything — it also installs the pinned Python 3.12 for you | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **git**                              | cloning the repo                                             | preinstalled on macOS/Linux                        |

Supported on **macOS, Linux, and Windows via [WSL2](https://learn.microsoft.com/windows/wsl/install)**. Native Windows (PowerShell / cmd) is untested — the TUI keybindings assume a POSIX shell.

That's the whole list. Docker is required for a later lesson — [03_sandboxing.md](03_sandboxing.md) walks you through it. (`gcloud` is not: the self-hosted stack that needed it is retired — remote runs live on Modal, [04_deploy.md](04_deploy.md).)

> **✅ Checkpoint** — `uv --version` prints a version. If it says `command not found`, uv is installed but not on your PATH yet: restart your shell.

## 2. Install

```bash
git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + wire git hooks   (or just: uv sync)
```

Then install `decode` as a CLI tool:

```bash
make install-cli    # uv tool install --editable .  — the command tracks your source
```

If `decode` isn't found afterward, run `uv tool update-shell` and restart your shell. Uninstall with `make uninstall-cli`.

> **✅ Checkpoint** — `decode --version` prints `decode, version <x.y.z>`. That proves the venv, the pinned Python, and the entrypoint all resolve. It needs **no API key** — `--version` exits before any provider is built.

## 3. Point decode at a model

To wrap up the installation, you need to configure a few environment variables.

From the repo root, run:

```bash
cp .env.example .env
```

Now fill it in with **one** of the three model providers:

- **[3a. Modal](#3a-modal--your-own-open-source-model-recommended) — what we recommend.** You serve an open-weights model yourself, so there is no rate limit to hit: the $30 signup credits buy roughly 7 hours on the 1×H100 the default runs on, which is enough to run the whole course. You also get to watch your own open-weights model drive a harness you built, rather than a black box behind someone's API.
- **[3b. OpenRouter](#3b-openrouter--free-hosted-models) — hosted, still free.** One key, no GPU to manage; the free router spreads your calls across free tool-capable models. The daily cap is low until you add credit — $10 raises it.
- **[3c. Gemini](#3c-gemini--one-key-fastest-start) — the fastest first run.** One key and it's the default provider, so nothing else to set. Unfortunately, you will quickly hit its rate limits, which makes it annoying to use.

Switching later is a few lines in `.env` and no code change — so start wherever you'll be running in 60 seconds, then we recommend moving to Modal when rate limits start costing you time.

### 3a. Modal — your own open-source model (recommended)

You serve an open-weights model on a GPU, and decode talks to it over an OpenAI-compatible endpoint. Three commands and three env vars:

```bash
# 1. authenticate the CLI ($30 credits on signup: https://modal.com?source=decodingai&campaign=harnesseng)
uv run modal token set --token-id <your-token-id> --token-secret <your-token-secret>

# 2. serve the course default — Modal picks the GPU + serving recipe and prints the endpoint URL
uv run modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main

# 3. mint a token pair so your endpoint isn't open to the world
uv run modal workspace proxy-tokens create   # → Modal-Key: wk-... / Modal-Secret: ws-...
```

> **Why `uv run modal`?** The `modal` CLI is a project dependency, not something you install separately — `make install` already put it in the venv, so `uv` finds it from the repo root. A bare `modal …` only works if you've activated the venv yourself. Step 1 is one-time: it writes `~/.modal.toml` in your home directory, and every later `uv run modal …` picks it up.

Then put the endpoint in your `.env`:

```bash
LLM_PROVIDER=modal

MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

MODAL_ENDPOINT_URL=https://your-workspace--your-app.modal.run   # decode calls {url}/v1
MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8

MODAL_PROXY_TOKEN_ID=wk-...          # both, or neither (an --unauthenticated endpoint)
MODAL_PROXY_TOKEN_SECRET=ws-...
```

Lost the URL? `uv run modal endpoint list --env main`.

**Two account tokens, two endpoint tokens — don't mix them up.** `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` authenticate the _CLI_; they are **not** decode settings, so putting them in `.env` does nothing — `modal token set` (which writes `~/.modal.toml`) or exporting them in your shell is what counts. The `MODAL_PROXY_TOKEN_*` pair above is a different thing: it's how decode _calls_ your served model, and it is both-or-neither (a half-set pair is a friendly startup error, never a silent 401).

**Watch your credits.** A GPU you keep warm bills while idle. Autoscaling **Min 0** (the default) scales to zero between sessions — you pay for the first cold start instead of the idle hour. Stop an endpoint you're done with: `uv run modal endpoint stop <endpoint-id> --env main`.

**If the first turn is slow**, that's the cold start waking the GPU. Setting `COMPACTION_CONTEXT_WINDOW_TOKENS=262144` skips decode's startup probe of `/v1/models`, which is the one request that has to wait on a cold endpoint — decode otherwise reads the window from the endpoint, then a static table, then assumes a conservative `200000`.

**Picking a different model, endpoint tuning, autoscaling, benchmarks, and the cost/capability ladder** all live in [`02_modal_endpoints.md`](02_modal_endpoints.md). The default above is what every lesson is built and tested against.

> **✅ Checkpoint** — this returns your model id, and proves the URL and both proxy tokens are right:
>
> ```bash
> curl "$MODAL_ENDPOINT_URL/v1/models" \
>   -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" \
>   -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET"
> ```

### 3b. OpenRouter — free hosted models

One key at [openrouter.ai](https://openrouter.ai), two lines in `.env`:

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
# OPENROUTER_MODEL=openrouter/free   # the default — leave it alone unless you want a specific model
```

The default `openrouter/free` is the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router): it auto-routes across free **tool-capable** models, so one congested upstream can't 429-block your loop. Free models cost $0; adding $10 of credit raises the free daily cap (~50 → ~1000 requests/day) without making the free models paid.

Pin a specific model with `OPENROUTER_MODEL=<slug>` — but check that it supports tool calling first, or the loop breaks.

> **✅ Checkpoint** — `grep -c '^OPENROUTER_API_KEY=sk-or-' .env` prints `1`.

### 3c. Gemini — one key (fastest start)

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here     # free at https://aistudio.google.com/apikey
# GEMINI_MODEL=gemini-3.5-flash  # the default — leave it alone unless you want a specific model
```

That's it — `gemini` is the default provider, so no `LLM_PROVIDER` line is needed. Expect 429s once you start iterating hard; that's the free tier's per-minute cap, and the reason the course recommends Modal.

> **✅ Checkpoint** — `grep -c '^GEMINI_API_KEY=.\+' .env` prints `1`. Decode treats an unfilled placeholder (`changeme`, or blank) as **unset** and stops at startup with one line naming the variable, so a half-filled `.env` never reaches the provider.

### The provider matrix

| Provider (`LLM_PROVIDER`) | Model variable         | Default                    | Notes                                                                                                                                                                                                                                                                                                    |
| ------------------------- | ---------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `modal` **(recommended)** | `MODAL_ENDPOINT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | your own endpoint — no rate limits, $30 credits ≈ ~7h on 1×H100. Setup above; catalog in [`02_modal_endpoints.md`](02_modal_endpoints.md).                                                                                                                                                               |
| `gemini` (default)        | `GEMINI_MODEL`         | `gemini-3.5-flash`         | free tier at [Google AI Studio](https://aistudio.google.com/apikey); rate-limited.                                                                                                                                                                                                                       |
| `openrouter`              | `OPENROUTER_MODEL`     | `openrouter/free`          | needs `OPENROUTER_API_KEY`. The [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) auto-routes across free tool-capable models so one congested provider can't 429-block you. $10 of credit raises the free daily cap (~50 → ~1000 req/day); free models still cost $0. |

## 4. Turn on tracing (optional, 30 seconds)

Tracing is what turns the loop from a wall of streamed text into something you can inspect: every turn ships as a trace, every model and tool call as a span with its tokens, latency, and cost. When the agent does something surprising, this is where you find out why.

![Opik Trace](../assets/opik_trace.gif)

**Get the key:** sign up at [comet.com](https://www.comet.com/signup?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) (Opik is Comet's LLM observability product), then copy your API key from **Settings → API Keys**.

```bash
OPIK_API_KEY=your-key
```

**It stays free.** Opik's free plan includes **25,000 spans/month** — far more than this course needs.

> **✅ Checkpoint** — run one turn, then open your Opik project: the turn appears as a trace with a span per model/tool call.

## 5. Run

The easiest way is to run `decode` from inside the `building-a-coding-agent-from-scratch-course` repo you just cloned.

```bash
decode
```

![Decode REPL](../assets/decode_tui_plain.png)

You get an interactive REPL: type a message, the agent streams a reply, and every tool use **asks for approval first**.

| Action                                        | Key                            |
| --------------------------------------------- | ------------------------------ |
| Send a message                                | `Enter`                        |
| **Steer** a running turn (redirect it now)    | `Enter` while it's working     |
| **Follow-up** (queue work for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn                    | `Esc`                          |
| Approve / deny a tool                         | type `y` / `n` at the prompt   |
| Quit                                          | `Ctrl-D` or `/quit`            |

Everything decode produces is saved under **`<cwd>/.decode/`** (gitignored): `sessions/*.jsonl` (replayable sessions), `MEMORY.md` (cross-session memory), `logs/decode.log` (logs stay off the terminal).

> **✅ Checkpoint** — type `what files are in this directory?` and press `Enter`. Working looks like: the agent asks to run a read-only tool, streams a list back, and `.decode/sessions/` now holds a `.jsonl` transcript. If the answer never streams, see [00_troubleshooting.md](00_troubleshooting.md).

### Resume a session

Every session is a replayable JSONL transcript under `.decode/sessions/`, keyed by session id:

```bash
decode --resume               # continue the most recent session in this directory
decode --resume <session-id>  # continue a specific one (the id is the filename stem)
```

### Memory

decode supports the standard `AGENTS.md`.

Also, `.decode/MEMORY.md` loads into context at startup, and decode appends a one-sentence summary of the session on exit — so a fresh session already knows what the last one did.

### Try a skill

We prepared a set of default skills under `.decode/skills/` as demos, so you can try the coding agent on something familiar.

![Skills](../assets/demo-skills.png)

```bash
cd building-a-coding-agent-from-scratch-course
decode
/demo-1-terminal-arcade    # the agent builds a playable Snake game
```

Two skills (`/commit`, `/review-diff`) ship inside the package and work from any directory. To use the demos in your own project, copy them over: `cp -r <course-repo>/.decode/skills/. ~/my-project/.decode/skills/`.

> **✅ Checkpoint** — type `/` and the completion menu lists the demos. An empty menu means decode was launched somewhere without a `.decode/skills/` directory.

**Something not working?** Every known failure and its fix is in [00_troubleshooting.md](00_troubleshooting.md).

## 6. Environments — `DECODE_ENV` and the Environment Bucket (optional)

Everything so far read one file: `.env`. That is the whole story until a run leaves your laptop. `Settings` ([`config/settings.py`](../src/decode/config/settings.py)) is the **single source of truth** for every credential decode holds — nothing else reads one — so there is only ever one interesting question, **how does a value get *into* `Settings`?**, and `DECODE_ENV` is the whole answer ([ADR-0015](../docs/adr/0015-environment-bucket-secrets.md)):

| `DECODE_ENV` | The source chain (highest first) |
|---|---|
| `local` (default) | process env → **`.env`** → defaults. Kitaru is never imported. |
| `dev` / `staging` / `prod` | process env → **the Environment Bucket** (`decode-<env>`) → defaults. **`.env` is dropped from the chain entirely.** |

One surface, two injection mechanisms, selected by one variable. Values land in `Settings` **only** — never `os.environ` — so a model-chosen `bash` never inherits one. `DECODE_ENV` decides **where `Settings` gets its values, and nothing else** — not session dirs, not log paths, not `MEMORY.md`. It is the bootstrap variable, so it is read out-of-band (your `.env` file, overlaid by the process env) *before* the chain is built.

The Environment Bucket **is** a named [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) secret on the managed workspace, read through the kitaru client API (`KitaruClient().api.secrets`) — the only secret call in the codebase. Two things it is **not**: the Modal Secrets the remote apps read ([04_deploy.md §4](04_deploy.md#4-secrets--two-deliberately-asymmetric) — those outrank `.env` in the process env, so `DECODE_ENV` stays `local` in a container), and the secrets a replay's process holds ([04_deploy.md §6](04_deploy.md#6-replay-a-recorded-session-on-a-kitaru-worker)).

Every case below is an **A/B**: the same command with one thing flipped, and a different observable. 6a needs nothing beyond `.env`; 6b+ need `uv run kitaru status` to say `"authentication": "authenticated"` (else `uv run kitaru login https://<your-workspace>.cloudinfra.zenml.io`).

### 6a. OFF — `local`, and the invariant that comes with it

The claim: at the default env, decode does not import kitaru at all. It is a one-liner to check, and the same one-liner is the B side of the A/B:

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

Working: `False` at `local`, `True` at a remote env. That second import is the cost of an environment — the kitaru client and a network round trip to the workspace before the first prompt — and it is exactly why `local` is the default. (Recording is the *other* thing that imports kitaru, and it is opt-in too: [04_deploy.md §2](04_deploy.md#2-record-runs-as-kitaru-sessions-opt-in).) Note the free side-effect: the Opik project follows the environment (`decode-local` / `decode-staging`), so traces self-sort. Set `OPIK_PROJECT_NAME` explicitly and your value always wins.

`local` reads `.env` and there is nothing to mirror, so the sync script refuses outright:

```bash
make sync-secrets ENV=local
# → Error: `local` reads your .env directly — there is nothing to sync. Pick dev, staging or prod.
```

### 6b. ON — mirror `.env` into the Environment Bucket

The bucket name is **derived** (`decode-<env>`); there is no override knob, so "`DECODE_ENV=staging` pointed at the prod bucket" is unrepresentable. One command writes it:

```bash
make sync-secrets ENV=staging       # → uv run python scripts/sync_secrets.py --env staging
```

```
Mirroring .env → decode-staging (key names only; values are never printed).
decode-staging does not exist yet — it will be created.
Skipped (not a Settings field): MODAL_TOKEN_ID
  + GEMINI_API_KEY
  + OPENROUTER_API_KEY
This REPLACES the entire contents of decode-staging with these 2 key(s) — the write swaps the secret's whole key set, it does not merge into it.
Proceed? [y/N]:
```

Every line of that output is a design decision:

- **Key names only, never values** — in the diff, the confirmation, even a kitaru error (its stderr is redacted before printing).
- **REPLACES** — the write swaps the secret's *whole* key set (kitaru's PATCH does not merge), so the bucket is an exact **mirror** of your file; a key you delete from `.env` is gone on the next sync.
- **Skipped** keys are not `Settings` fields (`MODAL_TOKEN_ID`, …) — read from `os.environ`, the bucket could never feed them ([02_modal_endpoints.md](02_modal_endpoints.md#authenticate-the-cli)).
- **One-way** — `.env` → Kitaru, never back: dumping a prod bucket into a developer's working tree is the failure this design exists to prevent. `--yes` skips the prompt (CI).

Confirm it landed — names only, and with the same command: re-run the sync and answer **N**. The diff it prints *is* the read of the bucket (`=` unchanged, `~` changed, `+` added, `-` dropped), and nothing is written:

```bash
make sync-secrets ENV=staging       # answer N at "Proceed? [y/N]" → "Aborted — nothing was written…"
```

### 6c. ON — run against the bucket, with the key absent from your environment

```bash
env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode run "say hi in exactly three words"
env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode                    # the TUI, identically
```

Working: it answers. No provider key was in the process env, `.env` was not in the chain, and nothing was written to `os.environ` — the whole surface was hydrated into `Settings` from `decode-staging` at singleton construction, so the TUI and the headless flow behave identically (hydration is process-scoped, not a headless-only toggle).

### 6d. Negatives — the four ways this must fail (and win)

| Command | Working looks like |
|---|---|
| **Missing bucket** (or an unreachable workspace): `DECODE_ENV=prod uv run decode run "hi"` | ONE friendly stderr line, exit 1, **no traceback** — and it names the fix, not the missing key: *Decode: DECODE_ENV=prod but the environment bucket 'decode-prod' could not be loaded (no such secret on the Kitaru workspace, or this machine cannot reach it — check `kitaru login` / KITARU_API_URL) — run `make sync-secrets ENV=prod` (see running_the_code/01_install_and_usage.md).* |
| Same, in the **TUI**: `DECODE_ENV=prod uv run decode` | The **same** line, exit 1 — the REPL is guarded before it starts. Both surfaces or it isn't a config surface. |
| **No backfill**: delete `GEMINI_API_KEY` from the bucket (`make sync-secrets ENV=staging` after removing it from `.env`), put it back in `.env`, then `env -u GEMINI_API_KEY DECODE_ENV=staging uv run decode run "hi"` | `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` — it fails **loudly** even though the key is sitting right there in `.env`. That file is not in the chain at a remote env. **This is the point of having environments at all**: a provisioning gap must not be masked by a developer's laptop. |
| **Process env wins**: `GEMINI_API_KEY=<a-real-key> DECODE_ENV=staging uv run decode run "hi"` | It answers, using *your* key — precedence is always `process env > (.env \| bucket) > defaults`. Handy for a one-off override; also the escape hatch when a bucket key is stale. |

### 6e. Cleanup, and the automated backstop

The `decode-staging` bucket is deleted from the workspace dashboard (`uv run kitaru status` prints its URL) — kitaru 0.22.x has no `secrets` CLI. Leaving it costs nothing as long as `DECODE_ENV` is `local`.

Everything above is covered without network:

```bash
# Environment Bucket — the chain per DECODE_ENV, the no-backfill property, the captured failure.
uv run pytest tests/unit/decode/config/test_env_bucket.py \
              tests/unit/decode/config/test_settings.py \
              tests/unit/decode/config/test_env_example_drift.py -v

# The sync script — full-surface replace, key-names-only output, one-way, the local refusal.
uv run pytest tests/unit/scripts/test_sync_secrets.py -v
```

[`test_env_example_drift.py`](../tests/unit/decode/config/test_env_example_drift.py) is why [`.env.example`](../.env.example) cannot lie: its `KEY=` lines and the `Settings` fields must match in **both** directions.

## Develop

```bash
make test            # full test suite (unit + integration)
make unit-tests      # unit only
make lint-check      # ruff check        (lint-fix to auto-fix)
make format-check    # ruff format check (format-fix to apply)
make pre-commit      # format + lint + unit tests (the fast gate)
make ci              # what CI runs: lockfile check + format + lint + full suite
make help            # all targets
```
