# decode

**decode** is a terminal **coding agent** ("agentic harness") built from scratch, step by step, as an educational open-source course. It is a single Python package, `decode`, exposing a TUI you run in your terminal: a Pydantic-AI ReAct loop driving file/bash/web/MCP tools, with pluggable inference (Gemini / OpenRouter / Modal), local + remote sandboxing, Opik observability, and a Kitaru durability runtime. Standalone single-package Python (`cli-tool-python` shape); the TUI is a module *inside* the package (`prompt_toolkit` input + `Rich` output), not a separate service.

License: **Apache-2.0**. Depth references below name **squid scaffold specs** (shipped in the `iusztinpaul/squid` plugin, not this repo) — read them via the plugin cache.

# Key Components

Single package — one bullet for the package, then the internal module map under [Project Structure](#project-structure). Modules are built incrementally as the course progresses; only `config/`, `entities/`, and `logging.py` are foundational from day one.

- **`decode`** — [`src/decode/`](src/decode/): the whole coding agent. Python 3.12+, `cli-tool-python` shape (Click entrypoint launching the TUI). Conventions: async I/O for network/DB, sync for CPU; infrastructure imported directly (no premature interfaces); shared models in `entities/`, narrow types in `<module>/types.py`; every entrypoint calls `init_logger()` at module level before any project import. Depth: squid spec `python-backend` + `cli-tool-python`.

# Project Structure

The intended target tree. Most `src/` subpackages are created **when you reach their step** — do not pre-create empty packages. `tests/` mirrors `src/` 1:1.

```
.
├── AGENTS.md / CLAUDE.md          # this memory file (+ Claude Code import)
├── pyproject.toml                 # uv + hatchling; deps grow per step
├── Makefile                       # install / test / lint / format / pre-commit / build / ci
├── .pre-commit-config.yaml        # format + lint (commit) · unit tests (push)
├── .env.example                   # config & secrets surface
├── docs/
│   ├── adr/                       # Architecture Decision Records (Nygard)
│   └── glossary.md                # ubiquitous language
├── tasks/                         # file-based tracker — one md per task
├── tests/{unit,integration}/      # unit mirrors src/ 1:1; integration touches real infra
└── src/decode/
    ├── __init__.py
    ├── logging.py                 # init_logger() — module-level in every entrypoint
    ├── cli.py                     # Click entrypoint → launches the TUI        [bootstrap]
    ├── config/settings.py         # pydantic-settings; module-level `settings` singleton
    ├── entities/                  # shared models: Message, Conversation, ToolCall, Task…
    ├── tui/                       # input: prompt_toolkit · output: Rich (answers via SSE)
    ├── harness/                   # message Queue + Priority Gate around the loop
    ├── agent/                     # Pydantic-AI ReAct loop (LLM ⇄ Tools)
    ├── agents/                    # agents catalog (Build/Plan/Explore/Code-Reviewer) + subagents
    ├── tools/                     # file I/O, Bash, web, tasks, MCP factory, skill dispatcher, LSP, AskUser
    ├── permissions/               # allow/ask/deny · modes (default/plan/edit/bypass) · settings.json
    ├── sandbox/                   # Bash execution — local (Docker/Firecracker) + remote (Modal)
    ├── services/                  # services interface: LLM gateway, memory, LSP servers, MCP servers
    ├── runtime/                   # Kitaru: credentials proxy, durability, scheduling, HITL
    ├── context/                   # context engineering: compaction + conversation log (JSONL)
    ├── memory/                    # AGENTS.md / MEMORY.md loading
    └── observability/             # Opik tracing
```

**Scripts & entrypoints.** Operator scripts in `scripts/`; CLI entrypoint declared in `pyproject.toml` `[project.scripts]` (`decode = "decode.cli:cli"`). **Every entrypoint module calls `init_logger()` at module level before any project import.**

# Tech Stack

Single Python toolchain — `uv`, `ruff`, `pytest`. **Python 3.12+.**

| Layer | Choice | Notes |
|---|---|---|
| Package/deps | `uv` (+ `hatchling` build) | `uv.lock` committed; `uv sync` is the installer. |
| Lint/format | `ruff` | One config block in `pyproject.toml`; format + check are separate passes. |
| Test | `pytest` (+ `asyncio`, `mock`) | `tests/` mirrors `src/`; `filterwarnings=["error"]`. |
| CLI / TUI | `click` · `prompt_toolkit` · `rich` | Click wrapper is thin; logic in pure functions. |
| Agent loop | `pydantic-ai` | ReAct loop (LLM ⇄ tools). *added at its step* |
| MCP | `fastmcp` | MCP tool factory + servers. *added at its step* |
| Inference | `google-genai` (Gemini) · OpenRouter · Modal | Behind one **LLM Gateway**; OpenRouter is OpenAI-compatible. *added per step* |
| Observability | `opik` | Tracing + eval harness. *added at its step* |
| Sandbox / serving | `modal` (remote) · Docker/Firecracker (local) | *added at its step* |
| Durability | Kitaru | Credentials proxy, durability, scheduling, HITL. *confirm package source* |
| Datastore | SQLite | Conversation log is JSONL today; compaction landed on it (ADR-0006). SQLite remains a deferred durable-store option. |

Per-step libraries are `uv add`-ed when you reach them (see the commented block in `pyproject.toml`) — the initial install stays light.

## Access Documentation

Use the `context7` MCP server (when connected) to look up authoritative usage for any tech-stack item or external service above; fall back to web search otherwise.

**Reference docs (`llms.txt` — fetch on demand).** Each link below is an *index* of doc pages. Fetch the index first, then fetch only the specific page(s) you need. Do **not** pull whole `llms-full.txt` files into context unless a task truly requires the full reference.

- **Pydantic AI:** https://pydantic.dev/docs/ai/llms.txt — append `.md` to any doc page for raw markdown (e.g. `.../agents/index.md`).
- **Modal:** https://modal.com/llms.txt — full reference at https://modal.com/llms-full.txt (large; only if needed).
- **OpenRouter:** https://openrouter.ai/docs/llms.txt
- **Opik:** https://www.comet.com/docs/opik/llms.txt — also append `/llms.txt` to any section URL for a scoped index.
- **Kitaru (ZenML):** https://docs.zenml.io/llms.txt — full reference at https://docs.zenml.io/llms-full.txt.

## Running commands

All core verbs run at the repo root via the [`Makefile`](Makefile), wrapping `uv`:

| Verb | What it does |
|---|---|
| `make install` | `uv sync` + install git hooks. |
| `make test` | Full suite (`uv run pytest`). `make unit-tests` / `make integration-tests` for subsets. |
| `make lint-check` / `make lint-fix` · `make format-check` / `make format-fix` | `ruff check` / `ruff format` (assert vs write). |
| `make pre-commit` | `format-check + lint-check + unit-tests` (the fast gate). |
| `make build` | `uv build` → wheel + sdist. |
| `make ci` | What CI runs: `uv lock --check` + format-check + lint-check + test. |
| `make help` | Curated target list. |

Commands not wrapped by `make` — use the runner directly: `uv run <cmd>`, `uvx <one-shot-tool>`.

**Manual QA order:** `format-fix → lint-fix → format-check → lint-check → pre-commit → unit-tests`.

**Dependencies & env vars.** Add runtime deps with `uv add <pkg>`; dev tools with `uv add --group dev <pkg>` (PEP 735 — never `[project.optional-dependencies]`). New env vars → `.env.example` + `config/settings.py`; never read `os.environ` deep in call sites.

## Infrastructure & external services

Access infra **CLI-only** (no web UIs) so runs are reproducible and the orchestrator can spot-check by re-running commands.

- **Git / GitHub:** `git`; `gh` for PRs, issues, Actions logs.

For each external-service slug below (wrapped in `<!-- stack:* -->` for find-and-delete), the one-liner + its CLI. Grep `<!-- stack:` to locate or remove one.

- **Gemini** — primary LLM API via the `google-genai` SDK; one of three inference backends behind the LLM Gateway (with **OpenRouter**, OpenAI-compatible, and **Modal**-served open models). squid spec: `llm-gemini`.
- **Modal** — remote sandbox + open-model serving; `modal run` / `modal deploy` / `modal token set`. squid spec: `model-serving-modal`.
- **Opik** — LLM tracing + eval harness; `opik` CLI / `OPIK_API_KEY`. squid spec: `observability-opik`.
- **Kitaru** — ...

- **Project MCP servers:** *AGENT: fill in any MCP server this project's code talks to and the config it needs.*

# Key Principles You Will Respect All Over Your Work

- Always prioritize removing instructions over adding more.
- Whenever you add a new rule to memory (e.g. `AGENTS.md`), support it with a concise explanation plus good and bad examples. Good: "a 200-token chunk size", "sub-100ms latency". Bad: "a powerful architecture", "a robust pipeline".
- **Build it step by step.** This is a teaching codebase — favour the simplest thing that works and is readable over the clever or the speculative. One concept per step; no abstraction without a second concrete caller.
- **Infrastructure is imported, not abstracted.** Call `modal` / `opik` / `pydantic-ai` / `sqlite3` directly. Introduce an interface only when a real second implementation arrives (e.g. the local-vs-remote sandbox split, which is a genuine seam).
- **Datetimes are timezone-aware (UTC).** Reject naive `datetime` at every boundary. Type-annotate everything, including `-> None`. Library code never `print()`s — use the logger; user-facing CLI output goes through `click.echo` / `rich`.

# Developing New Features & Bug Fixes

This project uses the **squid** agent team (`iusztinpaul/squid` plugin). Direct chat for trivial edits; for one or a few groomed tasks use **`/implement-task`**; for a whole feature use **`/plan`** then **`/implement-night`** (or run **`/review`** / **`/review-ci`** standalone). Per-role rules ship with the plugin.

| Role | Responsibility |
|---|---|
| Product Architect (PA) | Grooms a feature into a Tasks Plan; authors ADRs + glossary; user-POV acceptance. |
| Software Engineer | Implements code + tests; commits each task after the Tester passes. |
| Tester | Full suite + e2e adversarial QA. |
| PR Reviewer | Diff review — correctness, simplicity, tests, standards, docs. |
| On-Call | Watches CI; diagnoses failures and hands fix tasks to the SWE. |

```
/plan  →  approved Tasks Plan (+ optional ADR) + branch
/implement-night:  /implement-task → /review → /review-ci  →  human squash-merges
```

Engineering discipline — TDD-first, branch off the active branch, run end-to-end before hand-off, regression-test-first for bugs, the format/lint/unit cadence — is enforced by the pipelines.

**Tracker:** `TRACKER_MODE: file`. One `tasks/<NNN>-<slug>.md` per task with a `status:` frontmatter field (`pending` → `in-progress` → `done`) and an append-only `## Log`. See [`tasks/README.md`](tasks/README.md).

Project-specific invariants the agents can't infer:

- **Sandbox is the one real abstraction.** Bash execution dispatches local (Docker/Firecracker) vs remote (Modal) behind a single `run` seam — keep both implementations behind it; don't leak Modal or Docker types upward.
- **Secrets never reach the model or the sandbox payload.** Credentials flow through the Kitaru credentials proxy; tool/sandbox calls carry handles, not raw keys.

# Testing E2E

The automated proof that the whole M1 stack hangs together is the capstone integration test
[`tests/integration/test_milestone1_capstone.py`](tests/integration/test_milestone1_capstone.py):
it drives a six-step conversation (read → gated write approve → gated write deny → todo_write →
ask_user → web_fetch) through the **real** `build_agent()` + `Runner` + `render_event` + session
log + memory write-back, swapping only the network boundary (`FunctionModel` for the model,
`httpx.MockTransport` for the web tool). Run it with `make integration-tests` (or the full gate,
`make ci`). It needs no API key and makes no network call.

What follows is the **manual** e2e pass against a real Gemini — exercise each surface like a user,
then try to break it (the adversarial half is the Tester's job).

**Launch.** One env var, no service to start first:

```bash
export GEMINI_API_KEY=…        # the only required secret (see .env.example); or put it in .env
uv run decode                  # the REPL: a "> " prompt + a footer hint render
```

A bare `uv run decode` with **no** `GEMINI_API_KEY` must print one friendly line on stderr —
`decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` — and exit
non-zero, **not** a traceback (the task-004 startup guard in `cli.py`).

For each surface below: the thing to type, and what "working" looks like.

| Surface | Type this | Working looks like |
|---|---|---|
| **Plain chat** | `what can you do?` | the answer **streams** in token-by-token above the prompt; the prompt stays pinned at the bottom. |
| **Read (gated)** | `read pyproject.toml` | a `permission? read …` prompt appears; type `y` → a panel with the numbered file contents; type `n` → the model is told it was denied and adapts. |
| **Write (gated, approve)** | `create a file hello.txt that says hi` | `permission? write …` → `y` → the file **appears** on disk (`cat hello.txt`) and the model confirms. |
| **Write (gated, deny)** | repeat the write, answer `n` | the file is **not** created (`ls hello.txt` → absent) and the model is told the write was denied (it does not pretend it wrote it). |
| **Bash** | `run the tests with make unit-tests` | `permission? bash …` → `y` → a panel with the command's stdout/stderr (truncated past the cap); a runaway command is bounded by `bash_timeout_s`. |
| **Todo checklist** | `make a 3-step plan to add a CLI flag and track it` | a blue **tasks** panel renders the checklist (`[ ]` / `[~]` / `[x]`) and re-renders as the model updates statuses. |
| **web_fetch** | `fetch https://example.com and summarize it` | `permission? web_fetch …` → `y` → the page comes back as Markdown (HTML stripped) and the model summarizes it. |
| **ask_user** | `deploy my app` (something underspecified) | the model calls `ask_user`; an `ask: …` question renders with a `type your answer:` cue; your next typed line **is** the answer and the turn resumes with it. |

**Mid-turn interaction** (while a turn is streaming — ADR-0002 §4-5):

- **Steer** — start a long turn, then type a line and press plain **Enter**. It is injected at the
  next model-request boundary (never mid-stream/mid-tool); the model sees it on the next leg.
- **Follow-up** — press **Alt+Enter** instead. It is queued and drained only when the turn would
  otherwise stop, continuing the conversation as a new turn.
- **Abort** — press **Esc**. The turn stops at the next boundary, keeps the work done so far, and
  the REPL returns to idle (a `[aborted]` marker renders).

**Persistence + memory across sessions:**

- `decode --resume` (or `decode --resume <session-id>`) replays the latest (or named) session log
  from `.decode/sessions/*.jsonl` — the prior conversation is seeded and you continue it.
- On quit (`/quit` or `Ctrl-D`), one cheap Gemini call appends a dated one-line summary to
  `./MEMORY.md`. Quit, `cat MEMORY.md` (a new `- YYYY-MM-DD: …` bullet), then relaunch — that line
  is injected back into the agent's instructions (it can recall what the last session did).

# Documentation Conventions

- **ADRs** at [`docs/adr/`](docs/adr/) — `NNNN-kebab-title.md`, Nygard template (Status / Context / Decision / Consequences). Every non-obvious architectural choice (which inference backend, the sandbox abstraction, the compaction strategy, choosing Kitaru) ships with one. squid spec: `adr`.
- **Glossary** at [`docs/glossary.md`](docs/glossary.md) — one canonical name per domain concept (Harness, Agent Loop, Priority Gate, Sandbox, Compaction, Subagent…), used identically in code / docs / specs / conversation; update it in the same PR that introduces or renames a concept. squid spec: `ubiquitous-language`.
