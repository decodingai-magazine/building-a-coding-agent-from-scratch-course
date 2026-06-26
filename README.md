# decode

**decode** is a terminal **coding agent** you run in your terminal — a [Pydantic AI](https://ai.pydantic.dev) ReAct loop on a selectable LLM provider (**Gemini**, **OpenRouter**, or a model you serve on **Modal**), driving file / bash / web tools behind a `prompt_toolkit` + `Rich` TUI, with an ask-before-every-tool permission gate, project memory, and replayable sessions.

This repository is an **educational, open-source course** that builds `decode` from scratch, step by step. It is a single Python package (`decode`); each module under `src/decode/` maps to one part of the architecture.

> **Status — Milestone 1 (vanilla on-device agent).** What's built today: the agent loop with **selectable LLM providers** (Gemini / OpenRouter / Modal — run it for free; see [LLM providers](#llm-providers)), the steering TUI, the core tools, the permission gate, memory, and a session log. Later milestones add sandboxing, observability (Opik), a durability runtime (Kitaru), and MCP — see [`AGENTS.md`](AGENTS.md) and the architecture decisions in [`docs/adr/0002-milestone-1-vanilla-agent-architecture.md`](docs/adr/0002-milestone-1-vanilla-agent-architecture.md) and [`docs/adr/0005-multi-llm-provider-support.md`](docs/adr/0005-multi-llm-provider-support.md).

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — the package/runtime manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`). uv installs the pinned Python 3.12 for you.
- **An API key for one LLM provider.** By default that's a **Gemini API key** ([Google AI Studio](https://aistudio.google.com/apikey) — free tier); you can instead run for free on **OpenRouter** (`:free` models) or a model you serve on **Modal** ($30 free credits). See [LLM providers](#llm-providers).

## Install

```bash
git clone git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + wire git hooks   (or just: uv sync)
```

### Run `decode` as a CLI tool (recommended)

`make install` lets you run the agent with `uv run decode`. To type just **`decode`** from **any project directory**, put it on your PATH:

```bash
make install-cli    # uv tool install --editable .  — the command tracks your source
```

Then `cd` into any project and run it directly:

```bash
cd ~/my-project
decode              # start a fresh session in this directory
decode --resume     # continue the most recent session here
```

`decode` always operates on the directory you launch it from (its working dir), and writes everything it produces under **`<cwd>/.decode/`** in that project:

- `.decode/sessions/*.jsonl` — replayable session transcripts (used by `decode --resume`).
- `.decode/MEMORY.md` — the one-sentence-per-session memory the agent appends on exit and reloads next time.
- `.decode/logs/decode.log` — the log file (logs go here, **not** to the terminal, so the REPL stays clean).

The whole `.decode/` directory is gitignored.

If `decode` isn't found afterward, run `uv tool update-shell` and restart your shell. Uninstall with `make uninstall-cli`.

## Configure

`decode` reads its config from environment variables, including a local `.env` file (loaded via pydantic-settings):

```bash
cp .env.example .env
# then edit .env and set:
#   GEMINI_API_KEY=your-key-here
#   GEMINI_MODEL=gemini-2.5-flash   # optional; this is the default
```

`.env` is gitignored. Precedence is **shell env var → `.env` → built-in default**. (If the selected provider's required config is missing, `decode` prints a one-line hint and exits instead of crashing.)

## LLM providers

`decode` runs the agent loop on one selectable **LLM provider**, and every option has a free path:

- **Gemini** (default) — free credits on [Google AI Studio](https://aistudio.google.com/apikey).
- **OpenRouter** — an OpenAI-compatible gateway with `:free` model ids.
- **Modal** — open-source models you serve yourself on Modal's $30 of free credits.

The default is `gemini`, so an existing `.env` that only sets `GEMINI_API_KEY` keeps working untouched. To switch, set `LLM_PROVIDER=<name>` plus that provider's secret(s) in `.env` — e.g. OpenRouter's free tier:

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key-here
# OPENROUTER_MODEL=qwen/qwen3-coder:free   # the default; a tool-calling-capable free model
```

### Choosing the model

Each provider has its **own** model variable — set the one for your active `LLM_PROVIDER` (in `.env` or as a shell env var); the others are ignored. All three ship a sensible default, so this is optional:

| Provider (`LLM_PROVIDER`) | Model variable | Default | Pick another |
|---|---|---|---|
| `gemini` | `GEMINI_MODEL` | `gemini-2.5-flash` | any Gemini model id (e.g. `gemini-2.5-pro`) |
| `openrouter` | `OPENROUTER_MODEL` | `qwen/qwen3-coder:free` | any id from [openrouter.ai/models](https://openrouter.ai/models) — the `:free` ones cost nothing |
| `modal` | `MODAL_ENDPOINT_MODEL` | `openai/gpt-oss-120b` | must match the model your endpoint serves — see [`MODAL_MODELS.md`](MODAL_MODELS.md) |

For example, to run Gemini's larger model:

```bash
LLM_PROVIDER=gemini          # the default, so optional
GEMINI_MODEL=gemini-2.5-pro
```

**Pick a tool-capable model.** The agent loop needs a model that supports **tool-calling + streaming**. The shipped defaults are known-good — `OPENROUTER_MODEL=qwen/qwen3-coder:free` and `MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b` — so for **both** OpenRouter and Modal, if you swap models, pick a tool-capable one or the loop breaks (the model narrates instead of emitting valid tool calls).

**Run on Modal.** Set `LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` (plus the `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` request headers, unless the endpoint was created `--unauthenticated`). See [`MODAL_MODELS.md`](MODAL_MODELS.md) for picking a model and creating the endpoint (`modal endpoint create`, `modal workspace proxy-tokens create`, and the wiring).

Every variable and its guidance lives in [`.env.example`](.env.example); the wiring decision and trade-offs are recorded in [`docs/adr/0005-multi-llm-provider-support.md`](docs/adr/0005-multi-llm-provider-support.md).

## Use

```bash
decode             # after `make install-cli` (or `uv run decode`)
```

You get an interactive REPL. Type a message and the agent streams a reply; when it wants to use a tool it **asks for approval first** (every tool, every time, in M1). Your messages echo as `you "…"` and the agent's streamed answer is labelled `Decode …`, so the conversation reads clearly.

| Action | Key |
|---|---|
| Send a message | `Enter` |
| **Steer** a running turn (redirect it now) | `Enter` while it's working |
| **Follow-up** (queue work for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn | `Esc` |
| Approve / deny a tool | type `y` / `n` at the prompt |
| Quit | `Ctrl-D` or `/quit` |

**Tools the agent can call** (all gated): `read` · `glob` · `grep` · `write` · `edit` · `bash` · `todo_write` (a task checklist) · `web_fetch` (HTML→Markdown) · `ask_user`.

**Skills** are reusable playbooks you trigger with `/<name>` (or that the agent invokes itself). They live in `.decode/skills/<name>/SKILL.md` and ship with the repo. For example, `repo-architecture` clones a GitHub repo, explores it in read-only passes, and writes an `ARCHITECTURE.md` — problem · usage · components · interfaces · end-to-end dataflow, all backed by Mermaid diagrams:

```bash
/repo-architecture https://github.com/iusztinpaul/designing-real-world-ai-agents-workshop
```

**Resume a previous session:**

```bash
decode --resume            # the most recent session
decode --resume <session>  # a specific session id / filename
```

**Memory.** `decode` loads `AGENTS.md` (walking from the working dir upward) and the harness `.decode/MEMORY.md` into its context, and on exit appends a one-sentence summary of the session to `.decode/MEMORY.md` so the next session has a little context. Full transcripts are saved to `.decode/sessions/*.jsonl` and logs to `.decode/logs/decode.log` (all gitignored under `<cwd>/.decode/`).

## Develop

All verbs run at the repo root via the [`Makefile`](Makefile) (wrapping `uv`):

```bash
make test            # full test suite (unit + integration)
make unit-tests      # unit only
make lint-check      # ruff check        (lint-fix to auto-fix)
make format-check    # ruff format check (format-fix to apply)
make pre-commit      # format + lint + unit tests (the fast gate)
make ci              # what CI runs: lockfile check + format + lint + full suite
make help            # all targets
```

Tests mirror `src/` 1:1 under `tests/`; model calls use Pydantic AI's `TestModel`/`FunctionModel`, so the suite needs **no network and no API key**. Conventions and the development workflow live in [`AGENTS.md`](AGENTS.md).

## License

[Apache-2.0](LICENSE).
