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
    ├── agents/                    # agents catalog: Build/Plan/Code-Reviewer (primary) + Explore (subagent, spawned via the agent tool)
    ├── tools/                     # file I/O, Bash, web, tasks, MCP factory, skill dispatcher, LSP, AskUser
    ├── permissions/               # allow/ask/deny · modes (default/plan/edit/bypass) · settings.json
    ├── sandbox/                   # Bash execution seam — none (host) / docker (local) / modal (remote)
    ├── services/lsp/              # LSP Service — hand-rolled stdio client; FIRST concrete services/ entry (ADR-0007)
    ├── services/                  # services interface: LLM gateway, memory, MCP servers land here later
    ├── runtime/                   # Kitaru durable flow + `decode run` (ADR-0008); HITL/creds-proxy later
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
| Code intelligence | `ty` (stdio LSP server) | Python `lsp` tool + post-edit diagnostics over a hand-rolled stdio client; swappable (`pylsp`), dev-group, pre-1.0, best-effort (ADR-0007). *added at its step* |
| Inference | `google-genai` (Gemini) · OpenRouter · Modal | Behind one **LLM Gateway**; OpenRouter is OpenAI-compatible. *added per step* |
| Observability | `opik` | Tracing + eval harness. *added at its step* |
| Sandbox / serving | Docker (local) · `modal` (remote) — behind one `run` seam by `SANDBOX_MODE` | Three executors: `none` (host, default) / `docker` / `modal`; docker via the CLI (no SDK). gVisor/Kata are free daemon-config upgrades; Firecracker a non-goal (ADR-0011). |
| Durability | `kitaru[local,pydantic-ai,llm]` | Durable headless flow (`decode run`) wrapping `build_agent()` via the `KitaruAgent` PydanticAI adapter — checkpoints + replay, local stack, offline (ADR-0008). Needs pydantic-ai 1.x: depend on `pydantic-ai-slim[google,openai]`, not the meta package (ADR-0009). HITL / creds-proxy / scheduling are later steps. |
| Datastore | SQLite | Conversation log is JSONL today; compaction landed on it (ADR-0006). SQLite remains a deferred durable-store option. |

Per-step libraries are `uv add`-ed when you reach them (see the commented block in `pyproject.toml`) — the initial install stays light.

## Access Documentation

Use the `context7` MCP server (when connected) to look up authoritative usage for any tech-stack item or external service above; fall back to web search otherwise.

**Reference docs (`llms.txt` — fetch on demand).** Each link below is an *index* of doc pages. Fetch the index first, then fetch only the specific page(s) you need. Do **not** pull whole `llms-full.txt` files into context unless a task truly requires the full reference.

- **Pydantic AI:** https://pydantic.dev/docs/ai/llms.txt — append `.md` to any doc page for raw markdown (e.g. `.../agents/index.md`).
- **Modal:** https://modal.com/llms.txt — full reference at https://modal.com/llms-full.txt (large; only if needed).
- **OpenRouter:** https://openrouter.ai/docs/llms.txt
- **Opik:** https://www.comet.com/docs/opik/llms.txt — also append `/llms.txt` to any section URL for a scoped index.
- **Kitaru (by ZenML):** https://docs.zenml.io/llms.txt — full reference at https://docs.zenml.io/llms-full.txt.

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

- **Gemini** — primary LLM API via the `google-genai` SDK; `GEMINI_API_KEY` (no dedicated CLI).
- **OpenRouter** — OpenAI-compatible inference backend; `openrouter` CLI.
- **Modal** — remote sandbox + open-model serving/inference backend; `modal run` / `modal deploy` / `modal token set`.
- **Opik** — LLM tracing + eval harness; `opik` CLI.
- **Kitaru** — runtime (durability, HITL); `kitaru` CLI. More within the `kitaru` skills and docs.

- **Project MCP servers:** *AGENT: fill in any MCP server this project's code talks to and the config it needs.*

# Key Principles You Will Respect All Over Your Work

- Always prioritize removing instructions over adding more.
- Always use the minimum number of words needed to explain what you do, write docs or code that achieve the desired goal.
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

