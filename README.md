# decode

**decode** is a terminal **coding agent** you run in your terminal — a [Pydantic AI](https://ai.pydantic.dev) ReAct loop on **Gemini**, driving file / bash / web tools behind a `prompt_toolkit` + `Rich` TUI, with an ask-before-every-tool permission gate, project memory, and replayable sessions.

This repository is an **educational, open-source course** that builds `decode` from scratch, step by step. It is a single Python package (`decode`); each module under `src/decode/` maps to one part of the architecture.

> **Status — Milestone 1 (vanilla on-device agent).** What's built today: the agent loop on Gemini, the steering TUI, the core tools, the permission gate, memory, and a session log. Later milestones add more inference providers (OpenRouter / Modal), sandboxing, observability (Opik), a durability runtime (Kitaru), and MCP — see [`AGENTS.md`](AGENTS.md) and the architecture decision in [`docs/adr/0002-milestone-1-vanilla-agent-architecture.md`](docs/adr/0002-milestone-1-vanilla-agent-architecture.md).

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — the package/runtime manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`). uv installs the pinned Python 3.12 for you.
- A **Gemini API key** ([Google AI Studio](https://aistudio.google.com/apikey) — there's a free tier).

## Install

```bash
git clone git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + wire git hooks   (or just: uv sync)
```

### Run `decode` from anywhere (optional)

`make install` lets you run the agent with `uv run decode`. To type just **`decode`** from any directory, put it on your PATH:

```bash
make install-cli    # uv tool install --editable .  — the command tracks your source
```

If `decode` isn't found afterward, run `uv tool update-shell` and restart your shell. Uninstall with `make uninstall-cli`.

## Configure

`decode` reads its config from environment variables, including a local `.env` file (loaded via pydantic-settings):

```bash
cp .env.example .env
# then edit .env and set:
#   GEMINI_API_KEY=your-key-here
#   GEMINI_MODEL=gemini-2.5-flash   # optional; this is the default
```

`.env` is gitignored. Precedence is **shell env var → `.env` → built-in default**. (If no key is set, `decode` prints a one-line hint and exits instead of crashing.)

## Use

```bash
uv run decode      # or just `decode` after `make install-cli`
```

You get an interactive REPL. Type a message and the agent streams a reply; when it wants to use a tool it **asks for approval first** (every tool, every time, in M1).

| Action | Key |
|---|---|
| Send a message | `Enter` |
| **Steer** a running turn (redirect it now) | `Enter` while it's working |
| **Follow-up** (queue work for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn | `Esc` |
| Approve / deny a tool | type `y` / `n` at the prompt |
| Quit | `Ctrl-D` or `/quit` |

**Tools the agent can call** (all gated): `read` · `glob` · `grep` · `write` · `edit` · `bash` · `todo_write` (a task checklist) · `web_fetch` (HTML→Markdown) · `ask_user`.

**Resume a previous session:**

```bash
uv run decode --resume            # the most recent session
uv run decode --resume <session>  # a specific session id / filename
```

**Memory.** `decode` loads `AGENTS.md` and `MEMORY.md` (walking from the working dir upward) into its context, and on exit appends a one-sentence summary of the session to `MEMORY.md` so the next session has a little context. Full transcripts are saved to `.decode/sessions/*.jsonl` (gitignored).

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
