# Getting Started

Install decode, point it at a model, run a first session. ~5 minutes. Enough for the first two lessons.

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

| Tool | Needed for | Install |
| --- | --- | --- |
| **[uv](https://docs.astral.sh/uv/)** | everything — also installs the pinned Python 3.12 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **git** | cloning the repo | preinstalled on macOS/Linux |

Supported: **macOS, Linux, Windows via [WSL2](https://learn.microsoft.com/windows/wsl/install)**. Native Windows untested (TUI keybindings assume a POSIX shell). Docker comes later ([03_sandboxing.md](03_sandboxing.md)).

> **✅ Checkpoint** — `uv --version` prints a version. `command not found` = not on PATH yet: restart your shell.

## 2. Install

```bash
git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + git hooks   (or just: uv sync)
make install-cli    # uv tool install --editable .  — `decode` tracks your source
```

`decode` not found afterward: `uv tool update-shell`, restart the shell. Uninstall: `make uninstall-cli`.

> **✅ Checkpoint** — `decode --version` prints `decode, version <x.y.z>`. Needs no API key.

## 3. Point decode at a model

```bash
cp .env.example .env
```

Fill in **one** provider:

- **[3a. Modal](#3a-modal--your-own-open-source-model-recommended) — recommended.** You serve an open-weights model; no rate limits. $30 signup credits ≈ 7 hours on the default 1×H100, enough for the whole course.
- **[3b. OpenRouter](#3b-openrouter--free-hosted-models) — hosted, free.** One key; low daily cap until you add $10 credit.
- **[3c. Gemini](#3c-gemini--one-key-fastest-start) — fastest first run.** One key, default provider. Rate limits bite quickly.

Switching later = a few lines in `.env`, no code change.

### 3a. Modal — your own open-source model (recommended)

```bash
# 1. authenticate the CLI ($30 credits on signup: https://modal.com?source=decodingai&campaign=harnesseng)
uv run modal token set --token-id <your-token-id> --token-secret <your-token-secret>

# 2. serve the course default — Modal picks GPU + serving recipe, prints the endpoint URL
uv run modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main

# 3. mint a token pair so the endpoint isn't open to the world
uv run modal workspace proxy-tokens create   # → Modal-Key: wk-... / Modal-Secret: ws-...
```

`uv run modal` because `modal` is a project dependency in the venv, not a global install. Step 1 is one-time: it writes `~/.modal.toml`.

Then in `.env`:

```bash
LLM_PROVIDER=modal

MODAL_ENDPOINT_URL=https://your-workspace--your-app.modal.run   # decode calls {url}/v1
MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8

MODAL_PROXY_TOKEN_ID=wk-...          # both, or neither (an --unauthenticated endpoint)
MODAL_PROXY_TOKEN_SECRET=ws-...
```

Lost the URL: `uv run modal endpoint list --env main`.

- **Two token pairs.** `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` authenticate the *CLI* — not decode settings, `.env` does nothing for them; `modal token set` or a shell export is what counts. `MODAL_PROXY_TOKEN_*` is how decode *calls* the model — both-or-neither (a half-set pair is a startup error, not a silent 401).
- **Credits.** Autoscaling **Min 0** (default) scales to zero between sessions. Stop an endpoint you're done with: `uv run modal endpoint stop <endpoint-id> --env main`.
- **Slow first turn** = cold start. `COMPACTION_CONTEXT_WINDOW_TOKENS=262144` skips decode's startup probe of `/v1/models`, the one request that waits on a cold endpoint.
- Other models, tuning, autoscaling, benchmarks: [02_modal_endpoints.md](02_modal_endpoints.md). Every lesson is tested against the default above.

> **✅ Checkpoint** — returns your model id; proves the URL and both proxy tokens:
>
> ```bash
> curl "$MODAL_ENDPOINT_URL/v1/models" \
>   -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" \
>   -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET"
> ```

### 3b. OpenRouter — free hosted models

One key at [openrouter.ai](https://openrouter.ai):

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
# OPENROUTER_MODEL=openrouter/free   # default — leave unless you want a specific model
```

`openrouter/free` = the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router): auto-routes across free **tool-capable** models. $10 credit raises the daily cap (~50 → ~1000 requests/day); free models stay $0. Pinning `OPENROUTER_MODEL=<slug>`: check it supports tool calling first.

> **✅ Checkpoint** — `grep -c '^OPENROUTER_API_KEY=sk-or-' .env` prints `1`.

### 3c. Gemini — one key (fastest start)

```bash
LLM_PROVIDER=gemini              # optional — gemini is the default
GEMINI_API_KEY=your-key-here     # free at https://aistudio.google.com/apikey
# GEMINI_MODEL=gemini-3.5-flash  # default
```

Expect 429s once you iterate hard (free-tier per-minute cap).

> **✅ Checkpoint** — `grep -c '^GEMINI_API_KEY=.\+' .env` prints `1`. A placeholder (`changeme`, blank) reads as unset and stops decode at startup with one line naming the variable.

### The provider matrix

| Provider (`LLM_PROVIDER`) | Model variable | Default | Notes |
| --- | --- | --- | --- |
| `modal` **(recommended)** | `MODAL_ENDPOINT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | your own endpoint, no rate limits, $30 credits ≈ 7h on 1×H100. Catalog: [02_modal_endpoints.md](02_modal_endpoints.md). |
| `gemini` (default) | `GEMINI_MODEL` | `gemini-3.5-flash` | free tier at [Google AI Studio](https://aistudio.google.com/apikey); rate-limited. |
| `openrouter` | `OPENROUTER_MODEL` | `openrouter/free` | needs `OPENROUTER_API_KEY`; free router across tool-capable models; $10 raises the daily cap. |

## 4. Turn on tracing (optional, 30 seconds)

Every turn ships as a trace, every model and tool call as a span with tokens, latency, cost.

![Opik Trace](../assets/opik_trace.gif)

Sign up at [comet.com](https://www.comet.com/signup?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course), copy the key from **Settings → API Keys**:

```bash
OPIK_API_KEY=your-key
```

Free plan: 25,000 spans/month — plenty.

> **✅ Checkpoint** — run one turn, open your Opik project: one trace, a span per model/tool call.

## 5. Run

From inside the cloned repo:

```bash
decode
```

![Decode REPL](../assets/decode_tui_plain.png)

Interactive REPL: type, the agent streams, every tool use **asks for approval first**.

| Action | Key |
| --- | --- |
| Send a message | `Enter` |
| **Steer** a running turn | `Enter` while it's working |
| **Follow-up** (queue for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn | `Esc` |
| Approve / deny a tool | `y` / `n` at the prompt |
| Quit | `Ctrl-D` or `/quit` |

Everything decode produces lands under **`<cwd>/.decode/`** (gitignored): `sessions/*.jsonl`, `MEMORY.md`, `logs/decode.log` (logs never hit the terminal).

> **✅ Checkpoint** — type `what files are in this directory?`. Working: the agent asks to run a read-only tool, streams a list, `.decode/sessions/` holds a `.jsonl`. Otherwise: [00_troubleshooting.md](00_troubleshooting.md).

### Resume a session

```bash
decode --resume               # most recent session in this directory
decode --resume <session-id>  # a specific one (id = filename stem)
```

### Memory

Standard `AGENTS.md` supported. `.decode/MEMORY.md` loads at startup; decode appends a one-sentence session summary on exit.

### Try a skill

Demo skills under `.decode/skills/`:

![Skills](../assets/demo-skills.png)

```bash
cd building-a-coding-agent-from-scratch-course
decode
/demo-1-terminal-arcade    # the agent builds a playable Snake game
```

`/commit` and `/review-diff` ship inside the package and work anywhere. To use the demos in your own project: `cp -r <course-repo>/.decode/skills/. ~/my-project/.decode/skills/`.

> **✅ Checkpoint** — type `/`; the completion menu lists the demos. Empty menu = launched somewhere without `.decode/skills/`.

Problems: [00_troubleshooting.md](00_troubleshooting.md).

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