- **A sandbox mode is one isolated Workspace, behind one seam.** `bash` **and** the file/search tools dispatch by **Sandbox Mode** (`none` / `docker` / `modal`) through ONE `SandboxExecutor` (`sandbox/executor.py`, a `tools/exec.py::CommandExecutor`) over a thin `SandboxBackend` seam with two adapters — `DockerBackend` (local) + `ModalBackend` (remote), **fresh-exec both** (each `bash`/exec is a brand-new process, so `cd` / `export` do NOT persist across calls; the filesystem does). `/workspace` ≡ the host `.decode/sandbox` (`settings.sandbox_workspace_dir`) ≡ a `git clone` of `--repo`/`SANDBOX_REPO` (or an empty scratch when none is given). **File tools operate on the sandbox filesystem *through* the backend seam**, not a host mirror: docker = plain pathlib on the bind mount, modal = the `SandboxFilesystem` API + remote `find`/`grep` — the rejected alternative was a `.decode/sandbox` mirror kept converged by an mtime-delta sync, which can't propagate a remote `rm` so `read`/`glob` would eventually lie. `none` stays **byte-identical** (host `LocalExecutor`; direct pathlib; `deps.cwd == launch cwd`). Don't leak Docker/Modal types upward — callers see only `ExecResult` + `FileStat`. Firecracker is a non-goal; gVisor/Kata are zero-code daemon-config upgrades the docker CLI inherits (ADR-0012 supersedes ADR-0011 §2,3; ADR-0011 §1,5-7 retained + its isolation table).
- **Harness artifacts anchor to the launch cwd; only the tool scope moves into the Workspace.** In a sandbox mode `deps.cwd` (the agent's file/search + `bash` scope) becomes the Workspace, but every harness artifact — `.decode/sessions`, `.decode/MEMORY.md`, logs, `.decode/skills`, the `.decode/settings.json` permission file — anchors to the **launch cwd**, carried on `AgentDeps.harness_home` (**Harness Home**). The skills catalog/dispatcher + memory injection read `harness_home`; the file/search tools + `bash` read `deps.cwd`. In `none` mode `harness_home == deps.cwd == launch cwd` (byte-identical). Good: the session log's header records Harness Home, so `--resume` finds it next launch. Bad: writing the session log under `deps.cwd` — it would ship into the user's repo branch and vanish when the sandbox is torn down (ADR-0012 §6).
- **The Workspace ships back as a git branch, host-side only.** A sandbox session's results survive via the **Hand-back** (`sandbox/handback.py`): the harness secures the final Workspace onto a deterministic `decode/<session-id>` **Session Branch** (auto-committing any uncommitted model work; the model's own commits are preserved, never rewritten) and `git push origin`s it — `--repo <URL>` lands it on the remote, `--repo <local path>` in the local source repo — with the user's **ambient host git creds**. **Every git command runs host-side against `.decode/sandbox` — no credential ever enters the sandbox** (the same secrets-never-in-the-sandbox invariant the Credential Proxy upholds). Layered durability: the local branch always exists even when the push fails (a friendly line names it + its `.decode/sandbox` location). Triggered on **REPL exit**, the idle-only **`/ship`** command, and **headless `decode run --repo` completion** — but **NOT** `decode run --hitl --repo` (intentionally unwired). Skipped for no-repo / non-git / unchanged-vs-cloned-HEAD Workspaces (ADR-0012 §8).
- **Secrets never reach the model or the sandbox payload.** Realized by the **Credential Proxy** (ADR-0011 §6, retained by ADR-0012 §9; headless + docker only): the **Worker** that runs model-chosen commands holds no token — the resolved **Proxy Rule** credential map lives only in the mitmproxy proxy container, which injects the header *after* the request leaves the worker. Distinct from the Kitaru **Secret-Store Config**, which hydrates the harness's own `Settings` (never a worker env) — ADR-0008 §5.

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
| **lsp (code intelligence)** | `where is build_agent defined?` | the model calls `lsp` (`definition`); it **auto-allows** (read-only — no prompt) and the answer cites the location (`src/decode/agent/factory.py:68:5`). Then ask it to `write a broken bad.py with a syntax error` → the approved write's result carries an appended `LSP diagnostics (ty) — fix these:` block and the model corrects it. |
| **agent (Explore subagents)** | `explore how permissions and sandboxing each work across the repo, in parallel` | the model issues one or more `agent(prompt)` calls; each **auto-allows** (no prompt — the `agent` tool is READ_ONLY, "can only cause reads") and renders as a tool call whose result panel is the child's **compressed report**. Each child is a read-only **Explore subagent** (`read`/`glob`/`grep`/`lsp` only — no `write`/`edit`/`bash`/`web_fetch`); several `agent(...)` calls in one response **fan out in parallel** (native `asyncio.create_task`, no custom gather), capped by `subagent_max_parallel` (default 4), each child bounded by `subagent_max_requests` (default 25) with its report truncated to `subagent_result_max_bytes` (default 16000). Children are **silent-until-done** (a no-op event sink — no sub-progress streams) and their transcripts are ephemeral, so `--resume` carries only the spawn call + folded report (ADR-0013). |
| **Opik tracing (observability)** | `export OPIK_API_KEY=<comet-key>` (free at comet.com), relaunch `uv run decode`, then run any turn (`what can you do?`) — and open the run in the Opik UI | on launch a `Decode - Opik tracing on (project 'decode').` line prints **once, before the banner** (never when `OPIK_API_KEY` is unset — then decode is **byte-identical**: zero spans, no network, no line). Each REPL **turn** is one Opik **Trace** (root **Span** `chat_turn`); the UI groups a session's traces into one **Thread** keyed on the session id, so a gated tool's approve/resume leg and any follow-up ride the **same** trace (turn latency honestly includes the gate wait). Every LLM + tool call is a **Span** with inputs/outputs, latency, tokens (`gen_ai.usage.*`) and — for models Opik prices (Gemini yes; OpenRouter/Modal open models may be tokens-only) — cost. Memory write-back + compaction ride along via the one global `instrument_pydantic_ai()`; a subagent `agent(...)` child **nests inside** the parent turn's trace with **child token usage visible** (closes ADR-0013 §9). `--resume` mints a fresh session id → a resumed conversation starts a **new** Thread. **Headless note:** a `decode run` is one Trace (`decode_run` / `decode_run_hitl`), Thread = the Kitaru exec_id; the activation surfaces only in the LOG, so **stdout stays exactly the answer** (pipe-clean) and stderr is untouched. A `--hitl` pause closes the run's span and the resume opens a fresh one under the same Thread; under a real provider on `checkpoint_strategy="calls"` some model spans may export as **siblings** of the run root (a documented ceiling — tokens ride every span regardless). Evals/experiments are M13 (ADR-0014). |
| **decode run (headless)** | `decode run "list the python files"` (a separate command, not in the REPL) | the agent tool-loops **headlessly** through a Kitaru durable flow — every tool runs inline under bypass with no prompt — and prints the result, exit `0`. The run is recorded as an inspectable checkpointed execution; a fresh re-run is a **new** execution (crash-resume replay of finished checkpoints is exercised in 059 / the capstone, not here). `RUNTIME_ENABLED=false` → one friendly stderr line, non-zero exit, no flow built (ADR-0008). |
| **decode run --hitl (durable HITL)** | `decode run --hitl "create config.toml, then deploy"` in one terminal; resolve from a **second** terminal | the gating headless run: read-only tools run inline, but a `write`/`edit`/`bash` (or `ask_user`/`exit_plan_mode`) **pauses the whole execution on a durable Kitaru wait**. While it polls, run `kitaru executions list` to find the waiting `<exec_id>` and the wait `<name>`, then `kitaru executions input <exec_id> --wait <name> --value 'true'` (approve), `'false'` (deny → the run stops, the tool never ran), or `'"staging"'` (an `ask_user` answer). The run resumes from that point and prints the result. An unanswered wait eventually times out and the run pauses, printing the `<exec_id>` + the `kitaru executions input` hint, exit `0`. **The timeout differs by wait kind (a known limitation — decode does not fork the adapter):** the `ask_user`/`exit_plan_mode` answer waits decode drives itself honor `runtime_wait_timeout_s`; the native `write`/`edit`/`bash` **approval** waits the adapter raises use its fixed `600s` default and ignore the setting (ADR-0008 §3). |
| **decode run --model (model override)** | `decode run --model gemini-2.5-pro "list the python files"` | same headless bypass run as above, but the **Model Override** overrides only the active provider's model id for this run (the provider stays `LLM_PROVIDER`-selected — no cross-provider swap; ADR-0010 §2). The answer prints on **stdout** (pipe-clean); on **stderr** the durable `exec_id: <id>` + a paste-ready `replay it with a change:  decode replay <id> --model gemini-2.5-pro` hint. Presence, not correctness — a model id wrong for the provider is not validated here; it fails at the first model request. Because the model rides through as a durable **flow input**, a later `decode replay` can swap it (ADR-0010 §4). |
| **decode replay --model (what-if replay)** | keep an `exec_id` from a `decode run` above, then `decode replay <exec_id> --from <checkpoint> --model gemini-2.5-pro` | re-executes that recorded **bypass** run from `--from` with the model swapped: turns before `--from` serve from the original run's **cache**, the anchor + downstream re-execute for real, so the swap only bites downstream (ADR-0010 §5). The (possibly changed) answer prints on **stdout**; the **new Fork** `exec_id:`, the `original:` id, and a `compare them:  kitaru executions get <new>  vs  kitaru executions get <original>` diff hint print on **stderr**. `--from` is **required** — Kitaru has no default anchor, so omitting it prints one friendly line (find checkpoints with `kitaru executions get <exec_id>`; `--model` omitted replays as-is). **Bypass-only:** a **HITL** exec_id is refused with one friendly line pointing at `kitaru executions replay <id>` (a HITL replay re-asks every wait on the local stack — ADR-0010 §5,7; answer-reuse is deferred, [`tasks/future/hitl-replay-answer-reuse.md`](tasks/future/hitl-replay-answer-reuse.md)). An ambiguous/invalid `--from` or a diverged swap each print one friendly line, non-zero exit, never a traceback. *Offline-provable scope:* the bypass model-swap re-executing downstream is proven hermetically by `tests/integration/test_runtime_capstone.py::test_model_swap_replay_re_executes_downstream_turns`; the deferred HITL answer-reuse needs a deployed stack. |

