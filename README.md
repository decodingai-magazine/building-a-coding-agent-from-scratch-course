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
# OPENROUTER_MODEL=openrouter/free   # the default: the Free Models Router (see below)
```

**The default `openrouter/free` is the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router):** it auto-routes each request to whatever free model is currently available and filters for the tool-calling the agent loop needs, so one congested provider can't hard-block you with a `429`. Pinning a single `:free` id instead (e.g. `qwen/qwen3-coder:free`) makes you eat that provider's rate limit alone.

**About OpenRouter's free rate limits.** `:free` models share a pool with daily caps. Per OpenRouter's current policy, adding **$10 of credits** raises the free-tier daily cap (roughly **50 → 1000 requests/day**) and unlocks BYOK — and free models still cost **$0** to run, so the $10 just sits in your balance. For an agent loop (many requests per turn) this is the difference between hitting the wall in minutes and a usable day; confirm the live figures at [openrouter.ai/settings/credits](https://openrouter.ai/settings/credits). If you'd rather not add credits, **Gemini's** per-key free tier is the more reliable free path (it's the default provider).

### Choosing the model

Each provider has its **own** model variable — set the one for your active `LLM_PROVIDER` (in `.env` or as a shell env var); the others are ignored. All three ship a sensible default, so this is optional:

| Provider (`LLM_PROVIDER`) | Model variable | Default | Pick another |
|---|---|---|---|
| `gemini` | `GEMINI_MODEL` | `gemini-2.5-flash` | any Gemini model id (e.g. `gemini-2.5-pro`) |
| `openrouter` | `OPENROUTER_MODEL` | `openrouter/free` (Free Models Router) | any id from [openrouter.ai/models](https://openrouter.ai/models) — the `:free` ones cost nothing |
| `modal` | `MODAL_ENDPOINT_MODEL` | `openai/gpt-oss-120b` | must match the model your endpoint serves — see [`MODAL_MODELS.md`](MODAL_MODELS.md) |

For example, to run Gemini's larger model:

```bash
LLM_PROVIDER=gemini          # the default, so optional
GEMINI_MODEL=gemini-2.5-pro
```

**Pick a tool-capable model.** The agent loop needs a model that supports **tool-calling + streaming**. The shipped defaults are known-good — `OPENROUTER_MODEL=openrouter/free` (which itself filters for tool-calling) and `MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b` — so for **both** OpenRouter and Modal, if you swap to a pinned model, pick a tool-capable one or the loop breaks (the model narrates instead of emitting valid tool calls).

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

**Tools the agent can call** (all gated): `read` · `glob` · `grep` · `lsp` (Python code intelligence) · `write` · `edit` · `bash` · `todo_write` (a task checklist) · `web_fetch` (HTML→Markdown) · `ask_user`.

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

## Headless runtime (`decode run`)

`decode run "<task>"` is the **unattended** counterpart to the REPL: it runs a single task to completion with no human at the keyboard and prints the agent's final answer. It builds the **same** agent as the TUI but drives it through a [Kitaru](https://docs.zenml.io/) **durable flow** — each turn is checkpointed, so an expensive multi-tool run survives a crash and resumes from where it stopped instead of re-paying for finished work. The design is recorded in [`docs/adr/0008-kitaru-durable-runtime.md`](docs/adr/0008-kitaru-durable-runtime.md).

```bash
decode run "list the python files under src and summarize what the cli module does"
```

The agent tool-loops headlessly and prints the result; the process exits `0`. Each run is recorded as a durable, inspectable Kitaru **checkpointed execution**; a *crashed* run can be resumed with its finished checkpoints replayed from cache (full crash-resume lands in a later step). A fresh re-run of the same task is a **new** execution, not a cache hit.

- **No human in the loop (this slice).** The run is autonomous, so it executes under **bypass** — every tool runs with no approval prompt (an `ask_user` becomes a no-op the agent works around). Durable human-in-the-loop approvals are a later step.
- **Setup.** It runs on Kitaru's **local stack, fully offline** — no Kitaru server and no `kitaru init` are required (a `default` stack is used). The interactive REPL is unaffected and never loads Kitaru.
- **Guards.** `decode run` needs the same provider config as the REPL (e.g. `GEMINI_API_KEY`); a missing key prints one friendly line and exits non-zero. Set `RUNTIME_ENABLED=false` to disable the subcommand entirely (it then exits with a friendly line and builds no flow).

## Context compaction

A long conversation grows toward the model's context window. `decode` keeps it in budget with a **cheapest-first cascade** that runs automatically at the end of each turn — measured against how full the window is — plus a manual override. The wiring and trade-offs are recorded in [`docs/adr/0006-conversation-compaction.md`](docs/adr/0006-conversation-compaction.md).

| Tier | Fires when | Working looks like |
|---|---|---|
| **Microcompaction** (no LLM, in-memory) | input usage reaches **~60%** of the window | a `Decode - microcompacted context (elided N old tool output(s), …)` line; old tool-output bodies are blanked for the next turn. **Not persisted** — `--resume` still replays the full transcript. |
| **Full compaction** (one LLM call) | input usage reaches **~80%** of the window, **or** you type **`/compact`** | a `Decode - compacted context (~N tokens → summary + M recent messages).` line; older turns collapse into a summary and recent turns stay verbatim. **Persisted** — `decode --resume` continues the *compacted* conversation. |

The footer carries a **fill gauge** — `○ ◔ ◑ ◕ ●` plus a percentage of the window used — that tracks the same window: **green** below ~60%, **yellow** ~60–80%, **red** at/above ~80%, so you watch the context approach compaction.

On exit, a second level compresses `.decode/MEMORY.md`: once it reaches its **200-line** cap, one cheap LLM call dedupes and merges the highest-signal facts instead of dropping the oldest lines (drop-oldest stays the guaranteed fallback, so the cap is always enforced).

Tune it in `.env` — every setting is optional with a safe default:

- `COMPACTION_CONTEXT_WINDOW_TOKENS` — your model's **input** window in tokens (default `1048576`, Gemini 2.5 Flash). The single source of truth that also drives the gauge.
- `COMPACTION_RESERVE_FRACTION` (default `0.20`) — full compaction fires at `window * (1 - reserve)`, i.e. **80%** full.
- `MICROCOMPACTION_RESERVE_FRACTION` (default `0.40`) — microcompaction fires earlier, at **60%** full (keep it larger than the full reserve so it fires first).
- `COMPACTION_ENABLED=false` disables the **automatic** cascade; manual `/compact` still works.

## LSP / code intelligence

Beyond the text tools (`read`, `grep`), `decode` can see your Python as a **semantic graph** by talking to a Language Server over LSP. It ships **`ty`** (Astral's type-checker, same vendor as `ruff`/`uv`) by default. The design and trade-offs are recorded in [`docs/adr/0007-lsp-integration.md`](docs/adr/0007-lsp-integration.md).

Two channels deliver it:

- **The `lsp` tool** (active) — the agent calls it on demand with one of four ops: `definition` (jump to where a symbol is defined), `references` (find every use), `hover` (type / signature / docs), and `diagnostics` (a file's problems, all severities). It's read-only, so the gate auto-allows it like `read` — no prompt.
- **Post-edit diagnostics** (passive) — after a successful `write` / `edit` of a `.py` file, that file's **errors** are appended to the tool's result as an `LSP diagnostics (ty) — fix these:` block, so the agent sees and fixes its mistakes inline. Errors only — clean files (and warnings-only files) stay silent.

It is **best-effort**: if `ty` isn't installed (it's a dev-group dependency) or the server is missing / slow, both channels degrade silently — the agent just falls back to the text tools and no turn ever breaks.

Tune it in `.env` — every setting is optional with a safe default:

- `LSP_ENABLED=false` disables the whole feature (no Language Server is ever spawned).
- `LSP_SERVER_COMMAND` / `LSP_SERVER_ARGS` — **swap the server**: the default is `ty` + `["server"]`; drop in `pylsp` (or any stdio LSP server) by overriding these (the spawn is `[LSP_SERVER_COMMAND, *LSP_SERVER_ARGS]`).
- `LSP_DIAGNOSTICS_ON_EDIT=false` turns off only the post-edit diagnostics; the `lsp` tool still works.
- `LSP_REQUEST_TIMEOUT_S` (default `10.0`) — per-request wall-clock timeout.

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
