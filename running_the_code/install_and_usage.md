# Getting Started

Install **decode**, point it at an LLM provider, and run your first session — in about 5 minutes.

In a hurry? The whole path is five commands:

```bash
# 0. install uv first:  curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install
cp .env.example .env      # then set GEMINI_API_KEY (free: https://aistudio.google.com/apikey)
uv run decode
```

The rest is the same path, one step at a time, then a map of everything decode can do. Each step ends with a **✅ Checkpoint** — one command whose output tells you whether to move on or jump to [Troubleshooting](#troubleshooting). For the _why_ behind each piece, read the lessons ([course outline](../README.md#-course-outline)).

## 1. Prerequisites

Core setup needs two tools. Everything else is optional and only unlocks a side quest:

| Tool                                 | Needed for                                                                                 | Install                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| **[uv](https://docs.astral.sh/uv/)** | everything — it also installs the pinned Python 3.12 for you                               | `curl -LsSf https://astral.sh/uv/install.sh \| sh`             |
| **git**                              | cloning the repo (and the sandbox hand-back later)                                         | preinstalled on macOS/Linux                                    |
| Docker _(optional)_                  | only `SANDBOX_MODE=docker` — see [sandboxing.md](sandboxing.md)                            | [docker.com](https://www.docker.com/products/docker-desktop/)  |
| `modal` CLI _(optional)_             | only the remote sandbox or serving your own model — see [modal_models.md](modal_models.md) | ships with `make install`; authenticate with `modal token set` |
| `gcloud` _(optional)_                | only the cloud runtime stack — see [infra.md](infra.md)                                    | `brew install google-cloud-sdk`                                |

Supported on **macOS, Linux, and Windows via [WSL2](https://learn.microsoft.com/windows/wsl/install)**. Native Windows (PowerShell / cmd) is untested — the TUI keybindings and the sandbox paths assume a POSIX shell.

The optional rows are lesson-4-and-later concerns. Skip them now; each side quest's guide walks its own setup when you get there.

> **✅ Checkpoint** — `uv --version` prints a version. If it says `command not found`, uv is installed but not on your PATH yet: restart your shell.

## 2. Install

```bash
git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + wire git hooks   (or just: uv sync)
```

To type just **`decode`** from **any project directory**, put it on your PATH:

```bash
make install-cli    # uv tool install --editable .  — the command tracks your source
```

If `decode` isn't found afterward, run `uv tool update-shell` and restart your shell. Uninstall with `make uninstall-cli`.

> **✅ Checkpoint** — `uv run decode --version` prints `decode, version <x.y.z>`. That proves the venv, the pinned Python, and the entrypoint all resolve. It needs **no API key** — `--version` exits before any provider is built.

## 3. Get your keys

You need exactly **one** key to start — everything else is opt-in:

| Env var                                 | Where to get it                                                                                                                                | When you need it                                                                             |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `GEMINI_API_KEY`                        | [Google AI Studio](https://aistudio.google.com/apikey) — free tier                                                                             | **the default provider; the only key you need to start**                                     |
| `OPENROUTER_API_KEY`                    | [openrouter.ai](https://openrouter.ai) — `:free` models cost $0                                                                                | only `LLM_PROVIDER=openrouter`                                                               |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | [modal.com](https://modal.com?source=decodingai&campaign=harnesseng) — $30 free credits                                                        | only the Modal sandbox or a self-served model ([modal_models.md](modal_models.md))           |
| `OPIK_API_KEY`                          | [comet.com](https://www.comet.com/signup?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) — free tier | only tracing + evals; unset = silent no-op                                                   |
| `SANDBOX_GIT_TOKEN`                     | a GitHub fine-grained, repo-scoped PAT                                                                                                         | only to let the model itself push / open PRs from a sandbox ([sandboxing.md](sandboxing.md)) |

Every variable decode reads — required and optional, with comments — is documented in [`.env.example`](../.env.example).

> **✅ Checkpoint** — you have one key in your clipboard. A Gemini key starts with `AIza`; an OpenRouter one with `sk-or-`.

## 4. Configure

Config comes from environment variables, including a local `.env` (loaded via pydantic-settings). Precedence: **shell env var → `.env` → built-in default**. Missing required config prints a one-line hint and exits, never a traceback.

```bash
cp .env.example .env    # then set GEMINI_API_KEY=your-key-here
```

The agent loop runs on one selectable **LLM provider** — set `LLM_PROVIDER` plus that provider's key from step 3; each provider has its own model variable (the others are ignored):

| Provider (`LLM_PROVIDER`) | Model variable         | Default               | Notes                                                                                                                                                                                                                                                                               |
| ------------------------- | ---------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gemini` (default)        | `GEMINI_MODEL`         | `gemini-3.5-flash`    | free credits on [Google AI Studio](https://aistudio.google.com/apikey)                                                                                                                                                                                                              |
| `openrouter`              | `OPENROUTER_MODEL`     | `openrouter/free`     | the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) — auto-routes across free tool-capable models so one congested provider can't 429-block you. Adding $10 credits raises the free daily cap (~50 → ~1000 req/day); free models still cost $0. |
| `modal`                   | `MODAL_ENDPOINT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | serve your own model — see [`modal_models.md`](modal_models.md) for picking a model and creating the endpoint.                                                                                                                                                                 |

**Pick a tool-capable model.** The loop needs tool-calling + streaming; the shipped defaults are known-good. Swap to a pinned model that lacks tool support and the loop breaks (the model narrates instead of calling tools). The wiring decision is [ADR-0005](../docs/adr/0005-multi-llm-provider-support.md).

> **✅ Checkpoint** — `grep -c '^GEMINI_API_KEY=.\+' .env` prints `1`. Decode treats an unfilled placeholder (`changeme`, or blank) as **unset** and stops at startup with one line naming the variable, so a half-filled `.env` never reaches the provider.

## 5. Run

```bash
cd ~/my-project     # decode operates on the directory you launch it from
decode              # after `make install-cli` (or `uv run decode` from the repo)
decode --resume     # continue the most recent session here
```

You get an interactive REPL: type a message, the agent streams a reply, and every tool use **asks for approval first**.

| Action                                        | Key                            |
| --------------------------------------------- | ------------------------------ |
| Send a message                                | `Enter`                        |
| **Steer** a running turn (redirect it now)    | `Enter` while it's working     |
| **Follow-up** (queue work for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn                    | `Esc`                          |
| Approve / deny a tool                         | type `y` / `n` at the prompt   |
| Quit                                          | `Ctrl-D` or `/quit`            |

Everything decode produces lands under **`<cwd>/.decode/`** (gitignored): `sessions/*.jsonl` (replayable transcripts), `MEMORY.md` (cross-session memory), `logs/decode.log` (logs stay off the terminal).

> **✅ Checkpoint** — type `what files are in this directory?` and press `Enter`. Working looks like: the agent asks to run a read-only tool, streams a list back, and `.decode/sessions/` now holds a `.jsonl` transcript. If the answer never streams, see [Troubleshooting](#troubleshooting).

**Try a skill.** Skills are reusable playbooks in `.decode/skills/<name>/SKILL.md`, triggered with `/<name>` (or invoked by the agent itself). Decode loads them from the directory you **launched** it in, so run this one from inside the course repo — that's where the six demos live:

```bash
cd building-a-coding-agent-from-scratch-course
decode
/demo-1-terminal-arcade    # the agent builds a playable Snake game
```

Two skills (`/commit`, `/review-diff`) ship inside the package and work from any directory. To use the demos in your own project, copy them over: `cp -r <course-repo>/.decode/skills/. ~/my-project/.decode/skills/`.

> **✅ Checkpoint** — type `/` and the completion menu lists the demos. An empty menu means decode was launched somewhere without a `.decode/skills/` directory.

## Troubleshooting

Decode's startup guards check **presence only** and print one line, never a traceback. Match the line you got:

| What you see | What it means | Fix |
| --- | --- | --- |
| `decode: command not found` | `make install-cli` ran, but uv's tool bin isn't on your PATH | `uv tool update-shell`, restart the shell. Or skip the CLI install and use `uv run decode` from the repo. |
| `Decode: set GEMINI_API_KEY in your environment or .env to start` | no key, an empty one, or an unfilled placeholder (`changeme`) — all three read as unset | put a real key in `.env`. Confirm with the step-4 checkpoint. |
| `Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY …` | provider switched, its key not set | set `OPENROUTER_API_KEY`, or drop `LLM_PROVIDER` to fall back to Gemini. |
| `429` / quota errors mid-turn | the Gemini free tier's per-minute or daily cap | wait out the minute, or switch to `LLM_PROVIDER=openrouter` — its default `openrouter/free` router spreads across free models. $10 of credit raises the daily cap (~50 → ~1000 req/day); free models still cost $0. |
| The agent **describes** the tool it would use instead of using it | the pinned model has no tool-calling | go back to a shipped default (`gemini-3.5-flash`, `openrouter/free`). Tool support is not optional for the loop. |
| `Decode: no known context window for model …; assuming …` | informational — the model isn't in the static table and the probe didn't answer | harmless; set `COMPACTION_CONTEXT_WINDOW_TOKENS` to silence it. See [Context window resolution](#context-window-resolution). |
| `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable` | `SANDBOX_MODE=docker` with Docker stopped | start Docker Desktop, or set `SANDBOX_MODE=none`. |
| `Decode: SANDBOX_MODE=modal but Modal credentials are missing` | no Modal account tokens in the process env | `modal token set …` (writes `~/.modal.toml`). Account tokens are **not** settings — `.env` does nothing for them. |
| `Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace …` | `--repo` passed without a sandbox mode | set `SANDBOX_MODE=docker` or `modal`, or drop `--repo`. |
| `/` shows no demo skills | decode was launched outside the course repo — skills load from the **launch** directory | relaunch from the repo, or copy `.decode/skills/` into your project. |
| LSP tools return nothing useful | the `ty` server is missing or slow; LSP is best-effort and degrades silently by design | it's a dev-group dependency — `make install` (not a bare `uv sync --no-dev`) installs it. |
| `uv sync` complains the lockfile is out of date | `pyproject.toml` and `uv.lock` drifted | `uv lock` then `make install`. `make ci` enforces this with `uv lock --check`. |

**Still stuck?** Logs never touch the terminal — they're at `.decode/logs/decode.log` under the directory you launched from. Turn up the detail with `LOG_LEVEL=DEBUG` in `.env`, reproduce, then read the tail. If that doesn't explain it, [open an issue](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/issues) with that tail and your provider + model.

## What decode can do

The full feature map — each row is one line here and a deep dive one click away:

| Feature                       | What it does                                                                                                                                                                                                                                                             | Deep dive                                                                                      |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| **Gated tools**               | `read` · `glob` · `grep` · `lsp` · `agent` · `write` · `edit` · `bash` · `todo_write` · `web_fetch` · `ask_user`. Read-only tools auto-allow; everything else asks `y/n` first.                                                                                          | [ADR-0002](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)                         |
| **Permission modes & agents** | allow/ask/deny rules, modes (default/plan/edit/bypass), and a Build/Plan/Code-Reviewer agents catalog.                                                                                                                                                                   | [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md)               |
| **Skills**                    | reusable playbooks triggered with `/<name>` — see [Try a skill](#5-run) above.                                                                                                                                                                                           | [ADR-0004](../docs/adr/0004-milestone-3-skills.md)                                             |
| **Memory**                    | loads `AGENTS.md` + `.decode/MEMORY.md` into context; appends a one-sentence session summary on exit.                                                                                                                                                                    | [ADR-0002](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)                         |
| **Sessions & resume**         | every session is a replayable JSONL transcript; `decode --resume [<session-id>]` continues it.                                                                                                                                                                           | [ADR-0002](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)                         |
| **Multi-provider inference**  | one seam, three providers: Gemini, OpenRouter, or a model you serve on Modal.                                                                                                                                                                                            | [ADR-0005](../docs/adr/0005-multi-llm-provider-support.md), [modal_models.md](modal_models.md) |
| **Context compaction**        | automatic budget-keeping: cheap trim near ~60% of the window, one-LLM-call summary near ~80% (or `/compact`); footer gauge tracks it. The window is resolved per run for the model that run actually uses — see [Context window resolution](#context-window-resolution). | [ADR-0006](../docs/adr/0006-conversation-compaction.md)                                        |
| **LSP code intelligence**     | `definition` / `references` / `hover` / `diagnostics` on demand, plus post-edit type-checking so the agent fixes its own mistakes.                                                                                                                                       | [ADR-0007](../docs/adr/0007-lsp-integration.md)                                                |
| **Explore subagents**         | read-only children fan out in parallel (up to 4) and hand back compressed reports instead of flooding the main context.                                                                                                                                                  | [ADR-0013](../docs/adr/0013-explore-subagents.md)                                              |
| **Sandboxing**                | move all tools into an isolated Docker or Modal Workspace; work on any repo with `--repo`; get the work back as a git branch.                                                                                                                                            | [sandboxing.md](sandboxing.md)                                                                 |
| **Headless runtime**          | `decode run "<task>"` — unattended, durable (checkpoint per call, survives crashes), optional `--hitl` waits.                                                                                                                                                            | [runtime.md](runtime.md)                                                                       |
| **Replay & what-if**          | re-run any recorded execution from any anchor with the model swapped.                                                                                                                                                                                                    | [runtime.md](runtime.md)                                                                       |
| **Environments & secrets**    | `DECODE_ENV` selects `.env` (local) vs the Environment Bucket (deployed); secrets never reach the model's context.                                                                                                                                                       | [credentials.md](credentials.md)                                                               |
| **Observability**             | one `OPIK_API_KEY` and every turn ships a full trace — every model/tool call as a span with tokens, latency, cost.                                                                                                                                                       | [ADR-0014](../docs/adr/0014-opik-observability.md)                                             |
| **Evals**                     | outcome benchmark, behavior regression probes, LLM judges, online evals (`make eval-benchmark` / `make eval-regression`).                                                                                                                                                | [running_the_code/evals.md](evals.md)                                                          |
| **Cloud deployment**          | the whole headless agent on Modal, checkpoints on a self-hosted [Kitaru](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand) server.                                                       | [infra.md](infra.md)                                                                           |

### Context window resolution

Compaction and the footer gauge both need one number: your model's max **input** window. Decode
resolves it per run, for the model that run actually uses — so `decode run "…" --model <id>` uses
`<id>`'s window, not the one in your `.env`. First hit wins:

| #   | Source                             | Notes                                                                                                                                                                                      |
| --- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `COMPACTION_CONTEXT_WINDOW_TOKENS` | If you set it, it wins outright and **no probe runs** — you own the number.                                                                                                                |
| 2   | A provider probe                   | modal / any OpenAI-compatible endpoint: `GET {url}/v1/models` → `max_model_len`. Gemini: `models.get(model=…)` → `input_token_limit`. OpenRouter: `GET /api/v1/models` → `context_length`. |
| 3   | A small static table               | Gemini 2.5/3.5 → 1,048,576; Qwen3.6-35B-A3B → 262,144.                                                                                                                                     |
| 4   | `200000`                           | The conservative assumption. Decode says so once on stderr at startup.                                                                                                                     |

The probe is best effort: at most once per model per process, short timeout, never on `--help`/`--version`, never fatal — any failure falls silently through to the table, then the fallback. The winning source lands in the debug log. Set `COMPACTION_CONTEXT_WINDOW_TOKENS` if your endpoint cold-starts slowly (an idle Modal GPU can take a while to answer `/v1/models`) or reports a window you don't trust.

## Develop

All verbs run at the repo root via the [`Makefile`](../Makefile) (wrapping `uv`):

```bash
make test            # full test suite (unit + integration)
make unit-tests      # unit only
make lint-check      # ruff check        (lint-fix to auto-fix)
make format-check    # ruff format check (format-fix to apply)
make pre-commit      # format + lint + unit tests (the fast gate)
make ci              # what CI runs: lockfile check + format + lint + full suite
make help            # all targets
```

Tests mirror `src/` 1:1 under `tests/`; model calls use Pydantic AI's `TestModel`/`FunctionModel`, so the suite needs **no network and no API key**. Conventions and the development workflow live in [`AGENTS.md`](../AGENTS.md); design decisions in [`docs/adr/`](../docs/adr/); canonical terms in [`docs/glossary.md`](../docs/glossary.md).