**Sandboxing** (ADR-0012 — a sandbox mode gives the agent a fully **isolated Workspace**; the default
`SANDBOX_MODE=none` keeps today's host `LocalExecutor` + direct-pathlib file tools, so every row above is
byte-unchanged unless you relaunch with a mode set). In `docker` / `modal` the agent's **whole tool scope**
— the file/search tools **and** `bash` — is that one Workspace: `/workspace` ≡ the host `.decode/sandbox`
≡ a `git clone` of `--repo` / `SANDBOX_REPO` (or an empty scratch when none is given), while decode's own
artifacts stay at **Harness Home** (the launch cwd). ONE `SandboxExecutor` drives both backends
**fresh-exec** (each `bash`/exec is a new process — `cd` / `export` don't persist across calls, the
filesystem does). `--repo` needs a sandbox mode: `--repo`/`SANDBOX_REPO` with `SANDBOX_MODE=none` prints
`Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace, which only exists in a
sandbox mode …`, non-zero, no traceback.

| Surface | Type this | Working looks like |
|---|---|---|
| **file tools + bash (docker Sandbox)** | launch `SANDBOX_MODE=docker decode --repo <url-or-local-path>` (needs a running Docker daemon; add `--local` for a fast local-path clone), then `glob **/*.py, read one of them, write a NOTES.md, then run 'echo hi > side.txt && cat side.txt'` | on launch a `Decode - cloning <repo> into the workspace…` line (only when a repo is given AND the Workspace is still empty), then `Decode - starting docker sandbox (ghcr.io/astral-sh/uv:python3.12-bookworm-slim)…` + a `sandbox:docker` banner segment — `docker ps` shows a `sleep infinity` container before any bash runs (a clone failure degrades to an empty Workspace + one friendly line). The **file/search tools and `bash` share the one `/workspace` tree** — docker is a **live bind mount**, always truthful: a file `bash` writes is returned by `read`, a `bash` `rm` is reflected by `glob`. Each `bash` still gates (`permission? bash …` → `y`); **fresh-exec** — `cd` / `export` do NOT carry between calls (chain them: `cd /workspace/app && …`), the filesystem persists (one container/session); a timeout kills only that `docker exec` — the container + filesystem survive and the reply says it timed out. `lsp` + post-edit diagnostics **run** (host `ty` reads the live bind mount); **`git` is installed into the container at startup** (the slim base ships none) **with its identity preconfigured** (`SANDBOX_GIT_USER_*`, default `decode`/`decode@localhost`) so a model `git commit` in `/workspace` works — but pushing/PRs need creds, which stay host-side (hand-back) or ride the Credential Proxy, never in the sandbox; `web_fetch` stays **gated** (it reaches the host network). On exit (or `/ship`) the Workspace is handed back (see the `/ship` row). **Manual-QA peek:** `docker exec -it <id> bash` (filter `ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) drops you at `/workspace` = the host `.decode/sandbox`. **Guard:** daemon stopped → `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example).`, non-zero, no traceback. |
| **file tools + bash (modal Sandbox)** | launch `SANDBOX_MODE=modal decode --repo <url>` (needs `modal token set` account creds), then `glob **/*.py, read one, then run 'ls -a /workspace'` | after the clone line the launch prints `Decode - starting modal sandbox …` **and** `Decode - uploading the workspace to the modal sandbox…` — the Workspace is **bootstrap-uploaded** (ONE tar, host → remote; NOT `add_local_dir`-seeded, NOT mtime-synced), so `/workspace` holds the **cloned repo** (plus the seeded `.decode/skills`), not an empty scratch. The file tools go **directly against the remote `SandboxFilesystem`** (`read`/`write`/`edit`) and `glob`/`grep` run as remote `find`/`grep` — always truthful, no host mirror. `bash` gates as usual; filesystem changes persist across calls but `cd` / `export` reset per call (fresh-exec, like `none`). A sandbox that outlives its `SANDBOX_TIMEOUT_S` lifetime is recreated on the next op and **re-bootstrapped from the last local `.decode/sandbox` state**; the reply notes in-sandbox changes since the last export may be lost. `lsp` + post-edit diagnostics are **best-effort-disabled** (host `ty` can't reach the remote fs — ADR-0012 §7); **`git` is baked into the image** (`apt_install("git")` + a `git config` layer for the `SANDBOX_GIT_USER_*` identity, both cached — a model `git commit` in `/workspace` works out of the box); with **`SANDBOX_GIT_TOKEN` set, the token is injected INTO the modal sandbox** (`GITHUB_TOKEN` via `modal.Secret` + a credential-helper layer) so the model can `git push` / open a PR from inside — **one** `SANDBOX_GIT_TOKEN` serves both backends (docker feeds the same token to its Credential Proxy, worker token-free; modal injects it directly), the docker-proxy vs modal-direct-inject trade-off (ADR-0012 §10; use a scoped PAT since modal keeps it in-sandbox); `web_fetch` stays gated. At session end (or `/ship`) `/workspace` is **exported** down to the host `.decode/sandbox` for the hand-back. **Guard:** no Modal creds → ``Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` (see .env.example).``, non-zero, presence only (no `modal` import, no network call). |
| **decode run in a Sandbox** | `SANDBOX_MODE=docker decode run --repo <url> "run 'uname -a', then read README.md and summarize it"` — then `decode replay <exec_id> --from <checkpoint>` it | the headless **bypass** run executes `bash` + the file tools inside the Workspace with no prompt and prints the answer, exit `0`. Because sandbox `bash` has real side effects, a `decode replay` **re-executes** the sandbox `bash` (NOT served stale from cache — the flow sets `{"cache": False}` on the `bash` checkpoint when `SANDBOX_MODE != none`, ADR-0011 §5), unlike a `none`-mode cached turn. On completion the Workspace is **auto-shipped** back as a `decode/<exec_id>` branch host-side (the outcome line goes to **stderr** so stdout stays pipe-clean) — **only for `decode run --repo`, NOT `decode run --hitl --repo`** (the HITL path is intentionally unwired). The same backend guard runs in the headless pre-flight (daemon down / modal creds absent, or `--repo` in `none` mode → one stderr line, non-zero, **no flow built**). |
| **/ship (git hand-back)** | in a `SANDBOX_MODE=docker decode --repo <url>` session, do some work, then type `/ship` (idle-only, like `/compact` / `/clear`) | decode secures the final Workspace onto a `decode/<session-id>` branch and pushes it host-side: `Decode - handed the workspace back on branch decode/<id> (pushed to origin).` (modal exports `/workspace` down first; docker's mount is already live). A push that can't reach origin is never lost — `Decode - could not push decode/<id> to origin; the results are safe on the local branch decode/<id> in .decode/sandbox — push it yourself when ready.` An **unchanged / non-git** Workspace skips: `Decode - the workspace is unchanged from the cloned HEAD, so there is nothing to hand back.` In **`none` mode or with no `--repo`** → `Decode - no sandbox workspace to ship.` The **same hand-back runs automatically on REPL exit** (silent no-op on a skip). Every git command runs **host-side** with your ambient creds — no credential ever enters the sandbox. |
| **Credential Proxy (headless + docker)** | add a **Proxy Rule** to `DEFAULT_PROXY_RULES` in `src/decode/sandbox/proxy.py` (the shipped example: `github-auth` → `Authorization: Bearer {{ github-token.value }}` on `api.github.com`), `kitaru secrets set github-token --private --value=<PAT>`, set `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`, then `SANDBOX_MODE=docker decode run "use python urllib to GET https://api.github.com/user and print the login"` | the **Worker** container's request succeeds **authenticated** though the worker holds **no** token — a mitmproxy **Credential Proxy** container injects the header *after* the request leaves the worker. Prove the worker is token-free: `docker exec <worker-id> env \| grep -i token` prints nothing (the resolved credential map lives only in the proxy container's env, `DEFAULT_PROXY_RULES` ships empty = opt-in). **GitHub shortcut (ADR-0012 §10):** for a plain push/PR you need no Proxy Rule or Kitaru secret — just set `SANDBOX_GIT_TOKEN` **non-empty**; the proxy **auto-engages** and `github_token_rules` builds the two GitHub header rules (Bearer `api.github.com`, Basic `github.com`) from that one token (the same token modal direct-injects), and **git is installed into the proxy-wired worker** so its `git push` over the Basic rule has a client (worker still token-free — the token lives only in the proxy). `DEFAULT_PROXY_RULES` + the flag stay the general path for **other** hosts. Headless + docker only; the REPL never builds it (never imports kitaru). Cooperative egress, not an exfiltration barrier. **Guard:** same docker daemon guard; a no-op unless `sandbox_mode=docker` **and** (`SANDBOX_CREDENTIAL_PROXY_ENABLED=true` **or** a **non-empty** `SANDBOX_GIT_TOKEN`). |

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
  `.decode/MEMORY.md`. Quit, `cat .decode/MEMORY.md` (a new `- YYYY-MM-DD: …` bullet), then relaunch —
  that line is injected back into the agent's instructions (it can recall what the last session did).

## Headless replay & what-if (Kitaru operator surface — documented, not wrapped)

`decode replay` wraps only the **bypass model-swap** common case, 1:1 over Kitaru's native flow-object
replay (ADR-0010 §5). The full **checkpoint → replay → diff → decide** loop lives on Kitaru's own CLI /
SDK — decode deliberately does **not** re-implement diff, cohort, or checkpoint-override machinery
(ADR-0010 §6, non-goals). Everything below is that Kitaru operator surface, verified against the
installed **kitaru 0.18** + docs.zenml.io ("Replay and Overrides", "Replay and improve"). Claims are
scoped to what actually ships — where something is a pattern or a roadmap item, it says so.

**Three runs, not two.** The trustworthy what-if is three runs, and the middle one is the point:

| Run | What it is | Role |
|---|---|---|
| **Observed** | the original recorded run | what actually happened |
| **Baseline Rerun** | `kitaru executions replay <id> --from <cp>` with **no** change | the *control* — proves replay reproduces faithfully |
| **Fork** | same `--from`, **one** input changed (e.g. `--args '{"model":…}'`) | your change |

You diff the **Fork against the Baseline Rerun**, not against the Observed run — the control isolates
your one variable. If the Baseline Rerun does not reproduce the Observed run (a nondeterministic tool,
external state, time-dependent output), the diff is untrustworthy; pin the nondeterminism first.

**CLI replay with overrides** (the surface `decode replay --model` wraps a slice of):

```bash
kitaru executions replay <exec_id> --from <cp>                                   # Baseline Rerun (control)
kitaru executions replay <exec_id> --from <cp> --args '{"model":"gemini-2.5-pro"}'   # Fork (flow-input swap)
kitaru executions replay <exec_id> --from <cp> --overrides '{"checkpoint.<name>":<value>}'  # checkpoint-output swap
```

- `--args` = **flow-input** overrides (the CLI mirror of `flow.replay(..., model=…)`; `decode replay
  --model` surfaces this common case). The **Model Override** rides here.
- `--overrides checkpoint.<name>` = a **Checkpoint Override**: substitute a recorded checkpoint's single
  output at its **direct consumers**, re-executing from those consumers forward. Keys **must** start with
  `checkpoint.` (any other prefix raises `KitaruUsageError`) and the overridden checkpoint must expose a
  single output.
- **`--overrides checkpoint.X` is the tool-output mock stand-in.** Per-tool-call `output=` / `raise_=`
  mocks (force one tool call to return a fake value or fail) are **Kitaru roadmap, not shipped** — the
  ZenML guide flags this explicitly. Today, override the tool's recorded checkpoint output instead.

**Diff = compare the two execution records.** There is **no `kitaru diff` CLI and no `.diff()` SDK
method in kitaru 0.18** (verified — do not assume one). The diff is a manual comparison of the two
records; the ZenML "Replay and improve" guide's own pattern:

```bash
kitaru executions get <fork_exec_id>        # decision, per-checkpoint outputs, cost, latency
kitaru executions get <baseline_rerun_id>   # the control to compare against
```

SDK equivalent — `KitaruClient().executions.get(fork.exec_id)` vs `.get(rerun.exec_id)`, comparing their
fields (cost/latency/decision). Because the Baseline Rerun reproduced the observed baseline, any
difference is attributable to your one change. `decode replay` prints the same hint on stderr
(`kitaru executions get <new> vs <original>`), pointing only at this confirmed surface.

**Cohort: scale the winning change across recent runs** — an **example pattern on the SDK primitives,
NOT a core Kitaru API.** The ZenML "Replay and improve" guide ships it as `run_cohort` (+ `cost` /
`latency` / `quality_judge` metric callables) in the **kitaru examples repo**
(`examples/end_to_end/pydantic_replay_fork`), and states outright it is *"not in the `kitaru` package —
copy or adapt"* (`import kitaru_recipes` is **not** an installed module — verified):

```python
from cohort import run_cohort                 # from the EXAMPLE dir, not `import kitaru`
from utils import cost, latency, quality_judge
# exec_ids: recent runs, e.g. KitaruClient().executions.list(flow="run_agent_task")
report = run_cohort(exec_ids, baseline_model="gemini-2.5-flash",
                    variant_model="gemini-2.5-pro", metrics=[cost, latency, quality_judge])
report.summary()      # per-metric baseline-vs-variant deltas + an is-it-better verdict
report.regressions()  # the metrics / decisions that got worse
```

For each recent run it reproduces the baseline, replays the variant, and scores the pair — so you decide
on a cohort, not a single lucky run.

**Waits re-ask on replay.** A replayed run **re-asks** every `wait()` — Kitaru "does not support
overriding or pre-populating wait results." That is exactly why `decode replay` is **bypass-only** (a
bypass run has no waits to re-ask) and HITL answer-reuse is deferred to a deployed stack
([`tasks/future/hitl-replay-answer-reuse.md`](tasks/future/hitl-replay-answer-reuse.md), ADR-0010 §7). A
HITL exec_id passed to `decode replay` is refused with a friendly pointer to `kitaru executions replay`.
Related honesty note: on a `decode run --hitl` **pause**, Kitaru itself prints a `Waiting for input…`
line to **stdout** (framework behavior) — the pipe-clean guarantee is about the completed **bypass**
answer, not the HITL pause path.

**A subagent run is one opaque checkpoint.** A whole `agent(...)` spawn — the child's entire nested
loop — is one opaque tool call → **one** checkpoint under `"calls"`, so the nested child model calls
are **not** individual replay anchors and a `decode replay --model` swap does **not** reach inside a
child (the child rides the parent's model — `AgentDef` has no model field). A read-only child's cached
summary is replay-safe, so `agent` is never added to the sandbox-bash cache-disable set; and child
token spend stays folded into that one tool call, invisible until Opik lands (M10) — ADR-0013 §9.

**An agent can drive the whole loop.** Kitaru exposes this replay surface over an **MCP server**
(`kitaru-mcp` console script), so a coding agent (Claude Code, Codex, Cursor) can pull a recent run,
propose a change, replay it against the control, compare, and decide whether to widen to a cohort — the
future automation hook (no decode work now).

# Documentation Conventions

- **ADRs** at [`docs/adr/`](docs/adr/) — `NNNN-kebab-title.md`, Nygard template (Status / Context / Decision / Consequences). Every non-obvious architectural choice (which inference backend, the sandbox abstraction, the compaction strategy, choosing Kitaru) ships with one. squid spec: `adr`.
- **Glossary** at [`docs/glossary.md`](docs/glossary.md) — one canonical name per domain concept (Harness, Agent Loop, Priority Gate, Sandbox, Compaction, Subagent…), used identically in code / docs / specs / conversation; update it in the same PR that introduces or renames a concept. squid spec: `ubiquitous-language`.
